"""NLLB-200 translation engine via CTranslate2.

Provides fast INT8-quantized EN→VI translation for browser-translator.
Model is auto-downloaded and converted on first use — zero manual setup.

Converter command (equivalent, in case of issues):
    ct2-transformers-converter \\
      --model facebook/nllb-200-distilled-600M \\
      --output_dir ~/.cache/browser-translator/nllb-600m-ct2-int8 \\
      --quantization int8 --force
"""

import logging
import os
import shutil
from typing import Optional

from .config import NLLB_MODEL_PATH, NLLB_SRC_LANG, NLLB_TGT_LANG

logger = logging.getLogger(__name__)

# Cache model instance (singleton, loaded once at process start)
_translator = None
_tokenizer = None
# One-time check: True=available, False=unavailable, None=unchecked
_availability = None


def _check_installed():
    """Return True if ctranslate2 is importable."""
    try:
        import ctranslate2  # noqa: F401
        return True
    except ImportError:
        return False


def _auto_setup():
    """Download HF model and convert to CTranslate2 INT8 if not already done."""
    model_path = os.path.expanduser(NLLB_MODEL_PATH)
    if os.path.isdir(model_path):
        return True  # already set up

    logger.info("=" * 60)
    logger.info("NLLB model not found at %s", model_path)
    logger.info("Auto-downloading and converting (one-time, ~5 min, 594 MB)...")
    logger.info("=" * 60)

    parent = os.path.dirname(model_path)
    os.makedirs(parent, exist_ok=True)

    # Conversion needs torch to load the HF model
    try:
        import torch  # noqa: F401
    except ImportError:
        logger.warning(
            "PyTorch (torch) not found — cannot convert NLLB model.\n"
            "  Install with: uv tool install --reinstall "
            "'git+https://github.com/Mavis2103/browser-translator[nllb]'\n"
            "  Falling back to Ollama translation."
        )
        return False

    import ctranslate2
    import transformers

    hf_model = "facebook/nllb-200-distilled-600M"

    try:
        # Download tokenizer first (fast)
        logger.info("Downloading tokenizer...")
        tokenizer = transformers.AutoTokenizer.from_pretrained(
            hf_model, src_lang=NLLB_SRC_LANG
        )
    except Exception as e:
        logger.error("Failed to download tokenizer: %s", e)
        return False

    try:
        # Convert to CT2 INT8
        logger.info("Converting to CTranslate2 INT8 (this takes ~2 min)...")
        converter = ctranslate2.converters.TransformersConverter(
            hf_model,
            low_cpu_mem_usage=True,
        )
        converter.convert(
            output_dir=model_path,
            quantization="int8",
            force=True,
        )
    except Exception as e:
        logger.error("Failed to convert NLLB to CT2: %s", e)
        # Clean up only if nothing was written
        if not os.path.isdir(model_path):
            pass
        elif not any(f.endswith(".bin") for f in os.listdir(model_path)):
            shutil.rmtree(model_path, ignore_errors=True)
        return False

    try:
        # Save tokenizer files alongside CT2 model
        tok_dir = os.path.join(model_path, "tokenizer")
        tokenizer.save_pretrained(tok_dir)
        logger.info("NLLB model ready at %s (594 MB INT8)", model_path)
        return True
    except Exception as e:
        logger.warning("Failed to save tokenizer (CT2 model OK): %s", e)
        return True  # CT2 model still works


def _ensure_model():
    """One-time check: return True if NLLB model is ready."""
    global _availability
    if _availability is not None:
        return _availability

    if not _check_installed():
        logger.warning(
            "ctranslate2 not installed. Add --with ctranslate2 --with transformers "
            "to install. Falling back to Ollama."
        )
        _availability = False
        return False

    if _auto_setup():
        _availability = True
        return True

    _availability = False
    return False


def _load_model():
    """Lazy-load NLLB model via CTranslate2."""
    global _translator, _tokenizer
    if _translator is not None:
        return _translator, _tokenizer

    if not _ensure_model():
        raise RuntimeError("NLLB model not available")

    import ctranslate2
    import transformers

    model_path = os.path.expanduser(NLLB_MODEL_PATH)

    logger.info("Loading NLLB CT2 INT8 model from %s ...", model_path)
    _translator = ctranslate2.Translator(model_path, device="cpu")

    # Tokenizer files live in a tokenizer/ subfolder (separate from CT2 config)
    tok_path = os.path.join(model_path, "tokenizer")
    if not os.path.isdir(tok_path):
        tok_path = model_path
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

    Auto-downloads and converts the model on first call.
    Falls back silently if not available.

    Args:
        text: English text to translate.
        source_lang: Ignored (NLLB is fixed EN→VI, auto-detects).
        target_lang: Ignored (fixed VI).
        model: Ignored (NLLB model is fixed).

    Returns:
        Translated Vietnamese text, or None on failure/callers to fall back.
    """
    if not text or not text.strip():
        return ""

    try:
        translator, tokenizer = _load_model()
    except (RuntimeError, Exception):
        return None

    try:
        tokens = tokenizer.tokenize(text)
        tokens = [tokenizer.bos_token] + tokens[:510] + [tokenizer.eos_token]

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
            return None

        logger.debug("NLLB translation: %r → %r", text[:50], translated[:50])
        return translated

    except Exception as e:
        logger.debug("NLLB translate failed for %r: %s", text[:50], e)
        return None
