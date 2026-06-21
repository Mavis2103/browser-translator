"""STT engine — faster-whisper for local speech-to-text.

Replaces Moonshine STT completely. Supports multi-language
(English + Vietnamese mixed audio) with INT8 CPU quantization.
"""

import logging
from typing import Optional

import numpy as np

from .config import STT_MODEL_SIZE, STT_COMPUTE_TYPE, SAMPLE_RATE

logger = logging.getLogger(__name__)


class FasterWhisperEngine:
    """Local speech-to-text using faster-whisper with INT8 quantization.

    Handles mixed English/Vietnamese audio natively (multilingual model).
    """

    def __init__(
        self,
        model_size: str = STT_MODEL_SIZE,
        device: str = "cpu",
        compute_type: str = STT_COMPUTE_TYPE,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.model = None
        self._loaded = False

    def load(self):
        """Load the faster-whisper model (downloads on first call)."""
        from faster_whisper import WhisperModel

        logger.info(
            "Loading faster-whisper %s (device=%s, compute=%s)...",
            self.model_size, self.device, self.compute_type,
        )
        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            download_root=None,  # default cache ~/.cache/faster-whisper/
        )
        self._loaded = True
        logger.info("faster-whisper %s loaded successfully", self.model_size)

    def transcribe(
        self,
        audio_array: np.ndarray,
        sample_rate: int = SAMPLE_RATE,
    ) -> str:
        """Transcribe PCM float32 audio to text.

        Args:
            audio_array: 1-D float32 array, values in [-1, 1].
            sample_rate: Audio sample rate (default 16000).

        Returns:
            Transcribed text, or empty string if no speech detected.
        """
        if not self._loaded or self.model is None:
            raise RuntimeError("STT model not loaded")

        segments, info = self.model.transcribe(
            audio_array,
            beam_size=5,
            language=None,          # auto-detect (handles mixed EN/VN)
            vad_filter=True,        # Voice Activity Detection — skip silence
            vad_parameters=dict(
                threshold=0.5,
                min_speech_duration_ms=250,
                max_speech_duration_s=30,
                min_silence_duration_ms=100,
            ),
            condition_on_previous_text=False,
        )

        text_parts = []
        for segment in segments:
            t = (segment.text or "").strip()
            if t:
                text_parts.append(t)

        return " ".join(text_parts)

    @property
    def is_loaded(self) -> bool:
        return self._loaded
