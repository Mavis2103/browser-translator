# Browser Translator

Local AI-powered tool for **speech-to-speech translation** and **OCR page translation** in the browser. Runs entirely on CPU — no GPU needed.

## Architecture

```
┌──────────────────────────────────────┐
│  Chrome Extension (MV3)              │
│  ┌──────────┐  ┌────────────────┐    │
│  │tabCapture│  │  Content Script│    │
│  │→ audio   │  │  → overlays    │    │
│  └────┬─────┘  └──────┬─────────┘    │
│       │               │              │
│   WebSocket           CDP/HTTP       │
└───────┼───────────────┼──────────────┘
        │               │
┌───────┼───────────────┼──────────────┐
│  Python Backend (FastAPI :8765)      │
│  ┌────────────┐  ┌──────────────┐    │
│  │ Audio Pipe │  │  OCR Pipe    │    │
│  │ Moonshine  │  │  PaddleOCR   │    │
│  │ → Qwen3.5  │  │  → Qwen3.5   │    │
│  │ → Piper    │  │  → response  │    │
│  └────────────┘  └──────────────┘    │
│        │                             │
│  Ollama (Qwen3.5 4B) ← localhost     │
└──────────────────────────────────────┘
```

## Features

- **🎤 Speech-to-Speech Translation** — Captures tab audio via `chrome.tabCapture`, transcribes with Moonshine tiny-vi, translates with Qwen3.5, and speaks back with Piper TTS (Vietnamese voice)
- **📄 OCR Page Translation** — Takes full-page screenshots via CDP, extracts text with PaddleOCR PP-OCRv6, translates via Qwen3.5
- **🔒 100% Local** — No data leaves your machine
- **🌐 Multi-language** — Vietnamese, English, Japanese, Korean, Chinese, Thai, Indonesian

## Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| RAM | 8GB (with Qwen3.5 4B) | 16GB |
| CPU | Any x86_64 | 4+ cores |
| OS | Linux | Ubuntu 24.04+ |
| Browser | Google Chrome 120+ | Chrome 149+ |
| Disk | 8GB free | 15GB+ |

## Quick Start

### 1. First-time Install (run once)

```bash
git clone https://github.com/Mavis2103/browser-translator.git
cd browser-translator
./scripts/install.sh
```

This installs:
- System packages: `ffmpeg`, `zstd`, `curl`
- Python deps via `uv`
- Ollama (auto-downloads if missing) at `~/.local/bin/ollama`
- `qwen3.5:4b` translation model (~3.4GB)

### 2. Run

```bash
# Open a separate terminal for Ollama (if install.sh didn't start it):
OLLAMA_LIBRARY_PATH=$HOME/.local/lib/ollama ollama serve

# Start the tool:
./scripts/start.sh

# To stop:
./scripts/stop.sh
```

The script auto-launches Chrome with the extension loaded. Open the 🌐 icon from the toolbar.

## Manual Start

```bash
# Terminal 1: Ollama
ollama serve

# Terminal 2: Python backend
uvicorn backend.main:app --host 0.0.0.0 --port 8765

# Terminal 3: Chrome with extension
google-chrome --remote-debugging-port=9222 --load-extension=./extension
```

## Project Structure

```
browser-translator/
├── extension/              # Chrome Extension (MV3)
│   ├── manifest.json       # Extension manifest
│   ├── background.js       # Service worker (tabCapture, WebSocket)
│   ├── popup.html          # Control panel
│   ├── popup.js            # Popup logic
│   ├── content.js          # Content script (overlays, toasts)
│   ├── content.css         # Overlay styles
│   ├── styles.css          # Popup styles
│   └── icons/              # Extension icons
├── backend/                # Python Backend
│   ├── main.py             # FastAPI server (WebSocket + HTTP)
│   ├── audio_pipeline.py   # STT → Translation → TTS pipeline
│   ├── ocr_pipeline.py     # Screenshot → OCR → Translation
│   ├── translation.py      # Ollama translation client
│   └── config.py           # Configuration
├── scripts/
│   ├── start.sh            # Start everything
│   └── launch_chrome.sh    # Launch Chrome with extension
├── requirements.txt
├── README.md
└── CHANGELOG.md
```

## Technical Notes

- **Audio**: Chrome's `tabCapture` API captures only the audio from the active tab. For system-wide audio, use PipeWire monitor source instead.
- **STT**: Moonshine tiny-vi (26MB model) handles Vietnamese speech recognition on CPU in realtime.
- **TTS**: Piper with vi_VN voice generates natural Vietnamese speech.
- **OCR**: PaddleOCR PP-OCRv6 tiny (3MB) provides fast CPU inference via OpenVINO.
- **Translation**: Qwen3.5 4B runs locally via Ollama at ~2-5s per translation on i5 CPU.

## License

MIT
