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

## Quick Start

### Option A: Install CLI from GitHub (no git clone)

```bash
# 1. Install the CLI globally — extension bundled in wheel
uv tool install 'git+https://github.com/Mavis2103/browser-translator'

# 2. Install system deps (ffmpeg, etc.)
browser-translator install-deps

# 3. Start Ollama (if not already running)
ollama serve &

# 4. Start the backend
browser-translator start              # foreground; Ctrl+C to stop
# or:  browser-translator start --daemon   # background; use `stop` to kill
```

```
# 5. Extract extension and load in Chrome:
browser-translator build-ext                # creates ~/.local/share/browser-translator/dist/*.zip
unzip ~/.local/share/browser-translator/dist/browser-translator-extension-*.zip -d /tmp/ext
chrome://extensions  →  Developer mode  →  Load unpacked  →  select /tmp/ext/extension/
```

### Option B: Clone the repo (development or Chrome auto-launch)

```bash
git clone https://github.com/Mavis2103/browser-translator.git
cd browser-translator

# Install CLI in editable mode
uv tool install -e .

# System deps
browser-translator install-deps

# Load extension directly from the repo
# chrome://extensions → Developer mode → Load unpacked → select /path/to/browser-translator/backend/extension/
```

### Then click the 🌐 icon in the Chrome toolbar to open the control panel.

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

Packages the Chrome extension into a `.zip` for distribution. Output goes to
`~/.local/share/browser-translator/dist/`:

```bash
browser-translator build-ext
# → ~/.local/share/browser-translator/dist/browser-translator-extension-v1.0.5.zip (ready to share)
```

When developing from a cloned repo, the extension can also be loaded directly
without re-packaging (see Option B above).

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

OCR requires `paddleocr` and `paddlepaddle` (CPU, ~1 GB disk):

```bash
# In a clone (editable install):
pip install paddleocr paddlepaddle

# Via uv tool (git install, OCR extra):
uv tool install --with paddleocr --with paddlepaddle --reinstall 'git+https://github.com/Mavis2103/browser-translator'
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
