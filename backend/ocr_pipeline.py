"""OCR pipeline: CDP screenshot → PaddleOCR → Translation"""

import asyncio
import base64
import json
import logging
from typing import Optional

from .config import CDP_URL, PADDLE_OCR_LANG, FULL_PAGE_SCREENSHOT
from .translation import translate

logger = logging.getLogger(__name__)


class OcrPipeline:
    """Handles browser screenshot capture via CDP → OCR → Translation."""

    def __init__(self):
        self.ocr_reader = None
        self._loaded = False
        self.cdp_ws_url = None

        # Settings
        self.source_lang = "auto"
        self.target_lang = "vi"
        self.translation_model = "qwen3.5:0.8b"

    def load_models(self):
        """Load PaddleOCR model."""
        if self._loaded:
            return

        logger.info("Loading PaddleOCR...")
        try:
            from paddleocr import PaddleOCR
            # New PaddleX API (v3.x): pass `lang` and `use_textline_orientation` only;
            # legacy kwargs like `use_gpu`, `use_angle_cls`, `enable_mkldnn` no longer exist.
            self.ocr_reader = PaddleOCR(
                lang=PADDLE_OCR_LANG,
                use_textline_orientation=True,
            )
            logger.info("PaddleOCR loaded successfully")
            self._loaded = True
        except Exception as e:
            logger.error("Failed to load PaddleOCR: %s", e)
            raise

    async def get_cdp_ws_url(self) -> Optional[str]:
        """Get the WebSocket URL for the CDP browser."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{CDP_URL}/json/version") as resp:
                    data = await resp.json()
                    ws_url = data.get("webSocketDebuggerUrl")
                    logger.debug("CDP WS URL: %s", ws_url)
                    return ws_url
        except Exception as e:
            logger.error("Failed to get CDP URL: %s", e)
            return None

    async def capture_screenshot(self) -> Optional[bytes]:
        """Capture a full-page screenshot via CDP."""
        ws_url = await self.get_cdp_ws_url()
        if not ws_url:
            logger.error("No CDP connection available")
            return None

        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                # Enable Page domain
                await ws.send_json({"id": 1, "method": "Page.enable"})
                await ws.receive()

                if FULL_PAGE_SCREENSHOT:
                    # Get full page metrics
                    await ws.send_json({"id": 2, "method": "Page.getLayoutMetrics"})
                    resp = await ws.receive()
                    metrics = json.loads(resp.data)
                    content_size = metrics.get("result", {}).get("contentSize", {})
                    width = int(content_size.get("width", 1920))
                    height = int(content_size.get("height", 1080))

                    # Resize viewport to capture full page
                    await ws.send_json({
                        "id": 3,
                        "method": "Emulation.setDeviceMetricsOverride",
                        "params": {
                            "width": width,
                            "height": height,
                            "deviceScaleFactor": 1,
                            "mobile": False,
                        }
                    })
                    await ws.receive()

                # Capture screenshot
                await ws.send_json({
                    "id": 4,
                    "method": "Page.captureScreenshot",
                    "params": {
                        "format": "png",
                        "captureBeyondViewport": FULL_PAGE_SCREENSHOT,
                        "fromSurface": True,
                    }
                })
                resp = await ws.receive()
                data = json.loads(resp.data)
                screenshot_b64 = data.get("result", {}).get("data")
                if not screenshot_b64:
                    logger.error("No screenshot data in CDP response")
                    return None

                img_bytes = base64.b64decode(screenshot_b64)
                logger.debug("Screenshot captured: %d bytes", len(img_bytes))
                return img_bytes

    async def process_screenshot(self, img_bytes: bytes) -> dict:
        """Run OCR on a screenshot image and translate results."""
        if not self._loaded:
            self.load_models()

        # Save temporarily for PaddleOCR
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(img_bytes)
            temp_path = f.name

        try:
            # Run OCR
            result = self.ocr_reader.ocr(temp_path, cls=True)
            logger.debug("OCR result type: %s", type(result))

            # Extract text blocks
            text_blocks = []
            all_text = []

            if result and result[0]:
                for line in result[0]:
                    bbox, (text, confidence) = line
                    if confidence > 0.3:  # Filter low confidence
                        text_blocks.append({
                            "text": text,
                            "confidence": float(confidence),
                            "bbox": bbox,
                        })
                        all_text.append(text)

            full_text = "\n".join(all_text)
            logger.info("OCR extracted %d text blocks, %d chars", len(text_blocks), len(full_text))

            # Translate
            translated = None
            if full_text.strip():
                translated = translate(full_text, self.source_lang, self.target_lang, model=self.translation_model)

            return {
                "success": True,
                "texts": full_text[:2000],  # Truncate for display
                "translated": translated[:2000] if translated else "",
                "blocks": text_blocks[:50],  # Limit blocks
                "block_count": len(text_blocks),
            }

        except Exception as e:
            logger.exception("OCR processing failed: %s", e)
            return {"success": False, "error": str(e)}
        finally:
            import os
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    def set_language(self, source: str, target: str, model: str = None):
        self.source_lang = source
        self.target_lang = target
        if model:
            self.translation_model = model
