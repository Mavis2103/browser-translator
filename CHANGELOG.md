# Changelog

All notable changes to this project will be documented in this file.

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
