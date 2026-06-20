#!/usr/bin/env bash
# Launch Chrome with the Browser Translator extension loaded
# Use this when the backend is already running.

set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
EXTENSION_PATH="$DIR/extension"

if [ ! -f "$EXTENSION_PATH/manifest.json" ]; then
    echo "[!] Extension not found at $EXTENSION_PATH"
    exit 1
fi

echo "[*] Launching Chrome with extension: $EXTENSION_PATH"

google-chrome \
    --remote-debugging-port=9222 \
    --load-extension="$EXTENSION_PATH" \
    --disable-gpu \
    --no-first-run \
    --new-window \
    "chrome://newtab" 2>/dev/null &

echo "[+] Chrome launched. Extension loaded."
echo "    CDP port: 9222"
echo "    Click the extension icon to open the control panel."
