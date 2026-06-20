"""Browser Translator - Configuration"""

import os
from pathlib import Path

# Project paths
PROJECT_DIR = Path(__file__).parent.parent
BACKEND_DIR = PROJECT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"

# Ollama
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
TRANSLATION_MODEL = os.environ.get("TRANSLATION_MODEL", "qwen3.5:4b")
# Comma-separated fallback chain: preferred, fallback, ...
TRANSLATION_MODEL_CHAIN = os.environ.get(
    "TRANSLATION_MODEL_CHAIN",
    "qwen3.5:4b,qwen3.5:1.5b,qwen3.5:8b,qwen:4b"
).split(",")

# Moonshine STT
STT_MODEL = os.environ.get("STT_MODEL", "tiny-vi")  # Moonshine tiny-vi model

# Piper TTS
PIPER_VOICE = os.environ.get("PIPER_VOICE", "vi_VN")  # Vietnamese voice

# Audio settings
SAMPLE_RATE = 16000
AUDIO_CHUNK_DURATION = 5  # seconds before processing a chunk (legacy)
SILENCE_THRESHOLD = 0.01  # RMS energy above which audio is treated as voiced
SILENCE_DURATION = 0.8  # seconds of silence to trigger flush (tuned for VN speech)
MIN_FLUSH_DURATION = 1.0  # minimum buffered audio before flush is allowed
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
