"""Translation module — Ollama Qwen3.5 and/or NLLB-200 via CTranslate2.

The active engine is selected by ``TRANSLATION_ENGINE`` in config:
  - ``"nllb"``  → NLLB-200 CT2 INT8 (fast, good quality, fixed EN↔VI)
  - ``"ollama"`` → Ollama Qwen3.5 model chain (flexible lang pairs)

The ``translate()`` function is a drop-in replacement that dispatches to
the configured engine transparently.
"""

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from .config import (
    OLLAMA_URL,
    TRANSLATION_ENGINE,
    TRANSLATION_MODEL_CHAIN,
)

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


# ── NLLB engine (lazy) ───────────────────────────────────────────

_nllb_available = None  # tri-state: None = unchecked, True/False


def _nllb_translate(text: str) -> Optional[str]:
    """Translate via NLLB-200 CT2 INT8 (fixed EN→VI)."""
    global _nllb_available
    try:
        from . import nllb_translator
        result = nllb_translator.translate(text)
        _nllb_available = result is not None or _nllb_available is not False
        return result
    except Exception as e:
        logger.warning("NLLB translation failed: %s", e)
        _nllb_available = False
        return None


# ── Ollama engine ────────────────────────────────────────────────


def _ollama_translate(text: str, source_lang: str, target_lang: str,
                      model: Optional[str] = None) -> Optional[str]:
    """Translate via Ollama Qwen3.5 model chain."""
    source_name = LANG_NAMES.get(source_lang, source_lang)
    target_name = LANG_NAMES.get(target_lang, target_lang)
    user_prompt = f"Translate from {source_name} to {target_name}:\n\n{text.strip()}"

    candidates = []
    if model and model not in TRANSLATION_MODEL_CHAIN:
        candidates.append(model)
    candidates.extend(c for c in TRANSLATION_MODEL_CHAIN if c)
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
            },
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
                    logger.debug("Translation [%s]: %r → %r",
                                 candidate, text[:50], translated[:50])
                    return translated
            continue
        except urllib.error.HTTPError as e:
            code = e.code
            body = e.read().decode()
            logger.warning("Model %s HTTP %s: %s", candidate, code, body[:200])
            if code != 404:
                last_error = f"HTTP {code}: {body[:100]}"
            continue
        except urllib.error.URLError as e:
            logger.error("Ollama connection error: %s", e.reason)
            return None
        except Exception as e:
            logger.warning("Model %s failed: %s", candidate, e)
            last_error = str(e)
            continue

    logger.error("All Ollama models exhausted. Last error: %s", last_error)
    return None


# ── Public API ───────────────────────────────────────────────────


def translate(text: str, source_lang: str = "auto", target_lang: str = "vi",
              model: Optional[str] = None) -> Optional[str]:
    """Translate *text* from *source_lang* to *target_lang*.

    Dispatches to the configured engine (NLLB or Ollama).
    Falls back to Ollama if NLLB is unavailable.
    """
    if not text or not text.strip():
        return ""

    engine = TRANSLATION_ENGINE.lower()

    # NLLB path — fast, good quality, fixed EN→VI
    if engine == "nllb":
        result = _nllb_translate(text)
        if result is not None:
            return result
        logger.info("NLLB unavailable, falling back to Ollama.")

    # Ollama path (default / fallback)
    return _ollama_translate(text, source_lang, target_lang, model)


def detect_language(text: str) -> str:
    """Detect language using Ollama (unchanged)."""
    payload = {
        "model": TRANSLATION_MODEL_CHAIN[0] if TRANSLATION_MODEL_CHAIN else "qwen3.5:0.8b",
        "prompt": (
            f"What language is this text written in? Reply with a language code only "
            f"(e.g., 'en', 'vi', 'ja', 'ko', 'zh', 'th', 'id'). Text: {text[:200]}"
        ),
        "system": "Reply with a short language code only (2 letters).",
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 10},
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
