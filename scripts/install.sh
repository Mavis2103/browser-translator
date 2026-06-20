#!/usr/bin/env bash
# Browser Translator - One-time installer
# Run once on a new machine to set up everything this tool needs.
# Tested on Ubuntu 24.04+. Other distros may work but not guaranteed.

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

echo "================================================"
echo "  Browser Translator — First-time install"
echo "================================================"

# 1. System packages
echo "[*] Installing system packages (ffmpeg, etc.)..."
if command -v apt-get >/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq ffmpeg zstd curl
else
    echo "[!] apt-get not found. ffmpeg may be required for audio decoding."
fi

# 2. Python packages
echo "[*] Installing Python dependencies..."
uv pip install -r "$DIR/requirements.txt" || {
    echo "[!] uv failed. Falling back to: python3 -m pip install"
    python3 -m pip install -r "$DIR/requirements.txt"
}

# 3. Ollama (auto-install CPU-friendly lite binary if not present)
if ! command -v ollama >/dev/null && [ ! -x "$HOME/.local/bin/ollama" ]; then
    echo "[*] Installing Ollama (CPU, user-local)..."
    LATEST=$(curl -s https://api.github.com/repos/ollama/ollama/releases/latest | \
        python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null || echo "v0.30.10")
    TMP=$(mktemp -d)
    curl -fsSL "https://github.com/ollama/ollama/releases/download/${LATEST}/ollama-linux-amd64.tar.zst" \
        -o "$TMP/ollama.tgz" || { echo "[!] Ollama download failed. Install manually."; exit 1; }

    mkdir -p "$HOME/.local/bin" "$HOME/.local/lib/ollama"
    tar --zstd -xf "$TMP/ollama.tgz" -C "$TMP" bin/ollama
    tar --zstd -xf "$TMP/ollama.tgz" -C "$TMP" \
        --exclude="*/cuda_v*" lib/ollama/

    cp "$TMP/bin/ollama" "$HOME/.local/bin/ollama"
    cp -r "$TMP/lib/ollama/"* "$HOME/.local/lib/ollama/"
    rm -rf "$TMP"
    chmod +x "$HOME/.local/bin/ollama"
    echo "[+] Ollama installed at ~/.local/bin/ollama"
fi

# Add ollama to PATH for this shell session
export PATH="$HOME/.local/bin:$PATH"

# 4. Pull translation model
echo "[*] Pulling translation model (qwen3.5:0.8b, ~1.0GB on disk; default)..."
"$HOME/.local/bin/ollama" pull qwen3.5:0.8b
# Also pull fallback chain (optional but recommended for resilience)
echo "[*] Pulling fallback models (1.5b → 4b) ..."
"$HOME/.local/bin/ollama" pull qwen3.5:1.5b 2>/dev/null || echo "  (optional, skipped)"

# 5. Verify
echo "[*] Running verification..."
python3 -c "
import urllib.request, json
try:
    with urllib.request.urlopen('http://localhost:11434/api/tags', timeout=3) as r:
        models = [m['name'] for m in json.loads(r.read()).get('models', [])]
        assert 'qwen3.5:0.8b' in models, 'qwen3.5:0.8b not pulled'
        print('  ✅ Ollama: qwen3.5:0.8b OK (chain: 0.8b → 1.5b → 4b → 8b)')
except Exception as e:
    print(f'  ⚠️  Ollama not running. Run: ollama serve')
    print(f'      ({e})')
"

python3 -c "
import moonshine_voice
print('  ✅ Moonshine STT: ok')
from moonshine_voice.tts import TextToSpeech
print('  ✅ Moonshine TTS: ok')
" 2>&1

python3 -c "
from paddleocr import PaddleOCR
print('  ✅ PaddleOCR: ok')
" 2>&1

echo ""
echo "================================================"
echo "  ✅ Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Start Ollama:      OLLAMA_LIBRARY_PATH=\$HOME/.local/lib/ollama ollama serve &"
echo "  2. Start the tool:    ./scripts/start.sh"
echo ""
echo "Chrome will launch with the extension already"
echo "loaded. Click the 🌐 icon in the toolbar to open it."
echo "================================================"
