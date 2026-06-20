"""Audio pipeline: Speech-to-Text (Moonshine) + Text-to-Speech (Moonshine TTS)"""

import logging
from typing import Optional, Callable
import numpy as np

from .config import SAMPLE_RATE, SILENCE_THRESHOLD, SILENCE_DURATION
from .translation import translate

logger = logging.getLogger(__name__)


class AudioPipeline:
    """Handles STT (Moonshine) → Translation → TTS (Moonshine TTS) pipeline."""

    def __init__(self):
        self.stt_model = None
        self.tts_engine = None
        self._loaded = False

        # Audio buffer for accumulating incoming chunks
        self.audio_buffer = bytearray()
        self.buffer_duration = 0  # seconds of audio buffered
        self.last_voice_activity = 0  # timestamp of last voice activity

        # Callbacks
        self.on_transcription: Optional[Callable] = None
        self.on_translation: Optional[Callable] = None
        self.on_tts_audio: Optional[Callable] = None

        # Settings
        self.source_lang = "auto"
        self.target_lang = "vi"

    def load_models(self):
        """Load Moonshine STT and TTS models."""
        if self._loaded:
            return

        logger.info("Loading Moonshine STT model for Vietnamese...")
        try:
            from moonshine_voice.download import get_model_for_language
            from moonshine_voice.transcriber import Transcriber

            # Get Vietnamese model (auto-downloads if needed)
            model_path, model_arch = get_model_for_language('vi')
            logger.info("Moonshine model path: %s (arch: %s)", model_path, model_arch)

            self.stt_model = Transcriber(
                model_path=str(model_path),
                model_arch=model_arch,
            )
            logger.info("Moonshine STT loaded successfully")
        except Exception as e:
            logger.error("Failed to load Moonshine STT: %s", e)
            raise

        logger.info("Loading Moonshine TTS for Vietnamese...")
        try:
            from moonshine_voice.tts import TextToSpeech
            self.tts_engine = TextToSpeech(
                language="vi-vn",
                voice="piper_vi_VN-vais1000-medium",
                download=True,
            )
            logger.info("Moonshine TTS loaded successfully")
        except Exception as e:
            logger.error("Failed to load Moonshine TTS: %s", e)
            logger.warning("TTS will be disabled")

        self._loaded = True

    def process_audio_chunk(self, chunk_data: bytes):
        """Process an incoming audio chunk from WebSocket."""
        # Decode opus/webm to PCM if needed
        pcm_data = self._decode_audio_chunk(chunk_data)
        if pcm_data is None:
            return

        self.audio_buffer.extend(pcm_data)
        duration = len(pcm_data) / (SAMPLE_RATE * 2)  # 16-bit = 2 bytes per sample
        self.buffer_duration += duration

        # Check for voice activity
        audio_array = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
        energy = np.sqrt(np.mean(audio_array ** 2))
        if energy > SILENCE_THRESHOLD:
            self.last_voice_activity = self.buffer_duration

        # Process when we have enough audio
        if self.buffer_duration >= 5.0:  # Every 5 seconds
            self._process_buffer()

    def _decode_audio_chunk(self, chunk_data: bytes) -> Optional[bytes]:
        """Decode webm/opus or raw PCM chunk."""
        try:
            # Check if it's a webm container (starts with 1a45dfa3)
            if len(chunk_data) > 4 and chunk_data[:4] == b'\x1a\x45\xdf\xa3':
                from pydub import AudioSegment
                import io
                audio = AudioSegment.from_file(io.BytesIO(chunk_data), format="webm")
                audio = audio.set_frame_rate(SAMPLE_RATE).set_channels(1).set_sample_width(2)
                return audio.raw_data
            else:
                # Assume raw PCM 16-bit mono
                return chunk_data
        except Exception as e:
            logger.warning("Audio decode failed, treating as PCM: %s", e)
            return chunk_data

    def _process_buffer(self):
        """Transcribe buffered audio, translate it, and generate TTS."""
        if len(self.audio_buffer) < SAMPLE_RATE * 2:  # Need at least 1 second
            return

        if not self._loaded:
            return

        # Convert buffer to numpy array
        audio_array = np.frombuffer(self.audio_buffer, dtype=np.int16).astype(np.float32) / 32768.0

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
            translated = translate(text, self.source_lang, self.target_lang)
            if translated:
                logger.info("Translated: %s", translated[:100])
                if self.on_translation:
                    self.on_translation(text, translated, self.source_lang, self.target_lang)

                # TTS
                if self.tts_engine:
                    self._generate_tts(translated)

        except Exception as e:
            logger.exception("Audio processing error: %s", e)

        # Reset buffer for next segment
        self._reset_buffer()

    def _reset_buffer(self):
        self.audio_buffer = bytearray()
        self.buffer_duration = 0

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

            # Convert float [-1,1] to int16 PCM
            import base64
            pcm_ints = (np.array(samples) * 32767).astype(np.int16)
            pcm_data = pcm_ints.tobytes()
            b64_data = base64.b64encode(pcm_data).decode("ascii")

            if self.on_tts_audio:
                self.on_tts_audio(b64_data, sample_rate)

            logger.debug("TTS generated: %d samples at %dHz", len(samples), sample_rate)
        except Exception as e:
            logger.exception("TTS generation failed: %s", e)

    def set_language(self, source: str, target: str):
        self.source_lang = source
        self.target_lang = target
        logger.info("Language set: %s → %s", source, target)
