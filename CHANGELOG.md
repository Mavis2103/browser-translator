# Changelog

All notable changes to this project will be documented in this file.

## [v1.0.2] - 2026-06-20

### Added
- **Silence-aware audio segmentation**: replaces hard-coded 5s window. Flushes on natural pause (0.8s silence after voice activity) with 30s hard cap and 1s min window.
- **Browser-side screenshot capture**: popup uses `chrome.tabs.captureVisibleTab` directly, bypassing CDP round-trip. New `/api/ocr/image` endpoint accepts base64 images.
- **OCR overlay panel**: translated text appears as a floating draggable panel on the webpage (closed via ✕ or Escape).
- **Quality slider**: popup dropdown selects translation model (Fast 1.5B / Balanced 4B / Quality 8B) with automatic fallback chain.
- **Health indicator row**: real-time model status (STT/TTS/OCR/LLM) in popup footer, polled every 5s.
- **Audio chunk deduplication**: 4-byte big-endian sequence prefix on each binary chunk, duplicates dropped at backend.
- **Ollama model fallback chain**: `translate()` tries model chain (qwen3.5:4b → 1.5b → 8b) on failure; explicit model override tries then falls through.

### Fixed
- **PaddleX API migration**: PaddleOCR v3.x works with `lang` + `use_textline_orientation` only; legacy kwargs removed.
- **Ollama cold-load timeout**: bumped to 120s for first translation to handle model load latency.
- **Translation scoping bug**: `msg` variable used outside its scope in `startAudioCapture`.

### Changed
- `backend/config.py`: new constants `MIN_FLUSH_DURATION`, `MAX_BUFFER_DURATION`, `SILENCE_DURATION` (0.8s), `TRANSLATION_MODEL_CHAIN`.
- `backend/main.py`: new `OcrCaptureByImageRequest` model + `/api/ocr/image` endpoint; health returns per-model status.
- `backend/translation.py`: `translate()` accepts optional `model`; falls back through `TRANSLATION_MODEL_CHAIN`.
- `extension/manifest.json`: added `"tabs"` permission for `captureVisibleTab`.
- `extension/popup.html`: added model selector + health row.
- `extension/popup.js`: browser-side screenshot capture + model propagation + health polling.
- `extension/background.js`: 4-byte seq prefix on audio chunks; `translationModel` in WebSocket messages.
- `extension/content.js`: `showOcrPanel()` floating panel with close handler.

## [v1.0.1] - 2026-06-20

### Changed
- **OCR engine**: Migrated to new PaddleX API (PaddleOCR v3.x). Removed legacy kwargs `use_gpu`, `use_angle_cls`, `enable_mkldnn`, `rec_batch_num`, `det_db_thresh`. Only `lang` + `use_textline_orientation` accepted.
- **TTS engine**: Swapped Piper for **Moonshine built-in TTS** (`TextToSpeech` from `moonshine_voice.tts`) with voice `piper_vi_VN-vais1000-medium`. Auto-downloads on first use. No separate voice file download needed.
- **STT engine**: Confirmed correct API — `Transcriber(model_path=get_model_for_language('vi'), model_arch=...)` (model arch comes back as `BASE`, not `TINY`).
- **Translation**: Added `think: false` to Ollama `generate` request to skip Qwen3.5 reasoning chain on every translation.

### Fixed
- **Backend startup**: PaddleOCR no longer crashes with `Unknown argument: use_gpu`.
- **Ollama boot**: Manual install requires `OLLAMA_LIBRARY_PATH=$HOME/.local/lib/ollama` for `llama-server` discovery. Now auto-set in `start.sh` and documented.
- **CDP graceful failure**: OCR endpoint returns clean 200 with `success: false` instead of crashing when Chrome not running on port 9222.

### Added
- `scripts/install.sh`: One-time installer for new machines (installs apt packages, uv deps, Ollama CPU binary, pulls `qwen3.5:4b`).
- `scripts/stop.sh`: Clean shutdown script.
- `requirements.txt`: Added explicit `paddlepaddle>=3.0`.
- `README.md`: Updated Quick Start pointing to `./scripts/install.sh`.
- `LICENSE`: MIT license file added.

## [v1.0.0] - 2026-06-20

### Initial Release
- Chrome Extension (MV3) with `tabCapture` audio capture
- Python backend with FastAPI WebSocket streaming (`/ws/audio`)
- Speech-to-Speech translation pipeline: Moonshine STT → Qwen3.5 translate → Moonshine TTS
- OCR page translation: CDP screenshot → PaddleOCR → Qwen3.5 translate
- Popup control panel with language selection (VI/EN/JA/KO/ZH/TH/ID)
- Content script with toast notifications and overlay support
- Health endpoint (`/api/health`)
- CPU-only inference, no GPU required

[unreleased]: https://github.com/Mavis2103/browser-translator/compare/v1.0.1...HEAD
[v1.0.1]: https://github.com/Mavis2103/browser-translator/compare/v1.0.0...v1.0.1
[v1.0.0]: https://github.com/Mavis2103/browser-translator/releases/tag/v1.0.0
