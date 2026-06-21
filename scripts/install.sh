#!/usr/bin/env bash
# Browser Translator — First-time Installer
#
# NOTE: The recommended way to install is:
#   uv tool install -e .
# Then: browser-translator install-deps
#
# This script is kept as a fallback. It auto-detects Ollama,
# installs system packages, Python deps, and pulls the model.

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

echo "================================================"
echo "  Browser Translator — First-time install"
echo "================================================"

# ── 1. Check / Install uv ───────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "[*] uv not found. Installing via pipx..."
    pip3 install --user uv 2>/dev/null || python3 -m pip install --user uv 2>/dev/null || {
        echo "[!] Could not install uv. Install it manually:"
        echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
        echo "    Then re-run this script."
        exit 1
    }
    export PATH="$HOME/.local/bin:$PATH"
fi
echo "[+] uv: $(uv --version 2>/dev/null || echo 'installed')"

# ── 2. System packages ──────────────────────────────────────────
echo ""
echo "[*] Installing system packages (ffmpeg, etc.)..."
if command -v apt-get >/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq ffmpeg zstd curl
elif command -v pacman >/dev/null; then
    sudo pacman -Sy --noconfirm ffmpeg zstd curl
elif command -v brew >/dev/null; then
    brew install ffmpeg zstd curl
else
    echo "[!] Package manager not detected. ffmpeg is required for audio decoding."
    echo "    Install it manually before proceeding."
fi

# ── 3. Python deps via uv ──────────────────────────────────────
echo ""
echo "[*] Installing Python dependencies (core)..."
uv pip install -r "$DIR/requirements.txt" 2>/dev/null || {
    echo "[!] uv pip install failed. Trying direct pip..."
    pip3 install -r "$DIR/requirements.txt" 2>/dev/null || {
        echo "[!] pip also failed. Try: uv tool install -e ."
        echo "    Or: pip install -e ."
        exit 1
    }
}

# ── 4. Ollama (auto-install if missing) ────────────────────────
echo ""
echo "[*] Checking Ollama..."

OLLAMA_BIN="$(command -v ollama 2>/dev/null || true)"
if [ -z "$OLLAMA_BIN" ]; then
    for candidate in "$HOME/.local/bin/ollama" "/usr/local/bin/ollama" "/usr/bin/ollama"; do
        if [ -x "$candidate" ]; then
            OLLAMA_BIN="$candidate"
            break
        fi
    done
fi

if [ -z "$OLLAMA_BIN" ]; then
    echo "[*] Ollama not found. Installing via official script..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "[+] Ollama installed via official installer."
    OLLAMA_BIN="$(command -v ollama || true)"
else
    echo "[+] Ollama found: $OLLAMA_BIN"
fi

# If still not found, try manual download (CPU-only tarball)
if [ -z "$OLLAMA_BIN" ] || [ ! -x "$OLLAMA_BIN" ]; then
    echo "[*] Offline install from tarball..."
    LATEST=$(curl -s https://api.github.com/repos/ollama/ollama/releases/latest | \
        python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null || echo "v0.30.10")
    TMP=$(mktemp -d)
    curl -fsSL "https://github.com/ollama/ollama/releases/download/${LATEST}/ollama-linux-amd64.tar.zst" \
        -o "$TMP/ollama.tgz" || { echo "[!] Download failed. Install manually."; rm -rf "$TMP"; exit 1; }

    mkdir -p "$HOME/.local/bin"
    tar --zstd -xf "$TMP/ollama.tgz" -C "$TMP" bin/ollama
    cp "$TMP/bin/ollama" "$HOME/.local/bin/ollama"
    rm -rf "$TMP"
    chmod +x "$HOME/.local/bin/ollama"
    OLLAMA_BIN="$HOME/.local/bin/ollama"
    echo "[+] Ollama installed at $OLLAMA_BIN"
fi

export PATH="$(dirname "$OLLAMA_BIN"):$PATH"

# ── 5. Pull translation models ──────────────────────────────────
echo ""
echo "[*] Pulling translation model (qwen3.5:0.8b, ~1.0 GB)..."
"$OLLAMA_BIN" pull qwen3.5:0.8b
echo "[*] Pulling optional fallback..."
"$OLLAMA_BIN" pull qwen3.5:1.5b 2>/dev/null || echo "  (optional, skipped)"

# ── 6. Verify Python imports ────────────────────────────────────
echo ""
echo "[*] Verifying installation..."

python3 -c "
import moonshine_voice
print('  ✅ moonshine_voice')
from moonshine_voice.tts import TextToSpeech
print('  ✅ Moonshine TTS')
" 2>&1

python3 -c "
try:
    from paddleocr import PaddleOCR
    print('  ✅ PaddleOCR (optional) ✓')
except ImportError:
    print('  - PaddleOCR (optional) — install with: pip install paddleocr paddlepaddle')
" 2>&1

echo ""
echo "================================================"
echo "  ✅ Installation complete!"
echo ""
echo "Quick start (recommended):"
echo "  1. uv tool install -e $DIR"
echo "  2. Start Ollama:        ollama serve &"
echo "  3. Start the backend:   browser-translator start"
echo "  4. Load extension in Chrome:"
echo "     → chrome://extensions → Developer mode"
echo "     → Load unpacked → select $DIR/backend/extension"
echo ""
echo "Alternative (legacy):    ./scripts/start.sh"
echo "================================================"
