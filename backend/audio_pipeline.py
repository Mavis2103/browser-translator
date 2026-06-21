"""Audio pipeline: Speech-to-Text (Moonshine) + Text-to-Speech (Moonshine TTS)"""

import logging
from typing import Optional, Callable
import numpy as np

from .config import (
    SAMPLE_RATE,
    SILENCE_THRESHOLD,
    SILENCE_DURATION,
    MIN_FLUSH_DURATION,
    MAX_BUFFER_DURATION,
)
from .translation import translate

logger = logging.getLogger(__name__)


class AudioPipeline:
    """Handles STT (Moonshine) → Translation → TTS (Moonshine TTS) pipeline."""

    def __init__(self):
        self.stt_model = None
        self.tts_engine = None
        self._loaded = False
        self.translation_model = "qwen3.5:0.8b"  # default (small/fast); may be overridden via set_language
        # Buffer / silence tracking — flush on natural pause rather than fixed window.
        self.audio_buffer = bytearray()
        self.buffer_duration = 0  # seconds of audio buffered
        self.last_voice_activity = 0  # running duration when voice energy last crossed threshold
        self.silence_start: Optional[float] = None  # running duration when silence began
        self.flushing = False  # guard against re-entrant flush
        self.last_seq = 0  # last audio chunk sequence number for dedup

        # Callbacks
        self.on_transcription: Optional[Callable] = None
        self.on_translation: Optional[Callable] = None
        self.on_tts_audio: Optional[Callable] = None

        # Settings
        self.source_lang = "auto"
        self.target_lang = "vi"
        self.translation_model = "qwen3.5:0.8b"  # may be overridden via set_language

    def load_models(self):
        """Load Moonshine STT and TTS models concurrently.

        STT + TTS live in the same library but load weight files independently,
        so we run them on two threads to cut the total cold-load time roughly
        in half (~5s → ~2-3s on a typical SSD).
        """
        if self._loaded:
            return

        import threading

        logger.info("Loading Moonshine STT + TTS concurrently...")

        # moonshine_voice touches disk via download() in both code paths.
        # A shared lock prevents races when both threads first-touch the same asset.
        download_lock = threading.Lock()
        _errors: dict = {}

        def _load_stt_thread():
            try:
                from moonshine_voice.download import get_model_for_language
                from moonshine_voice.transcriber import Transcriber
                with download_lock:
                    model_path, model_arch = get_model_for_language('vi')
                logger.info("Moonshine STT path: %s (arch: %s)", model_path, model_arch)
                self.stt_model = Transcriber(
                    model_path=str(model_path),
                    model_arch=model_arch,
                )
                logger.info("Moonshine STT loaded successfully")
            except Exception as e:
                _errors["stt"] = e
                logger.error("Failed to load Moonshine STT: %s", e)

        def _load_tts_thread():
            try:
                from moonshine_voice.tts import TextToSpeech
                with download_lock:
                    self.tts_engine = TextToSpeech(
                        language="vi-vn",
                        voice="piper_vi_VN-vais1000-medium",
                        download=True,
                    )
                logger.info("Moonshine TTS loaded successfully")
            except Exception as e:
                _errors["tts"] = e
                logger.error("Failed to load Moonshine TTS: %s", e)
                logger.warning("TTS will be disabled")

        stt_thread = threading.Thread(target=_load_stt_thread, name="load-stt", daemon=True)
        tts_thread = threading.Thread(target=_load_tts_thread, name="load-tts", daemon=True)
        stt_thread.start()
        tts_thread.start()
        stt_thread.join()
        tts_thread.join()

        # STT failure is fatal — the audio pipeline cannot work without it
        if "stt" in _errors:
            raise _errors["stt"]

        self._loaded = True
        loaded = ["STT" if self.stt_model else None, "TTS" if self.tts_engine else None]
        logger.info("Moonshine pipeline ready: %s", ", ".join(x for x in loaded if x))

    def process_audio_chunk(self, chunk_data: bytes):
        """Process an incoming audio chunk from WebSocket.

        Chunk format (from extension): 4-byte big-endian sequence number + payload.
        Duplicate or out-of-order chunks (seq ≤ last_seq) are dropped silently.
        """
        if self.flushing:
            return  # drop while a flush is in progress

        # Strip 4-byte big-endian sequence prefix
        if len(chunk_data) >= 4:
            import struct
            seq = struct.unpack(">I", chunk_data[:4])[0]
            payload = chunk_data[4:]
            if seq <= self.last_seq:
                logger.debug("Dropping duplicate audio chunk seq=%d (last=%d)", seq, self.last_seq)
                return
            self.last_seq = seq
        else:
            payload = chunk_data  # no prefix — legacy/raw PCM

        # Decode webm/opus to PCM if needed
        pcm_data = self._decode_audio_chunk(payload)
        if pcm_data is None:
            return

        self.audio_buffer.extend(pcm_data)
        duration = len(pcm_data) / (SAMPLE_RATE * 2)  # 16-bit = 2 bytes per sample
        self.buffer_duration += duration

        # Detect voice activity via short-time energy
        audio_array = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
        if audio_array.size:
            energy = float(np.sqrt(np.mean(audio_array ** 2)))
        else:
            energy = 0.0

        if energy > SILENCE_THRESHOLD:
            self.last_voice_activity = self.buffer_duration
            self.silence_start = None
        elif self.silence_start is None:
            self.silence_start = self.buffer_duration

        # Flush triggers (evaluated in priority order):
        #  A) Max buffer reached (cheap hard cap, prevents unbounded growth)
        #  B) Natural pause: SILENCE_DURATION of silence after at least one voice event
        #  C) Min viable window: at least MIN_FLUSH_DURATION buffered
        if self.buffer_duration >= MAX_BUFFER_DURATION:
            self._process_buffer()
        elif (
            self.silence_start is not None
            and self.last_voice_activity > 0
            and (self.buffer_duration - self.silence_start) >= SILENCE_DURATION
            and self.buffer_duration >= MIN_FLUSH_DURATION
        ):
            self._process_buffer()

    def _decode_audio_chunk(self, chunk_data: bytes) -> Optional[bytes]:
        """Decode webm/opus or raw PCM chunk.

        Current protocol (v1.0.9+): the extension streams raw PCM16 LE 16kHz
        mono from offscreen.js — never WebM. WebM/Opus decoding is kept as a
        defensive fallback for legacy clients, but the pydub path is broken on
        Python 3.13 (`pyaudioop` removed). We bail out of the pydub path
        cleanly rather than crashing the backend with
        ``No module named 'pyaudioop'``.
        """
        # WebM/Opus magic — only enter pydub path if pydub is importable
        if len(chunk_data) > 4 and chunk_data[:4] == b'\x1a\x45\xdf\xa3':
            try:
                from pydub import AudioSegment  # noqa: F401  (probes availability)
                import io
                audio = AudioSegment.from_file(io.BytesIO(chunk_data), format="webm")
                audio = audio.set_frame_rate(SAMPLE_RATE).set_channels(1).set_sample_width(2)
                return audio.raw_data
            except Exception as e:
                logger.warning("WebM decode skipped (%s); expecting raw PCM16 from extension", e)
                # Fall through and treat as raw PCM (will likely fail int16 alignment
                # check if it really is WebM — that's the client's bug to fix).
                return None
        # Assume raw PCM 16-bit mono (current offscreen.js path).
        # Validate byte alignment so np.frombuffer(..., dtype=int16) won't crash.
        if len(chunk_data) % 2 != 0:
            logger.warning("Dropping misaligned PCM chunk (len=%d, not even)", len(chunk_data))
            return None
        return chunk_data

    def _process_buffer(self):
        """Transcribe buffered audio, translate it, and generate TTS."""
        if self.flushing:
            return
        if len(self.audio_buffer) < SAMPLE_RATE * 2:  # Need at least 1 second
            return

        if not self._loaded:
            return

        # Guard: drop frames with no detected speech (no voice activity recorded)
        if self.last_voice_activity <= 0 or self.last_voice_activity >= self.buffer_duration:
            # No spoken content in the buffered window — keep waiting
            return

        # Convert buffer to numpy array
        audio_array = np.frombuffer(self.audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0

        self.flushing = True
        try:
            # STT
            logger.debug("Transcribing %d samples...", len(audio_array))
            transcript = self.stt_model.transcribe_without_streaming(
                audio_array.tolist(), sample_rate=SAMPLE_RATE
            )
            text = str(transcript).strip()
            if not text or text.isspace():
                text = ""
                for line in transcript.lines:
                    if line.text.strip():
                        text += line.text.strip() + " "
                text = text.strip()

            if not text:
                logger.debug("No speech detected")
                self._reset_buffer()
                return

            logger.info("Transcribed: %s", text[:100])
            if self.on_transcription:
                self.on_transcription(text)

            # Translate
            translated = translate(text, self.source_lang, self.target_lang, model=self.translation_model)
            if translated:
                logger.info("Translated: %s", translated[:100])
                if self.on_translation:
                    self.on_translation(text, translated, self.source_lang, self.target_lang)

                # TTS
                if self.tts_engine:
                    self._generate_tts(translated)

        except Exception as e:
            logger.exception("Audio processing error: %s", e)
        finally:
            self.flushing = False

        # Reset buffer for next segment
        self._reset_buffer()

    def _reset_buffer(self):
        self.audio_buffer = bytearray()
        self.buffer_duration = 0
        self.last_voice_activity = 0
        self.silence_start = None
        self.last_seq = 0

    def _generate_tts(self, text: str):
        """Generate TTS audio from translated text."""
        if not self.tts_engine:
            logger.debug("TTS engine not available")
            return

        try:
            # Synthesize using moonshine_voice TTS -> PCM float samples
            samples, sample_rate = self.tts_engine.synthesize(text)

            if not samples:
                logger.debug("TTS produced no samples")
                return

            # Convert float [-1,1] to int16 PCM, boosting volume ~1.6x
            import base64
            samples_arr = np.array(samples, dtype=np.float64)
            # Normalize peak to 0.99 then amplify to fill int16 range
            peak = float(np.max(np.abs(samples_arr)))
            norm_target = 0.95  # leave headroom to avoid clipping
            scale = norm_target / max(peak, 0.001)  # 0.001 prevents div-by-zero
            pcm_ints = (samples_arr * 32767 * min(scale, 2.0)).astype(np.int16)
            pcm_ints = np.clip(pcm_ints, -32768, 32767).astype(np.int16)
            pcm_data = pcm_ints.tobytes()
            b64_data = base64.b64encode(pcm_data).decode("ascii")

            if self.on_tts_audio:
                self.on_tts_audio(b64_data, sample_rate)

            logger.debug("TTS generated: %d samples at %dHz", len(samples), sample_rate)
        except Exception as e:
            logger.exception("TTS generation failed: %s", e)

    def set_language(self, source: str, target: str, model: str = None):
        self.source_lang = source
        self.target_lang = target
        if model:
            self.translation_model = model
        logger.info("Language set: %s → %s (model: %s)", source, target, self.translation_model)
