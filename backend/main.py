"""Browser Translator - FastAPI Backend Server

Provides:
- WebSocket endpoint for real-time audio streaming (STT → Translation → TTS)
- HTTP endpoint for OCR screenshot + translation
- Static file serving for the Chrome extension
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.config import SERVER_HOST, SERVER_PORT, LOG_LEVEL, PROJECT_DIR
from backend.audio_pipeline import AudioPipeline
from backend.ocr_pipeline import OcrPipeline

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("browser-translator")

# Create FastAPI app
app = FastAPI(title="Browser Translator", version="1.0.4")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global pipeline instances
audio_pipeline: AudioPipeline = None
ocr_pipeline: OcrPipeline = None

# Track active audio clients
active_audio_clients = {}

# ========== Models ==========

class LanguageSetRequest(BaseModel):
    sourceLang: str = "auto"
    targetLang: str = "vi"

class OcrCaptureRequest(BaseModel):
    sourceLang: str = "auto"
    targetLang: str = "vi"
    fullPage: bool = True

class OcrCaptureByImageRequest(BaseModel):
    sourceLang: str = "auto"
    targetLang: str = "vi"
    image: str  # base64-encoded PNG

class OcrCaptureResponse(BaseModel):
    success: bool
    texts: str = ""
    translated: str = ""
    error: str = ""


# ========== Lifecycle ==========

@app.on_event("startup")
async def startup():
    global audio_pipeline, ocr_pipeline

    logger.info("=" * 50)
    logger.info("Browser Translator Backend starting...")
    logger.info("=" * 50)

    # Check Ollama availability
    await check_ollama()

    # Initialize pipelines
    audio_pipeline = AudioPipeline()
    ocr_pipeline = OcrPipeline()

    # Load STT + TTS models (blocking, run in thread pool)
    logger.info("Loading audio models (this may take a moment)...")
    await asyncio.get_event_loop().run_in_executor(None, audio_pipeline.load_models)

    # Load OCR model
    logger.info("Loading OCR model...")
    try:
        ocr_pipeline.load_models()
    except Exception as e:
        logger.warning("OCR model failed to load: %s", e)
        logger.warning("OCR features will be unavailable")

    # Set up callbacks for audio pipeline
    audio_pipeline.on_transcription = lambda text: None  # handled by WebSocket connection

    logger.info("Backend ready on ws://%s:%s", SERVER_HOST, SERVER_PORT)


async def check_ollama():
    """Check if Ollama is running and has the model available."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            models = [m["name"] for m in data.get("models", [])]
            logger.info("Ollama models available: %s", ", ".join(models) if models else "none")

            from backend.config import TRANSLATION_MODEL
            if TRANSLATION_MODEL not in models:
                logger.warning("Model '%s' not found in Ollama! Please run: ollama pull %s",
                               TRANSLATION_MODEL, TRANSLATION_MODEL)
    except urllib.error.URLError:
        logger.error("Ollama not running! Please start it with: ollama serve")
    except Exception as e:
        logger.warning("Could not check Ollama: %s", e)


# ========== WebSocket: Audio Streaming ==========

@app.websocket("/ws/audio")
async def websocket_audio(websocket: WebSocket):
    await websocket.accept()
    client_id = id(websocket)
    logger.info("Audio client connected: %s", client_id)
    active_audio_clients[client_id] = {
        "ws": websocket,
        "capturing": False,
    }

    # Set up callbacks for this client
    async def send_json(msg: dict):
        try:
            await websocket.send_json(msg)
        except Exception:
            pass

    def on_transcription(text):
        asyncio.ensure_future(send_json({
            "type": "transcription",
            "text": text,
        }))

    def on_translation(original, translated, source, target):
        asyncio.ensure_future(send_json({
            "type": "translation",
            "original": original,
            "translated": translated,
            "source": source,
            "target": target,
        }))

    def on_tts_audio(b64_data, sample_rate):
        asyncio.ensure_future(websocket.send_json({
            "type": "tts_audio",
            "data": b64_data,
            "sampleRate": sample_rate,
        }))

    audio_pipeline.on_transcription = on_transcription
    audio_pipeline.on_translation = on_translation
    audio_pipeline.on_tts_audio = on_tts_audio

    try:
        await websocket.send_json({"type": "status", "status": "connected"})

        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.receive":
                data = message.get("bytes") or message.get("text")
                if isinstance(data, str):
                    # JSON message
                    try:
                        msg = json.loads(data)
                        if msg["type"] == "start_capture":
                            audio_pipeline.source_lang = msg.get("sourceLang", "auto")
                            audio_pipeline.target_lang = msg.get("targetLang", "vi")
                            active_audio_clients[client_id]["capturing"] = True
                            logger.info("Audio capture started: %s → %s",
                                        msg.get("sourceLang"), msg.get("targetLang"))
                        elif msg["type"] == "stop_capture":
                            active_audio_clients[client_id]["capturing"] = False
                            audio_pipeline._reset_buffer()
                            logger.info("Audio capture stopped")
                        elif msg["type"] == "set_language":
                            audio_pipeline.set_language(
                                msg.get("sourceLang", "auto"),
                                msg.get("targetLang", "vi"),
                                model=msg.get("translationModel", None),
                            )
                    except json.JSONDecodeError:
                        pass

                elif isinstance(data, bytes) and active_audio_clients[client_id]["capturing"]:
                    # Binary audio data
                    await asyncio.get_event_loop().run_in_executor(
                        None, audio_pipeline.process_audio_chunk, data
                    )

            elif message["type"] == "websocket.disconnect":
                break

    except WebSocketDisconnect:
        logger.info("Audio client disconnected: %s", client_id)
    except Exception as e:
        logger.exception("WebSocket error: %s", e)
    finally:
        active_audio_clients.pop(client_id, None)
        if not active_audio_clients:
            audio_pipeline._reset_buffer()


# ========== HTTP: OCR ==========

@app.post("/api/ocr/capture", response_model=OcrCaptureResponse)
async def ocr_capture(request: OcrCaptureRequest):
    """Capture browser screenshot via CDP and run OCR + translation."""
    logger.info("OCR capture requested: %s → %s", request.sourceLang, request.targetLang)

    ocr_pipeline.source_lang = request.sourceLang
    ocr_pipeline.target_lang = request.targetLang

    img_bytes = await ocr_pipeline.capture_screenshot()
    if not img_bytes:
        logger.error("CDP screenshot capture failed")
        return OcrCaptureResponse(
            success=False,
            error=(
                "Failed to capture screenshot via CDP. "
                "Use the 'Capture Page' button (browser-side) instead."
            )
        )

    logger.debug("Screenshot captured, running OCR...")
    result = await ocr_pipeline.process_screenshot(img_bytes)
    return OcrCaptureResponse(
        success=result.get("success", False),
        texts=result.get("texts", ""),
        translated=result.get("translated", ""),
        error=result.get("error", ""),
    )


@app.post("/api/ocr/image", response_model=OcrCaptureResponse)
async def ocr_image(request: OcrCaptureByImageRequest):
    """Run OCR + translation on a base64-encoded image (captured browser-side)."""
    logger.info("OCR image requested: %s → %s", request.sourceLang, request.targetLang)

    ocr_pipeline.source_lang = request.sourceLang
    ocr_pipeline.target_lang = request.targetLang

    try:
        import base64
        img_bytes = base64.b64decode(request.image)
    except Exception as e:
        return OcrCaptureResponse(success=False, error=f"Invalid base64 image: {e}")

    result = await ocr_pipeline.process_screenshot(img_bytes)
    return OcrCaptureResponse(
        success=result.get("success", False),
        texts=result.get("texts", ""),
        translated=result.get("translated", ""),
        error=result.get("error", ""),
    )


@app.get("/api/health")
async def health():
    """Health check endpoint with model status."""
    return {
        "status": "ok",
        "audio_capturing": any(c["capturing"] for c in active_audio_clients.values()),
        "audio_clients": len(active_audio_clients),
        "models": {
            "stt": audio_pipeline.stt_model is not None if audio_pipeline else False,
            "tts": audio_pipeline.tts_engine is not None if audio_pipeline else False,
            "ocr": ocr_pipeline.ocr_reader is not None if ocr_pipeline else False,
            "translation": audio_pipeline.translation_model if audio_pipeline else "unknown",
        },
        "ollama_available": True,
    }


# ========== Main ==========

def main():
    uvicorn.run(
        "backend.main:app",
        host=SERVER_HOST,
        port=SERVER_PORT,
        log_level=LOG_LEVEL.lower(),
        reload=True,
    )


if __name__ == "__main__":
    main()
