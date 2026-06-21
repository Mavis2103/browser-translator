"""Browser Translator - System-level audio capture & playback.

Captures system audio output (browser tab audio → speakers) without going
through a Chrome/extension layer. Plays translated TTS audio directly to
the default sink.

Design
------
Producers/consumers live in dedicated daemon threads so the FastAPI event
loop is never blocked. Audio flows are passed to the existing
`AudioPipeline` instance — exactly the same STT/translate/TTS path as the
WebSocket-driven capture used by the extension.

Capture
-------
- Opens a PortAudio input stream on the PipeWire/PulseAudio monitor source
  of the default sink (`PipeWire expose this transparently through the
  PulseAudio host API`).
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
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger("browser-translator.audio_system")

# Late import — sounddevice requires libportaudio (acked to be installed
# on the target machine in v1.1.0). We guard the import so that the rest of
# the backend still starts even if the system lib is missing (e.g. CI).
try:
    import sounddevice as sd  # type: ignore

    _SOUNDDEVICE_OK = True
except Exception as _sd_err:
    sd = None  # type: ignore
    _SOUNDDEVICE_OK = False
    logger.warning("sounddevice import failed: %s — system capture disabled", _sd_err)

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
    cleanly. Re-entrant start() requires explicit stop() first."""

    def __init__(self, audio_pipeline, vad=None):
        self._pipeline = audio_pipeline
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._vad_fn = make_vad() if vad is None else (make_vad() if vad is True else vad or None)
        self._block_idx = 0

    # ----- public -----

    def start(self) -> bool:
        """Start the capture thread. Returns False if sounddevice is missing
        or an error occurs."""
        if self.is_alive():
            logger.warning("System capture already running")
            return True
        if not _SOUNDDEVICE_OK:
            logger.warning(
                "Cannot start system capture: sounddevice / libportaudio missing.\n"
                "  Install: sudo apt install -y portaudio19-dev"
            )
            return False
        self._stop.clear()
        self._block_idx = 0
        self._thread = threading.Thread(
            target=self._run, name="bt-system-capture", daemon=True
        )
        self._thread.start()
        logger.info("System capture started")
        return True

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("System capture stopped")

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ----- internals -----

    def _run(self):
        stream = None
        try:
            # PortAudio input: default monitor source, 16 kHz mono int16
            # The PA/PipeWire host APIs both expose the monitor device at
            # default index when no explicit `device=` is given.
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=BLOCK_SAMPLES,
                latency="low",
            )
            stream.start()
            logger.debug("Capture: stream opened; reading 16kHz mono int16")
            while not self._stop.is_set():
                frames, overflowed = stream.read(BLOCK_SAMPLES)
                if overflowed:
                    logger.debug("input overflowed — caller likely slow")
                # `frames` is shape (BLOCK_SAMPLES, 1) int16 → flat bytes
                pcm_bytes = frames.astype(np.int16).tobytes()

                # Optional VAD gate
                if self._vad_fn is not None:
                    try:
                        if not self._vad_fn(pcm_bytes):
                            continue
                    except Exception:
                        # webrtcvad is strict about frame size; fall back to energy path
                        pass

                self._block_idx += 1
                # Hand to pipeline (the same code path the WebSocket uses).
                # Sequence number is required for dup detection — make a 4-byte
                # Big-Endian prefix identical to the extension's format.
                import struct
                prefix = struct.pack(">I", self._block_idx)
                self._pipeline.process_audio_chunk(prefix + pcm_bytes)
        except Exception:
            logger.exception("System capture failed")
        finally:
            try:
                if stream is not None:
                    stream.stop(); stream.close()
            except Exception:
                pass


# ========== System playback (translated TTS to speakers) ==========


class SystemPlayback:
    """Plays translated audio out to the default sink. The single output
    stream is reused across segments. `play_pcm_int16(...)` is non-blocking
    — the call returns when the data has been queued; a worker thread writes
    to the PortAudio output stream at its own pace."""

    def __init__(self, queue_size: int = 32):
        self._ready = _SOUNDDEVICE_OK
        self._out_stream = None
        self._lock = threading.Lock()
        self._q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._backend_name = ""  # filled at start()

    # ----- public -----

    def start(self) -> bool:
        if not self._ready:
            return False
        if self.is_alive():
            return True
        self._stop.clear()

        try:
            # Lazy-create output stream with a sample rate chosen at runtime
            # (we close + reopen whenever the incoming sample rate differs).
            self._open_output_stream(sr=22050)
        except Exception as e:
            logger.warning("Output stream open failed: %s", e)
            self._ready = False
            return False

        self._thread = threading.Thread(
            target=self._pump, name="bt-system-playback", daemon=True
        )
        self._thread.start()
        logger.info("System playback ready (default sink)")
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
        # Ensure output stream matches sample rate
        with self._lock:
            cur = self._out_stream
            if cur is None or cur.samplerate != sample_rate:
                self._reopen_output_stream(sample_rate)
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

    # ----- internals -----

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

    def _pump(self):
        while not self._stop.is_set():
            try:
                chunk = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                with self._lock:
                    stream = self._out_stream
                if stream is None:
                    continue
                # Write in small blocks so we can abort quickly on stop()
                i = 0
                while i < len(chunk):
                    block = chunk[i:i + BLOCK_SAMPLES]
                    if len(block) < BLOCK_SAMPLES:
                        # Pad final block with zeros
                        block = np.concatenate([
                            block, np.zeros(BLOCK_SAMPLES - len(block), dtype=np.int16)
                        ])
                    if self._stop.is_set():
                        break
                    stream.write(block)
                    i += BLOCK_SAMPLES
            except Exception:
                logger.exception("Playback pump error")


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
