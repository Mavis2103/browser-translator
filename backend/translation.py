"""Translation module - Ollama client for Qwen3.5 4B"""

import json
import urllib.request
import urllib.error
import logging
from typing import Optional

from .config import OLLAMA_URL, TRANSLATION_MODEL, TRANSLATION_MODEL_CHAIN

logger = logging.getLogger(__name__)

LANG_NAMES = {
    "vi": "Vietnamese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "th": "Thai",
    "id": "Indonesian",
    "auto": "auto-detect",
}

TRANSLATION_SYSTEM_PROMPT = """You are a precise translator. Translate the given text accurately while preserving meaning, tone, and formatting.

Rules:
- Translate ONLY the text content, nothing else
- Preserve numbers, URLs, code fragments, and proper names
- If the source language is unknown, detect it first
- Return ONLY the translated text, no explanations or notes
- Keep the same paragraph structure"""


def translate(text: str, source_lang: str = "auto", target_lang: str = "vi", model: str = None) -> Optional[str]:
    """Translate text using Ollama Qwen3.5 model.

    Tries the model chain in order — picks the first model that responds.
    """
    if not text or not text.strip():
        return ""

    source_name = LANG_NAMES.get(source_lang, source_lang)
    target_name = LANG_NAMES.get(target_lang, target_lang)

    user_prompt = f"Translate from {source_name} to {target_name}:\n\n{text.strip()}"

    # Try models in order: explicit override → configured chain.
    # An explicit user model is tried first; if it fails we fall through to the chain.
    candidates = []
    if model and model not in TRANSLATION_MODEL_CHAIN:
        candidates.append(model)
    candidates.extend([c for c in TRANSLATION_MODEL_CHAIN if c])
    last_error = None

    for candidate in candidates:
        if not candidate:
            continue
        payload = {
            "model": candidate,
            "prompt": user_prompt,
            "system": TRANSLATION_SYSTEM_PROMPT,
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
                "num_predict": 512,
            }
        }

        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                translated = result.get("response", "").strip()
                if translated:
                    logger.debug("Translation [%s]: %r → %r", candidate, text[:50], translated[:50])
                    return translated
            # Model responded but with empty result — try next
            continue
        except urllib.error.HTTPError as e:
            code = e.code
            body = e.read().decode()
            logger.warning("Model %s HTTP %s: %s", candidate, code, body[:200])
            if code != 404:  # 404 = model not pulled, skip to next
                last_error = f"HTTP {code}: {body[:100]}"
            continue
        except urllib.error.URLError as e:
            logger.error("Ollama connection error: %s", e.reason)
            return None  # Connection down entirely — no point retrying
        except Exception as e:
            logger.warning("Model %s failed: %s", candidate, e)
            last_error = str(e)
            continue

    # All models exhausted
    logger.error("All translation models failed. Last error: %s", last_error)
    return None


def detect_language(text: str) -> str:
    """Detect language using Ollama."""
    payload = {
        "model": TRANSLATION_MODEL,
        "prompt": f"What language is this text written in? Reply with a language code only (e.g., 'en', 'vi', 'ja', 'ko', 'zh', 'th', 'id'). Text: {text[:200]}",
        "system": "Reply with a short language code only (2 letters).",
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 10,
        }
    }

    req_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=req_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            lang = result.get("response", "").strip().lower()[:2]
            if lang in LANG_NAMES:
                return lang
            return "en"
    except Exception:
        return "en"
