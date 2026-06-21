# Changelog

All notable changes to this project will be documented in this file.

## [v1.1.0] - 2026-06-21

### Added
- **System-level audio capture (`--system` flag)** — capture browser/system audio directly from the PipeWire/PulseAudio monitor source, bypassing the browser extension. New `backend/audio_system.py` module: PortAudio-based loopback capture + TTS playback. CLI: `browser-translator start --system`. HTTP API: `POST/GET /api/audio/system`. Automatic backend detection (PipeWire/PulseAudio/ALSA). Uses `sounddevice` for low-latency capture. 16kHz mono Int16 (no WebM/Opus). TTS plays to speakers via background output stream. Optional webrtcvad gate (falls back to energy-based silence detection).

## [v1.0.13] - 2026-06-21

### Fixed
- **No TTS audio output** — `playTranslatedAudio()` was instantiated from the service worker's `handleBackendMessage('tts_audio')`. Service workers have no DOM, so `AudioContext` constructor silently failed (or never existed) and no audio was scheduled. Moved TTS playback to `offscreen.js` (which is a DOM context with full WebAudio support) and split the offscreen-doc WS `onmessage` switch so only `tts_audio` plays locally; the rest (`translation`, `transcription`, `ocr_result`, `status`, `error`) is forwarded to the service worker for popup/content-script routing.

## [v1.0.12] - 2026-06-21

### Fixed
- **`schedule() failed: call_soon_threadsafe() got an unexpected keyword argument 'loop'`** — `BaseEventLoop.call_soon_threadsafe` only forwards *positional* args to the scheduled callback; passing `loop=` keyword bound to `asyncio.ensure_future` failed with `TypeError`. Replaced with the canonical helper **`asyncio.run_coroutine_threadsafe(coro, main_loop)`** — the standard idiom for scheduling coroutines from a worker thread onto a known event loop.

## [v1.0.11] - 2026-06-21

### Fixed
- **`RuntimeError: There is no current event loop in thread 'asyncio_0'`** — The audio pipeline runs `process_audio_chunk` inside `run_in_executor(...)` (background thread). Its callbacks `on_transcription` / `on_translation` / `on_tts_audio` called `asyncio.ensure_future` from that worker thread. Python ≥3.10 removed implicit event-loop creation for non-main threads, so this raised on every transcript.

  Fix: capture the main loop once per WebSocket handler via `asyncio.get_running_loop()`, then dispatch cross-thread with `main_loop.call_soon_threadsafe(asyncio.ensure_future, coro, loop=main_loop)`.

## [v1.0.10] - 2026-06-21

### Fixed
- **`No module named 'pyaudioop'` crash on Python 3.13** — pydub's WebM/Opus decoder transitively imports `pyaudioop` which was removed from Python 3.13 standard library. When the extension sent WebM/Opus, the backend fell back to treating it as raw PCM, then crashed in `np.frombuffer(..., dtype=np.int16)` with `ValueError: buffer size must be a multiple of element size`.

### Changed
- **Audio encoding protocol** — extension now streams raw **PCM16 LE 16kHz mono** via WebAudio `ScriptProcessorNode` instead of WebM/Opus via `MediaRecorder`. This:
  - Matches Moonshine STT's native format (Float32, mono, 16kHz) — backend converts Int16 → Float32 internally.
  - Eliminates the broken pydub decode path entirely.
  - Bandwidth: 32 KB/s × 1 channel × 2 bytes = same or lower than WebM/Opus 16 kbps.
- **`_decode_audio_chunk`** — guards `pydub` import so it only triggers for legacy WebM clients; misaligned PCM chunks are dropped with a warning instead of crashing.

## [v1.0.9] - 2026-06-21

### Fixed
- **"Start → Stop ngay lập tức"** — Service worker passed `consumerTabId: <tab.id>` to `chrome.tabCapture.getMediaStreamId()`, but the consumer is the offscreen document, NOT the tab. This caused `getUserMedia` inside `offscreen.js` to reject, triggering `stopCapture()` immediately. Removed the erroneous `consumerTabId` parameter (Chrome 116+ allows cross-context consumption when omitted, per official Google sample).

## [v1.0.8] - 2026-06-21

### Fixed
- **`chrome.tabCapture.capture is not a function`** — Service worker cannot call `capture()` directly (extension API is "Foreground only"). Replaced with the MV3 offscreen-document pattern: `chrome.tabCapture.getMediaStreamId()` in service worker → consumer offscreen document runs `navigator.mediaDevices.getUserMedia` + `MediaRecorder`. Works on Chrome 116+, Brave, and Edge.

### Added
- **`offscreen.html` + `offscreen.js`** — new extension files for audio capture.
- **`minimum_chrome_version: "116"`** — manifest pins the minimum Chromium that supports offscreen-document `getMediaStreamId` consumption.
- **`optional: [{ sampleRate: 16000 }]`** in `offscreen.js` getUserMedia, matching Moonshine STT's native sample rate.

## [v1.0.7] - 2026-06-21

### Fixed
- **WebSocket `/ws/audio` 404** — `uvicorn` was missing the `websockets` library, so it silently returned 404 on the `Upgrade: websocket` request instead of `101 Switching Protocols`. Added `websockets>=12.0` as a hard dependency (no more relying on `uvicorn[standard]` extras resolution). Extension now connects on first load.

## [v1.0.6] - 2026-06-21

### Fixed
- **`install-deps` pydub false alarm** — pydub 0.25.1 emits `SyntaxWarning` on Python 3.13. Broadened the import test's `except ImportError` to `except Exception` so the real error shows, and pinned `pydub>=0.25,<0.26`.
- **`install-deps` sudo handling** — was using `sudo apt-get` which requires a TTY. Switched to `sudo -n` (non-interactive) and added `try/except` so the script continues with a warning instead of crashing half-way, even when passwordless sudo isn't configured.
- **`browser-translator version`** — added subcommand to print current version.

## [v1.0.5] - 2026-06-21

### Added
- **`browser-translator` CLI** — new entry point via `uv tool install`. Commands:
  `start`, `start --daemon`, `stop`, `status`, `build-ext`, `install-deps`.
- **Extension bundled in wheel** — `backend/extension/` is included as package data.
  `build-ext` works from installed packages (no repo clone needed).
- **`MANIFEST.in`** — ensures extension files are included in source distributions.
- **`backend/__init__.py`** — `backend` is now a proper Python package.
- **`pyproject.toml`** — project metadata, dependencies, scripts entry point.

### Changed
- **Extension moved** to `backend/extension/`. Symlink `extension → backend/extension/` at
  project root preserves backward compatibility.
- **`build-ext` output** → `~/.local/share/browser-translator/dist/` (XDG-compatible).
- **`install-deps`** no longer requires `requirements.txt` path; shows OCR install hint.
- **Version bumped** to 1.0.5 across all components.
- **README** rewritten with two Quick Start paths (git install vs clone).

### Fixed
- **No hardcoded paths** — Ollama binary auto-detected (PATH first, then common locations);
  `OLLAMA_LIBRARY_PATH` derived from binary location. Shell scripts updated.
- **No `sys.path.insert(0, ...)` hack** — removed from `main.py`. Package imports work
  via proper module resolution.
- **No `PROJECT_ROOT` global** — replaced with `_project_root()` helper that only
  resolves to the repo root in development/editable mode.

## [v1.0.4] - 2026-06-20

### Performance
- **Parallel Moonshine load** — STT and TTS now load concurrently on two threads with a shared disk lock. Reduces cold-start by ~40% (5-7s → ~3-4s) and warm-start by ~50% (3-4s → ~1-2s). Library itself doesn't share STT/TTS state internally, so the win comes from concurrent disk I/O on the two `.ort` bundles.

## [v1.0.3] - 2026-06-20

### Changed
- **Default translation model: qwen3.5:0.8b** (was qwen3.5:4b). 0.8b is the new default:
  - Disk: 1.0 GB (was 3.4 GB) — 70% smaller
  - RAM at inference: ~1.3 GB (was ~4 GB)
  - Latency: ~1s warm (vs ~5s cold)
  - Vietnamese output: still clean, tone + formal accuracy maintained
- **Model fallback chain rebalanced**: `0.8b → 1.5b → 4b → 8b` (was `4b → 1.5b → 8b`).
- **Popup quality slider**: added 0.8b option at top, now default. Option order: Fast 0.8B → Balanced 1.5B → Quality 4B → Maximum 8B.
- **install.sh**: pulls `qwen3.5:0.8b` as default; also attempts `qwen3.5:1.5b` as automatic fallback.
- **README**: updated Quick Start to reflect new model + smaller footprint.

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

## [v1.1.0] - 2026-06-21

### Added
- **System-level audio capture (--system flag)** — capture browser/system audio directly from
  PipeWire/PulseAudio monitor source, bypassing the browser extension entirely.
  - New module `backend/audio_system.py`: PortAudio-based loopback capture + TTS playback.
  - New CLI flag: `browser-translator start --system`.
  - New HTTP API: `POST/GET /api/audio/system` for runtime start/stop/toggle.
  - Automatically detects PipeWire vs PulseAudio vs ALSA.
  - Uses `sounddevice` (PortAudio) for low-latency capture; falls back gracefully if
    libportaudio is missing.
  - 16kHz mono Int16 directly (no WebM/Opus encoding needed).
  - TTS played directly to speakers via a background output stream.
  - webrtcvad gate (optional; falls back to energy-based silence detection).
