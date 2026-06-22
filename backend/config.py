"""Browser Translator - Configuration"""

import os
from pathlib import Path

# Project paths
PROJECT_DIR = Path(__file__).parent.parent
BACKEND_DIR = PROJECT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"

# Ollama
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
TRANSLATION_MODEL = os.environ.get("TRANSLATION_MODEL", "qwen3.5:0.8b")
# Model chain: primary → fallback → ... (tried in order, first to respond wins)
TRANSLATION_MODEL_CHAIN = os.environ.get(
    "TRANSLATION_MODEL_CHAIN",
    "qwen3.5:0.8b,qwen3.5:1.5b"
).split(",")

# Translation engine: "nllb" (default, auto-downloads model) or "ollama"
TRANSLATION_ENGINE = os.environ.get("TRANSLATION_ENGINE", "nllb")

# NLLB model path (CTranslate2 format, can be HF model name or local dir)
NLLB_MODEL_PATH = os.environ.get(
    "NLLB_MODEL_PATH",
    str(Path.home() / ".cache" / "browser-translator" / "nllb-600m-ct2-int8")
)
# NLLB source/target language codes (FLORES-200 codes)
NLLB_SRC_LANG = os.environ.get("NLLB_SRC_LANG", "eng_Latn")
NLLB_TGT_LANG = os.environ.get("NLLB_TGT_LANG", "vie_Latn")

# faster-whisper STT
STT_MODEL_SIZE = os.environ.get("STT_MODEL_SIZE", "base")  # tiny, base, small, medium, large-v3
STT_COMPUTE_TYPE = os.environ.get("STT_COMPUTE_TYPE", "int8")  # int8, int8_float16, float16, float32

# Piper TTS (via moonshine-voice, optional — only used with --system flag)
PIPER_VOICE = os.environ.get("PIPER_VOICE", "vi_VN")  # Vietnamese voice

# Audio settings
SAMPLE_RATE = 16000
AUDIO_CHUNK_DURATION = 5  # seconds before processing a chunk (legacy)
SILENCE_THRESHOLD = 0.005  # RMS energy above which audio is treated as voiced (faster-whisper has own VAD, this is just for flush gating)
SILENCE_DURATION = 0.5  # seconds of silence to trigger flush (tuned for faster flush cycles)
MIN_FLUSH_DURATION = 0.5  # minimum buffered audio before flush is allowed
MAX_BUFFER_DURATION = 30.0  # hard cap to avoid unbounded buffering
SEQUENCE_RESET_ON_FLUSH = True  # reset voice-activity timeline after flush

# OCR settings
PADDLE_OCR_LANG = os.environ.get("PADDLE_OCR_LANG", "vi")
FULL_PAGE_SCREENSHOT = True

# CDP / Browser
CDP_URL = os.environ.get("CDP_URL", "http://localhost:9222")

# Server
SERVER_HOST = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8765"))

# Logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Ensure data dir exists
DATA_DIR.mkdir(parents=True, exist_ok=True)
