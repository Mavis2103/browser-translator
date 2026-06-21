"""Browser Translator - System-level audio capture & playback.

Captures system audio output (browser tab audio → speakers) without going
through a Chrome/extension layer. Plays translated TTS audio directly to
the default sink.

Design
------
Two backends are supported, tried in order:

1. **sounddevice** (preferred) — CFFI binding to PortAudio, works on any
   setup with libportaudio (ALSA, PipeWire, PulseAudio, JACK, etc.).
2. **parec/paplay** (fallback) — PulseAudio CLI tools, zero Python native
   deps. Works on PipeWire-pulse and PulseAudio systems.

Capture
-------
- Opens a capture stream on the PipeWire/PulseAudio monitor source of the
  default sink.
- Force 16 kHz mono int16 directly — PipeWire resamples on the way in, so
  the JS extension's brittle JSON-format conversion is bypassed.
- 100 ms blocks (1600 frames) → low end-to-end latency.
- Optional VAD (webrtcvad) to gate STT — falls back to energy-based silence
  detection (already implemented in `audio_pipeline.py`).

Playback
--------
TTS produces base64-encoded PCM Int16 LE; we decode and stream blocks to
the default output. A small ring buffer keeps writes non-blocking.
"""
from __future__ import annotations

import base64
import logging
import logging.config
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger("browser-translator.audio_system")

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

# 1) sounddevice (CFFI → libportaudio)
try:
    import sounddevice as sd  # type: ignore
    _SOUNDDEVICE_OK = True
except Exception as _sd_err:
    sd = None  # type: ignore
    _SOUNDDEVICE_OK = False
    logger.debug("sounddevice unavailable: %s", _sd_err)

# 2) PulseAudio CLI tools (fallback — zero native deps)
_HAS_PAREC = shutil.which("parec") is not None
_HAS_PAPLAY = shutil.which("paplay") is not None
_HAS_PULSE_UTILS = _HAS_PAREC and _HAS_PAPLAY
if not _HAS_PULSE_UTILS:
    logger.debug("parec/paplay unavailable — pulse-utils fallback disabled")

# 3) Optional VAD
try:
    import webrtcvad  # type: ignore
    _WEBRTCVAD_OK = True
except Exception:
    webrtcvad = None  # type: ignore
    _WEBRTCVAD_OK = False
    logger.debug("webrtcvad not available — falling back to energy-based VAD")

# Reuse project constants
SAMPLE_RATE = 16000          # Moonshine STT native
BLOCK_SAMPLES = 1600         # 100 ms at 16 kHz
CHANNELS = 1
DTYPE = "int16"


# ========== Backend auto-detect ==========

@dataclass
class AudioBackend:
    """What audio stack is actually running. Discovery is best-effort:
    `pactl info` reads PulseAudio API metadata (works under PipeWire-pulse
    compat layer and on native PulseAudio)."""
    name: str               # 'pulse' | 'pipewire-pulse' | 'pipewire' | 'alsa' | 'unknown'
    server_version: str = ""
    default_sink: str = ""
    monitor_source: str = ""


def detect_backend() -> AudioBackend:
    """Try to detect the audio stack in use. Cheap; only invoked once at
    startup. Returns a populated `AudioBackend` (always; falls back to
    `'unknown'` on probe failure)."""
    backend = AudioBackend(name="unknown")
    try:
        if shutil.which("pactl"):
            r = subprocess.run(
                ["pactl", "info"], capture_output=True, text=True, timeout=3
            )
            if r.returncode == 0:
                out = r.stdout
                for line in out.splitlines():
                    if line.startswith("Server Name:"):
                        v = line.split(":", 1)[1].strip()
                        backend.server_version = v
                        lname = v.lower()
                        if "pipewire" in lname and "pulseaudio" in lname:
                            backend.name = "pipewire-pulse"
                        elif "pipewire" in lname:
                            backend.name = "pipewire"
                        elif "pulseaudio" in lname:
                            backend.name = "pulse"
                    elif line.startswith("Default Sink:"):
                        backend.default_sink = line.split(":", 1)[1].strip()
    except Exception as e:
        logger.debug("detect_backend failed: %s", e)

    if not backend.monitor_source and backend.default_sink:
        # Standard PipeWire/PulseAudio convention: monitor source = sink + ".monitor"
        backend.monitor_source = f"{backend.default_sink}.monitor"

    logger.info(
        "Audio backend: name=%s version=%s sink=%s monitor=%s",
        backend.name, backend.server_version, backend.default_sink, backend.monitor_source,
    )
    return backend


# ========== Optional VAD (webrtcvad) ==========


def make_vad(aggressiveness: int = 2):
    """Build a VAD wrapper. Returns a callable (frame_bytes) -> bool,
    where frame must be a multiple of 16/32 kHz frame size in 16-bit LE.
    Returns True when speech is detected in the frame.

    Falls back to None (caller uses pipeline-level energy VAD) if
    webrtcvad is missing or audio_params are wrong."""
    if not _WEBRTCVAD_OK:
        return None
    try:
        vad = webrtcvad.Vad(aggressiveness)
        return lambda frame_bytes: vad.is_speech(frame_bytes, SAMPLE_RATE)
    except Exception as e:
        logger.debug("Failed to construct VAD: %s", e)
        return None


# ========== System capture ==========


class SystemCapture:
    """Background thread that opens the system monitor source and feeds raw
    16-bit/16 kHz/mono PCM frames into the AudioPipeline.

    Designed for graceful shutdown: `stop()` waits for the stream to close
    cleanly. Re-entrant start() requires explicit stop() first.

    Backends (tried in order):
      1. sounddevice (PortAudio) — when libportaudio.so is available.
      2. parec subprocess (PulseAudio/pipewire-pulse) — zero native deps.
    """

    def __init__(self, audio_pipeline, vad=None):
        self._pipeline = audio_pipeline
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._vad_fn = make_vad() if vad is None else (make_vad() if vad is True else vad or None)
        self._block_idx = 0
        self._backend = "none"

    # ----- public -----

    def start(self) -> bool:
        """Start the capture thread. Returns False if no audio backend is
        available."""
        if self.is_alive():
            logger.debug("System capture already running")
            return True
        if not _SOUNDDEVICE_OK and not _HAS_PULSE_UTILS:
            logger.warning(
                "Cannot start system capture: no audio backend available.\n"
                "  Options:\n"
                "    sudo apt install -y portaudio19-dev   (for sounddevice)\n"
                "    sudo apt install -y pulseaudio-utils   (for parec fallback)"
            )
            return False
        self._stop.clear()
        self._block_idx = 0
        self._thread = threading.Thread(
            target=self._run if _SOUNDDEVICE_OK else self._run_parec,
            name="bt-system-capture",
            daemon=True,
        )
        self._thread.start()
        self._backend = "sounddevice" if _SOUNDDEVICE_OK else "parec"
        logger.info("System capture started (backend: %s)", self._backend)
        return True

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("System capture stopped")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def backend(self) -> str:
        return self._backend

    # ----- internals: sounddevice -----

    def _run(self):
        stream = None
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCK_SAMPLES,
                latency="low",
            )
            stream.start()
            logger.debug("Capture: PortAudio stream opened")
            while not self._stop.is_set():
                frames, overflowed = stream.read(BLOCK_SAMPLES)
                if overflowed:
                    logger.debug("input overflowed — caller likely slow")
                pcm_bytes = frames.astype(np.int16).tobytes()
                if self._vad_fn is not None:
                    try:
                        if not self._vad_fn(pcm_bytes):
                            continue
                    except Exception:
                        pass
                self._emit(pcm_bytes)
        except Exception:
            logger.exception("System capture (sounddevice) failed")
        finally:
            try:
                if stream is not None:
                    stream.stop(); stream.close()
            except Exception:
                pass

    # ----- internals: parec (PulseAudio/pipewire-pulse) -----

    def _run_parec(self):
        """Capture loop via ``parec`` subprocess — zero native Python deps."""
        _backend = get_backend()
        monitor = _backend.monitor_source
        if not monitor:
            logger.error("No monitor source found — cannot start parec capture")
            return

        try:
            self._proc = subprocess.Popen(
                [
                    "parec",
                    "-d", monitor,
                    "--raw",
                    "--format=s16le",
                    f"--rate={SAMPLE_RATE}",
                    f"--channels={CHANNELS}",
                    "--latency-msec=100",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            assert self._proc.stdout is not None
            logger.debug("Capture: parec started (monitor=%s)", monitor)

            block_size = BLOCK_SAMPLES * 2  # 3200 bytes = 1600 × int16
            while not self._stop.is_set():
                chunk = self._proc.stdout.read(block_size)
                if not chunk or len(chunk) < block_size:
                    if self._stop.is_set():
                        break
                    logger.warning("parec returned short read (%d bytes)", len(chunk) if chunk else 0)
                    time.sleep(0.05)
                    continue
                if self._vad_fn is not None:
                    try:
                        if not self._vad_fn(chunk):
                            continue
                    except Exception:
                        pass
                self._emit(chunk)

        except Exception:
            logger.exception("System capture (parec) failed")
        finally:
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=2)
                except Exception:
                    pass
                self._proc = None

    # ----- shared emit -----

    def _emit(self, pcm_bytes: bytes):
        self._block_idx += 1
        import struct
        prefix = struct.pack(">I", self._block_idx)
        self._pipeline.process_audio_chunk(prefix + pcm_bytes)


# ========== System playback (translated TTS to speakers) ==========


class SystemPlayback:
    """Plays translated audio out to the default sink. The single output
    stream is reused across segments. `play_pcm_int16(...)` is non-blocking
    — the call returns when the data has been queued; a worker thread writes
    to the PortAudio output stream at its own pace.

    Backends (tried in order):
      1. sounddevice (PortAudio) — when libportaudio.so is available.
      2. paplay subprocess (PulseAudio/pipewire-pulse) — zero native deps.
    """

    def __init__(self, queue_size: int = 32):
        self._use_sd = _SOUNDDEVICE_OK
        self._use_pa = not _SOUNDDEVICE_OK and _HAS_PULSE_UTILS
        self._ready = _SOUNDDEVICE_OK or _HAS_PULSE_UTILS
        self._out_stream = None
        self._pa_proc: Optional[subprocess.Popen] = None
        self._pa_rate = 0  # current paplay sample rate
        self._lock = threading.Lock()
        self._q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._backend_name = "sounddevice" if _SOUNDDEVICE_OK else ("paplay" if _HAS_PULSE_UTILS else "none")

    # ----- public -----

    def start(self) -> bool:
        if not self._ready:
            return False
        if self.is_alive():
            return True
        self._stop.clear()

        if self._use_sd:
            try:
                self._open_output_stream(sr=22050)
            except Exception as e:
                logger.warning("Output stream open failed: %s", e)
                self._ready = False
                return False

        # paplay subprocess started lazily on first chunk (so we know sample rate)

        self._thread = threading.Thread(
            target=self._pump,
            name="bt-system-playback",
            daemon=True,
        )
        self._thread.start()
        logger.info("System playback ready (backend: %s)", self._backend_name)
        return True

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        with self._lock:
            if self._out_stream is not None:
                try: self._out_stream.stop(); self._out_stream.close()
                except Exception: pass
                self._out_stream = None
            self._stop_paplay()
        logger.info("System playback stopped")

    def is_alive(self) -> bool:
        return self._ready and self._thread is not None and self._thread.is_alive()

    def play_pcm_int16(self, samples: np.ndarray, sample_rate: int):
        """Queue an Int16 mono PCM segment for playback."""
        if not self.is_alive():
            logger.warning("play_pcm_int16 dropped: playback not active")
            return
        if samples.ndim != 1:
            samples = samples.reshape(-1)

        if self._use_sd:
            # Ensure output stream matches sample rate
            with self._lock:
                cur = self._out_stream
                if cur is None or cur.samplerate != sample_rate:
                    self._reopen_output_stream(sample_rate)
        elif self._use_pa:
            # Ensure paplay is running at the right sample rate
            with self._lock:
                if self._pa_proc is None or self._pa_rate != sample_rate:
                    self._start_paplay(sample_rate)

        try:
            self._q.put_nowait(samples.astype(np.int16, copy=False).copy())
        except queue.Full:
            logger.warning("playback queue full — dropping chunk")

    def play_base64_pcm(self, b64_data: str, sample_rate: int = 22050):
        """Convenience: decode base64 → int16 numpy → enqueue for playback."""
        try:
            raw = base64.b64decode(b64_data)
            arr = np.frombuffer(raw, dtype=np.int16)
            self.play_pcm_int16(arr, sample_rate)
        except Exception as e:
            logger.warning("play_base64_pcm decode failed: %s", e)

    # ----- internals: sounddevice -----

    def _open_output_stream(self, sr: int):
        with self._lock:
            if self._out_stream is not None:
                try: self._out_stream.close()
                except Exception: pass
            self._out_stream = sd.OutputStream(
                samplerate=sr,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCK_SAMPLES,
                latency="low",
            )
            self._out_stream.start()

    def _reopen_output_stream(self, sr: int):
        # Caller holds _lock
        self._open_output_stream(sr)

    # ----- internals: paplay -----

    def _start_paplay(self, sr: int):
        """Start (or restart) paplay at a given sample rate. Caller holds _lock."""
        self._stop_paplay()
        self._pa_proc = subprocess.Popen(
            [
                "paplay",
                "--raw",
                f"--rate={sr}",
                "--format=s16le",
                "--channels=1",
                "--latency-msec=100",
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._pa_rate = sr
        logger.debug("paplay started at %d Hz", sr)

    def _stop_paplay(self):
        if self._pa_proc is not None:
            try:
                self._pa_proc.stdin.close()
            except Exception:
                pass
            try:
                self._pa_proc.terminate()
                self._pa_proc.wait(timeout=2)
            except Exception:
                try:
                    self._pa_proc.kill()
                except Exception:
                    pass
            self._pa_proc = None
            self._pa_rate = 0

    # ----- shared pump -----

    def _pump(self):
        while not self._stop.is_set():
            try:
                chunk = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if self._use_sd:
                    self._pump_sounddevice(chunk)
                elif self._use_pa:
                    self._pump_paplay(chunk)
            except Exception:
                logger.exception("Playback pump error")

    def _pump_sounddevice(self, chunk: np.ndarray):
        with self._lock:
            stream = self._out_stream
        if stream is None:
            return
        i = 0
        while i < len(chunk):
            block = chunk[i:i + BLOCK_SAMPLES]
            if len(block) < BLOCK_SAMPLES:
                block = np.concatenate([
                    block, np.zeros(BLOCK_SAMPLES - len(block), dtype=np.int16)
                ])
            if self._stop.is_set():
                break
            stream.write(block)
            i += BLOCK_SAMPLES

    def _pump_paplay(self, chunk: np.ndarray):
        with self._lock:
            proc = self._pa_proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(chunk.tobytes())
            proc.stdin.flush()
        except (BrokenPipeError, OSError):
            logger.warning("paplay pipe broken — stopping playback")
            self._ready = False


# ========== Module-level singletons ==========

_capture: Optional[SystemCapture] = None
_playback: Optional[SystemPlayback] = None
_backend: Optional[AudioBackend] = None


def get_backend() -> AudioBackend:
    global _backend
    if _backend is None:
        _backend = detect_backend()
    return _backend


def start_system_audio(pipeline) -> bool:
    """Wire up audio capture & playback to the AudioPipeline. Returns
    True if at least capture started."""
    global _capture, _playback
    backend = get_backend()

    if _playback is None:
        _playback = SystemPlayback()
    _playback.start()

    if _capture is None:
        _capture = SystemCapture(pipeline, vad=True)
    capture_ok = _capture.start()

    # Route TTS to system playback
    if capture_ok and _playback.is_alive():
        pipeline.on_tts_audio = lambda b64, sr: _playback.play_base64_pcm(b64, sr)

    # Optional transcription/translation log via backend logger so journalctl
    # has the data — easier than side-channel to the popup.
    pipeline.on_transcription = lambda text: logger.info("[system-capture] transcribed: %s", text[:100])
    if pipeline.on_translation is None or pipeline.on_translation.__name__ == "on_translation":
        # leave whatever was wired by main.py; don't clobber the WS client callback
        pass
    return capture_ok


def stop_system_audio():
    global _capture, _playback
    if _capture is not None:
        _capture.stop()
        _capture = None
    if _playback is not None:
        _playback.stop()
        _playback = None
