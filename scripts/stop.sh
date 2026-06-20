#!/usr/bin/env bash
# Browser Translator - Stop everything (backend, Chrome with extension, Ollama)

# Stop backend (uvicorn)
pkill -f "uvicorn backend.main:app" 2>/dev/null && echo "[+] Backend stopped" || true

# Stop Chrome with our extension loaded
pkill -f "google-chrome.*load-extension=.*/extension" 2>/dev/null && echo "[+] Chrome stopped" || true

# Optionally also stop Ollama (uncomment if you want to)
# pkill -f "ollama serve" 2>/dev/null && echo "[+] Ollama stopped" || true

echo "Done."
