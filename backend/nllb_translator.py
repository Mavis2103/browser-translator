"""NLLB-200 translation engine via CTranslate2.

Provides fast INT8-quantized EN→VI translation for browser-translator.
Converter script (run once):
    ct2-transformers-converter \\
      --model facebook/nllb-200-distilled-600M \\
      --output_dir ~/.cache/browser-translator/nllb-600m-ct2-int8 \\
      --quantization int8 --force
"""

import logging
from typing import Optional

from .config import NLLB_MODEL_PATH, NLLB_SRC_LANG, NLLB_TGT_LANG

logger = logging.getLogger(__name__)

# Cache model instance (singleton, loaded once at process start)
_translator = None
_tokenizer = None


def _load_model():
    """Lazy-load NLLB model via CTranslate2."""
    global _translator, _tokenizer
    if _translator is not None:
        return _translator, _tokenizer

    import os
    import ctranslate2
    import transformers

    model_path = os.path.expanduser(NLLB_MODEL_PATH)

    if not os.path.isdir(model_path):
        logger.error(
            "NLLB model not found at %s. "
            "Run: ct2-transformers-converter --model facebook/nllb-200-distilled-600M "
            "--output_dir %s --quantization int8 --force",
            model_path, model_path,
        )
        raise FileNotFoundError(f"NLLB model not found: {model_path}")

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
    except (FileNotFoundError, Exception) as e:
        logger.error("NLLB model unavailable: %s", e)
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
        logger.error("NLLB translation failed: %s", e)
        return None
