#!/usr/bin/env bash
# Browser Translator - Quick Start Script
# Starts the Python backend + launches Chrome with the extension.
# Auto-starts Ollama if not already running.

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

export OLLAMA_LIBRARY_PATH="${OLLAMA_LIBRARY_PATH:-$HOME/.local/lib/ollama}"
export PATH="$HOME/.local/bin:$PATH"

OLLAMA_BIN="${OLLAMA_BIN:-$HOME/.local/bin/ollama}"
[ -x "$OLLAMA_BIN" ] || OLLAMA_BIN="$(command -v ollama 2>/dev/null || true)"
[ -x "$OLLAMA_BIN" ] || OLLAMA_BIN=""

echo "================================================"
echo "  Browser Translator — Starting..."
echo "================================================"

# 1. Ensure Ollama is running
if ! curl -s --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    if [ -n "$OLLAMA_BIN" ]; then
        echo "[*] Ollama not running. Starting it..."
        OLLAMA_LIBRARY_PATH="$OLLAMA_LIBRARY_PATH" nohup "$OLLAMA_BIN" serve \
            >"$HOME/.browser-translator-ollama.log" 2>&1 &
        OLLAMA_PID=$!
        echo "[+] Ollama PID: $OLLAMA_PID"

        # Wait for Ollama to become ready
        for i in $(seq 1 20); do
            if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
                echo "[+] Ollama is ready!"
                break
            fi
            sleep 1
        done
    else
        echo "[!] Ollama not running and not found in PATH."
        echo "    Run: OLLAMA_LIBRARY_PATH=$OLLAMA_LIBRARY_PATH ollama serve"
        exit 1
    fi
fi

# 2. Check if Qwen3.5 model is pulled
if ! curl -s http://localhost:11434/api/tags 2>/dev/null | grep -q "qwen3.5"; then
    echo "[!] Qwen3.5:4b not pulled. Run: $OLLAMA_BIN pull qwen3.5:4b"
    exit 1
fi

# 3. Check Python deps
echo "[*] Checking Python dependencies..."
python3 -c "import moonshine_voice; import paddleocr; import uvicorn" 2>/dev/null || {
    echo "[!] Missing packages. Run: ./scripts/install.sh"
    exit 1
}

# 4. Launch backend
echo "[*] Starting backend on port 8765..."
PYTHONPATH="$DIR" OLLAMA_LIBRARY_PATH="$OLLAMA_LIBRARY_PATH" \
    nohup python3 -m uvicorn backend.main:app \
        --host 0.0.0.0 --port 8765 --log-level info \
        >"$HOME/.browser-translator.log" 2>&1 &
BACKEND_PID=$!
echo "[+] Backend PID: $BACKEND_PID (log: ~/.browser-translator.log)"

# Wait for backend to be ready
for i in $(seq 1 30); do
    if curl -s http://localhost:8765/api/health >/dev/null 2>&1; then
        echo "[+] Backend is ready!"
        break
    fi
    sleep 2
done

# Sanity check: backend actually answers with status=ok
STATUS=$(curl -s http://localhost:8765/api/health | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
if [ "$STATUS" != "ok" ]; then
    echo "[!] Backend didn't reach status=ok. Check log: tail ~/.browser-translator.log"
    exit 1
fi

# 5. Launch Chrome with extension
EXTENSION_PATH="$DIR/extension"
echo "[*] Launching Chrome with extension: $EXTENSION_PATH"

nohup google-chrome \
    --remote-debugging-port=9222 \
    --load-extension="$EXTENSION_PATH" \
    --disable-gpu \
    --no-first-run \
    --disable-dev-shm-usage \
    --new-window \
    "chrome://newtab" \
    >"$HOME/.browser-translator-chrome.log" 2>&1 &
CHROME_PID=$!
echo "[+] Chrome PID: $CHROME_PID (log: ~/.browser-translator-chrome.log)"

echo ""
echo "================================================"
echo "  ✅ Ready!"
echo ""
echo "  Backend:    http://localhost:8765"
echo "  Ollama:     http://localhost:11434"
echo "  CDP:        http://localhost:9222"
echo "  Logs:       ~/.browser-translator{,-ollama,-chrome}.log"
echo ""
echo "  → Click the 🌐 icon in Chrome toolbar to open"
echo "    the translation control panel."
echo ""
echo "  To stop everything:"
echo "    kill $BACKEND_PID $CHROME_PID ${OLLAMA_PID:-}"
echo "================================================"

