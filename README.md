# Browser Translator

Local AI-powered tool for **speech-to-speech translation** and **OCR page translation** in the browser. Runs entirely on CPU — no GPU needed.

## Features

- **🎤 Speech-to-Speech Translation** — Captures tab audio via `chrome.tabCapture`, transcribes with Moonshine tiny-vi, translates with Qwen3.5, speaks back with Piper TTS
- **📄 OCR Page Translation** — Captures page screenshots, extracts text with PaddleOCR PP-OCRv6, translates via Qwen3.5
- **🔒 100% Local** — No data leaves your machine
- **🌐 Multi-language** — Vietnamese, English, Japanese, Korean, Chinese, Thai, Indonesian

## Requirements

| Resource   | Minimum                     | Recommended             |
| ---------- | --------------------------- | ----------------------- |
| RAM        | 8GB (with Qwen3.5 0.8B)    | 16GB                    |
| CPU        | Any x86_64                  | 4+ cores                |
| OS         | Linux                       | Ubuntu 24.04+           |
| Browser    | Google Chrome 120+          | Chrome 149+             |
| Disk       | 6GB free                    | 15GB+                   |

## Quick Start (on a new machine)

```bash
# 1. Install the CLI globally (no git clone needed)
uv tool install 'git+https://github.com/Mavis2103/browser-translator'

# 2. Install system + Python deps
browser-translator install-deps

# 3. Start Ollama (if not already running)
ollama serve &

# 4. Start the backend
browser-translator start
#   → Ctrl+C to stop
#   → Or: browser-translator start --daemon  (background, use `stop` to kill)
```

```
# Load extension in Chrome
chrome://extensions  →  Developer mode  →  Load unpacked
Select: browser-translator/backend/extension/

# If installed via git clone, the extension is at:
#   browser-translator/backend/extension/
# If only the CLI was installed (no clone), get the extension separately:
#   1. browser-translator build-ext    (creates a .zip)
#   2. Extract and load in Chrome
```

Then click the 🌐 icon in the Chrome toolbar to open the control panel.

## Managing the Backend

The `browser-translator` CLI is your single entry point:

```
browser-translator start          # Foreground (Ctrl+C to stop)
browser-translator start --daemon # Background daemon
browser-translator stop           # Stop daemonized backend
browser-translator status         # Health check + model status
browser-translator build-ext      # Package extension as .zip
browser-translator install-deps   # Install all deps (fresh machine)
```

### `browser-translator start`

- Auto-detects Ollama (checks PATH, then `~/.local/bin`, `/usr/local/bin`, `/usr/bin`)
- Derives `OLLAMA_LIBRARY_PATH` from the binary location when needed
- Starts the FastAPI backend on port 8765
- No Chrome auto-launch — you load the extension manually

### `browser-translator build-ext`

Packages `extension/` into `dist/browser-translator-extension-v1.0.4.zip` for distribution:

```bash
browser-translator build-ext
# → dist/browser-translator-extension-v1.0.4.zip (ready to share)
```

### `browser-translator status`

```
✓ Backend: ok
  URL:   http://0.0.0.0:8765
  Audio: idle
  STT:   ✓ Moonshine
  TTS:   ✓ Moonshine
  OCR:   ✗ not loaded
  LLM:   qwen3.5:0.8b
```

## Architecture

```
┌──────────────────────────────────────┐
│  Chrome Extension (MV3)              │
│  ┌──────────┐  ┌────────────────┐    │
│  │tabCapture│  │  Content Script│    │
│  │→ audio   │  │  → overlays    │    │
│  └────┬─────┘  └──────┬─────────┘    │
│       │               │              │
│   WebSocket           HTTP           │
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
│  Ollama (Qwen3.5) ← localhost:11434  │
└──────────────────────────────────────┘
```

## Components

| Component        | Model / Tool               | Size   | RAM      |
| ---------------- | -------------------------- | ------ | -------- |
| Speech-to-Text   | Moonshine tiny-vi          | 26 MB  | ~100 MB  |
| Text-to-Speech   | Piper vi_VN                | 50 MB  | ~100 MB  |
| OCR              | PaddleOCR PP-OCRv6 tiny    | 3 MB   | <100 MB  |
| Translation      | Qwen3.5 0.8B via Ollama    | ~1 GB  | ~1.3 GB  |
| Fallback chain   | Qwen3.5 1.5B / 4B          | ~5 GB  | ~4 GB    |

### Optional: OCR Support

OCR requires `paddleocr` and `paddlepaddle` (CPU):

```bash
pip install paddleocr paddlepaddle
# Or: uv pip install browser-translator[ocr]
```

## Project Structure

```
browser-translator/
├── backend/                # Python Backend (package)
│   ├── __init__.py
│   ├── cli.py              # uv tool entry point (browser-translator)
│   ├── main.py             # FastAPI server (WebSocket + HTTP)
│   ├── config.py           # Configuration (env-var driven)
│   ├── audio_pipeline.py   # STT → Translation → TTS
│   ├── ocr_pipeline.py     # Screenshot → OCR → Translation
│   ├── translation.py      # Ollama translation client
│   ├── extension/          # ← Chrome Extension (MV3) bundled in wheel
│   │   ├── manifest.json
│   │   ├── background.js
│   │   ├── popup.html
│   │   ├── popup.js
│   │   ├── content.js
│   │   ├── content.css
│   │   ├── styles.css
│   │   └── icons/
├── extension -> backend/extension/  # symlink for backward compat
├── scripts/
│   ├── start.sh            # Legacy: auto-start backend + Chrome (w/ extension)
│   └── install.sh          # Legacy: first-time setup
├── pyproject.toml           # ← NEW: uv tool / pip install support
├── requirements.txt
├── README.md
└── CHANGELOG.md
```

## Env vars (all optional)

| Variable                | Default                    | Description                     |
| ----------------------- | -------------------------- | ------------------------------- |
| `OLLAMA_URL`            | `http://localhost:11434`   | Ollama endpoint                 |
| `TRANSLATION_MODEL`     | `qwen3.5:0.8b`            | Primary translation model       |
| `TRANSLATION_MODEL_CHAIN` | `qwen3.5:0.8b,qwen3.5:1.5b` | Fallback chain            |
| `CDP_URL`               | `http://localhost:9222`    | Chrome DevTools Protocol        |
| `SERVER_HOST`           | `0.0.0.0`                 | Backend bind address            |
| `SERVER_PORT`           | `8765`                    | Backend port                    |
| `STT_MODEL`             | `tiny-vi`                 | Moonshine STT model name        |
| `PIPER_VOICE`           | `vi_VN`                   | Piper voice locale              |
| `OLLAMA_BIN`            | auto-detected             | Override Ollama binary path     |
| `OLLAMA_LIBRARY_PATH`   | auto-detected             | Override Ollama lib path        |

> **OLLAMA_LIBRARY_PATH**: Only needed when Ollama was installed from the GitHub tarball (not the official installer). The CLI and shell scripts auto-detect this from the binary location. You should not need to set it manually.

## Legacy mode (shell scripts)

The `.sh` scripts are kept as fallback but the `browser-translator` CLI is preferred.

```bash
# One-time setup
./scripts/install.sh

# Start everything (incl. Chrome auto-launch)
./scripts/start.sh
```

## Technical Notes

- **Audio**: Chrome's `tabCapture` captures only the active tab. For system-wide audio, use PipeWire monitor.
- **STT**: Moonshine tiny-vi (26 MB) handles Vietnamese speech on CPU in realtime.
- **TTS**: Piper vi_VN generates natural Vietnamese speech.
- **OCR**: PaddleOCR PP-OCRv6 tiny (3 MB) via OpenVINO on CPU.
- **Translation**: Qwen3.5 0.8B runs locally via Ollama at ~2-5s on i5 CPU.

## License

MIT
