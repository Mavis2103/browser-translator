"""NLLB-200 translation engine via CTranslate2.

Provides fast INT8-quantized EN→VI translation for browser-translator.

Converter script (run once):
    ct2-transformers-converter \\
      --model facebook/nllb-200-distilled-600M \\
      --output_dir ~/.cache/browser-translator/nllb-600m-ct2-int8 \\
      --quantization int8 --force
"""

import logging
import os
from typing import Optional

from .config import NLLB_MODEL_PATH, NLLB_SRC_LANG, NLLB_TGT_LANG

logger = logging.getLogger(__name__)

# Cache model instance (singleton, loaded once at process start)
_translator = None
_tokenizer = None
# One-time check: True=available, False=unavailable, None=unchecked
_availability = None


def _check_installed():
    """Return True if ctranslate2 is importable (installed as extra)."""
    try:
        import ctranslate2  # noqa: F401
        return True
    except ImportError:
        return False


def _check_model_path():
    """Check if the CT2 model directory exists (one-time)."""
    global _availability
    if _availability is not None:
        return _availability

    model_path = os.path.expanduser(NLLB_MODEL_PATH)
    if not _check_installed():
        logger.warning("ctranslate2 not installed (add --with ctranslate2). NLLB unavailable.")
        _availability = False
        return False

    if not os.path.isdir(model_path):
        logger.warning(
            "NLLB model not found at %s. "
            "Run: ct2-transformers-converter --model facebook/nllb-200-distilled-600M "
            "--output_dir %s --quantization int8 --force",
            model_path, model_path,
        )
        _availability = False
        return False

    _availability = True
    return True


def _load_model():
    """Lazy-load NLLB model via CTranslate2."""
    global _translator, _tokenizer
    if _translator is not None:
        return _translator, _tokenizer

    if not _check_model_path():
        raise FileNotFoundError(f"NLLB model not available: {NLLB_MODEL_PATH}")

    import ctranslate2
    import transformers

    model_path = os.path.expanduser(NLLB_MODEL_PATH)

    logger.info("Loading NLLB CT2 INT8 model from %s ...", model_path)
    _translator = ctranslate2.Translator(model_path, device="cpu")

    # Tokenizer files live in a tokenizer/ subfolder (separate from CT2 config)
    tok_path = os.path.join(model_path, "tokenizer")
    if not os.path.isdir(tok_path):
        tok_path = model_path  # fallback
    _tokenizer = transformers.AutoTokenizer.from_pretrained(
        tok_path,
        src_lang=NLLB_SRC_LANG,
    )
    logger.info("NLLB model loaded (CT2 INT8).")
    return _translator, _tokenizer


def translate(
    text: str,
    source_lang: str = "auto",
    target_lang: str = "vi",
    model: Optional[str] = None,
) -> Optional[str]:
    """Translate text using NLLB-200 CT2 INT8.

    Args:
        text: English text to translate.
        source_lang: Ignored (NLLB model is fixed EN→VI).
        target_lang: Ignored for now (defaults to VI).
        model: Ignored (the NLLB model is fixed).

    Returns:
        Translated Vietnamese text, or None on failure.
    """
    if not text or not text.strip():
        return ""

    try:
        translator, tokenizer = _load_model()
    except (FileNotFoundError, Exception):
        return None

    try:
        # Tokenize (no PyTorch dependency — get token IDs as list)
        tokens = tokenizer.tokenize(text)
        tokens = [tokenizer.bos_token] + tokens[:510] + [tokenizer.eos_token]

        # Translate with target language prefix
        results = translator.translate_batch(
            [tokens],
            target_prefix=[[NLLB_TGT_LANG]],
        )
        output_tokens = results[0].hypotheses[0]
        translated = tokenizer.decode(
            tokenizer.convert_tokens_to_ids(output_tokens),
            skip_special_tokens=True,
        ).strip()

        if not translated:
            logger.warning("NLLB returned empty translation for: %r", text[:50])
            return None

        logger.debug("NLLB translation: %r → %r", text[:50], translated[:50])
        return translated

    except Exception as e:
        logger.debug("NLLB translation failed for %r: %s", text[:50], e)
        return None
