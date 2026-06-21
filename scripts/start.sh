#!/usr/bin/env bash
# Browser Translator — Quick Start Script (legacy fallback)
#
# NOTE: The recommended way to start the backend is:
#   browser-translator start
# (installed via `uv tool install -e .` from the repo root)
#
# This script is kept as a fallback for users who prefer shell scripts.
# It auto-detects Ollama location and starts the backend on :8765.
# Unlike browser-translator start, it also launches Chrome with the extension loaded.

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

# ── Auto-detect Ollama ──────────────────────────────────────────
# Strategy: PATH first, then common locations.
# OLLAMA_LIBRARY_PATH is derived from the binary location (only needed for
# custom/manual installs from tar.zst). Standard installs (official script,
# apt, etc.) set it automatically and don't need this.
#
# Users can override with env vars:
#   OLLAMA_BIN=/path/to/ollama    (binary)
#   OLLAMA_LIBRARY_PATH=/path/    (override auto-detection)

OLLAMA_BIN="${OLLAMA_BIN:-}"
if [ -z "$OLLAMA_BIN" ]; then
    OLLAMA_BIN="$(command -v ollama 2>/dev/null || true)"
fi
if [ -z "$OLLAMA_BIN" ]; then
    for candidate in "$HOME/.local/bin/ollama" "$HOME/bin/ollama" "/usr/local/bin/ollama" "/usr/bin/ollama"; do
        if [ -x "$candidate" ]; then
            OLLAMA_BIN="$candidate"
            break
        fi
    done
fi

# Derive OLLAMA_LIBRARY_PATH from binary location (pattern: <prefix>/bin/ollama → <prefix>/lib/ollama)
if [ -n "$OLLAMA_BIN" ] && [ -z "${OLLAMA_LIBRARY_PATH:-}" ]; then
    prefix="$(dirname "$(dirname "$OLLAMA_BIN")")"
    if [ -d "$prefix/lib/ollama" ]; then
        export OLLAMA_LIBRARY_PATH="$prefix/lib/ollama"
    fi
fi
export OLLAMA_LIBRARY_PATH="${OLLAMA_LIBRARY_PATH:-}"

# Ensure PATH includes common ollama location for detection
if [ -n "$OLLAMA_BIN" ]; then
    export PATH="$(dirname "$OLLAMA_BIN"):$PATH"
fi

echo "================================================"
echo "  Browser Translator — Starting..."
echo "================================================"

# 1. Ensure Ollama is running
if ! curl -s --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    if [ -n "$OLLAMA_BIN" ] && [ -x "$OLLAMA_BIN" ]; then
        echo "[*] Ollama not running. Starting it..."
        # shellcheck disable=SC2086
        nohup "$OLLAMA_BIN" serve >"$HOME/.browser-translator-ollama.log" 2>&1 &
        OLLAMA_PID=$!
        echo "[+] Ollama PID: $OLLAMA_PID"

        for i in $(seq 1 20); do
            if curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
                echo "[+] Ollama is ready!"
                break
            fi
            sleep 1
        done
    else
        echo "[!] Ollama not running and not found."
        echo "    Install: curl -fsSL https://ollama.com/install.sh | sh"
        echo "    Then:    ollama serve"
        exit 1
    fi
fi

# 2. Check model
if ! curl -s http://localhost:11434/api/tags 2>/dev/null | grep -q "qwen3.5"; then
    echo "[!] qwen3.5 model not pulled."
    if [ -n "$OLLAMA_BIN" ]; then
        echo "    Run: $OLLAMA_BIN pull qwen3.5:0.8b"
    else
        echo "    Run: ollama pull qwen3.5:0.8b"
    fi
    exit 1
fi

# 3. Check Python deps
echo "[*] Checking Python dependencies..."
python3 -c "import moonshine_voice; import uvicorn" 2>/dev/null || {
    echo "[!] Missing packages. Install: uv tool install -e .  or  ./scripts/install.sh"
    exit 1
}

# 4. Launch backend (PYTHONPATH not needed — backend/ is a proper package now)
echo "[*] Starting backend on port 8765..."
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

# Sanity check
STATUS=$(curl -s http://localhost:8765/api/health | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
if [ "$STATUS" != "ok" ]; then
    echo "[!] Backend didn't reach status=ok. Check log: tail ~/.browser-translator.log"
    exit 1
fi

# 5. Launch Chrome with extension
EXTENSION_PATH="$DIR/backend/extension"
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
