"""Browser Translator CLI — uv tool entry point.

Usage:
    browser-translator start          Start the backend (foreground, Ctrl+C to stop)
    browser-translator start --daemon Start in background, use 'stop' to kill
    browser-translator stop           Stop a daemonized backend
    browser-translator status         Health check
    browser-translator build-ext      Package the Chrome extension as a .zip
    browser-translator install-deps   Install system + Python dependencies
"""

import argparse
import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── helpers ──────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
BT_DIR = DATA_HOME / "browser-translator"
BT_DIR.mkdir(parents=True, exist_ok=True)

PID_FILE = BT_DIR / "backend.pid"
LOG_FILE = BT_DIR / "backend.log"


def _find_ollama():
    """Locate the ollama binary — check PATH first, then common locations."""
    ollama = shutil.which("ollama")
    if ollama:
        return ollama
    for candidate in (
        Path.home() / ".local" / "bin" / "ollama",
        Path("/usr/local/bin/ollama"),
        Path("/usr/bin/ollama"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _ollama_running():
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as r:
            return True
    except Exception:
        return False


def _check_ollama_or_die():
    if _ollama_running():
        return
    ollama_bin = _find_ollama()
    if ollama_bin:
        print("! Ollama is installed but not running.")
        print(f"  Start it: {ollama_bin} serve")
    else:
        print("! Ollama not found.")
        print("  Install: curl -fsSL https://ollama.com/install.sh | sh")
    sys.exit(1)


def _backend_running(host: str, port: int):
    try:
        req = urllib.request.Request(f"http://{host}:{port}/api/health")
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read().decode()).get("status") == "ok"
    except Exception:
        return False


# ── commands ─────────────────────────────────────────────────────

def cmd_start(args):
    """Start the backend server."""
    # Already running?
    if _backend_running(args.host, args.port):
        print(f"✓ Backend already running at http://{args.host}:{args.port}")
        return 0

    # Ollama check
    _check_ollama_or_die()

    # Foreground (default) — just hand over to uvicorn
    if not args.daemon:
        print(f"Starting backend on http://{args.host}:{args.port} ...")
        print("Press Ctrl+C to stop.")
        sys.stdout.flush()

        import uvicorn
        from backend.main import app

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            reload=args.reload,
        )
        return 0

    # Daemon mode — background with PID file
    # Clean up stale PID
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)
            print(f"! Backend already running (PID {pid}). Use 'stop' first.")
            return 1
        except (ProcessLookupError, ValueError, OSError):
            PID_FILE.unlink(missing_ok=True)

    cmd = [
        sys.executable, "-m", "uvicorn",
        "backend.main:app",
        "--host", args.host,
        "--port", str(args.port),
        "--log-level", args.log_level,
    ]

    print(f"Starting backend in background on http://{args.host}:{args.port} ...")
    print(f"  PID file: {PID_FILE}")
    print(f"  Log file: {LOG_FILE}")

    with open(LOG_FILE, "w") as logf:
        proc = subprocess.Popen(
            cmd,
            stdout=logf,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )

    PID_FILE.write_text(str(proc.pid))

    # Wait up to 20s for readiness
    for _ in range(20):
        if _backend_running(args.host, args.port):
            print(f"✓ Backend ready (PID {proc.pid})")
            return 0
        time.sleep(1)

    # Health check — maybe loaded slowly; check once more
    if _backend_running(args.host, args.port):
        print(f"✓ Backend ready (PID {proc.pid})")
        return 0

    print("! Backend did not become ready within 20s. Log tail:")
    _tail_log()
    return 1


def cmd_stop(args):
    """Stop the daemonized backend."""
    if not PID_FILE.exists():
        print("No PID file found. Backend is not running in daemon mode.")
        return 0

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        # Wait up to 5s for graceful shutdown
        for _ in range(10):
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except ProcessLookupError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
        PID_FILE.unlink(missing_ok=True)
        print(f"✓ Backend (PID {pid}) stopped.")
    except (ProcessLookupError, ValueError, OSError) as e:
        print(f"! Could not stop: {e}")
        PID_FILE.unlink(missing_ok=True)
        return 1
    return 0


def cmd_status(args):
    """Health check."""
    url = f"http://{args.host}:{args.port}/api/health"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        print(f"✗ Backend not reachable at {url}")
        print(f"  {e}")
        return 1

    print(f"✓ Backend: {data.get('status', '?')}")
    print(f"  URL:   http://{args.host}:{args.port}")
    print(f"  Audio: {'active' if data.get('audio_capturing') else 'idle'}")
    print(f"  Clients: {data.get('audio_clients', 0)}")

    models = data.get("models", {})
    if models.get("stt"):
        print("  STT:   ✓ Moonshine")
    else:
        print("  STT:   ✗ not loaded")
    if models.get("tts"):
        print("  TTS:   ✓ Moonshine")
    else:
        print("  TTS:   ✗ not loaded")
    if models.get("ocr"):
        print("  OCR:   ✓ PaddleOCR")
    else:
        print("  OCR:   ✗ not loaded")
    print(f"  LLM:   {models.get('translation', '?')}")
    return 0


def cmd_build_ext(args):
    """Package the Chrome extension as a distributable .zip."""
    ext_dir = PROJECT_ROOT / "extension"
    if not ext_dir.is_dir():
        print(f"! Extension directory not found: {ext_dir}")
        print("  Run this from the browser-translator repository root.")
        return 1

    dist_dir = PROJECT_ROOT / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)

    from backend import __version__ as ver
    out_name = f"browser-translator-extension-v{ver}.zip"
    out_path = dist_dir / out_name

    # Zip it
    shutil.make_archive(
        str(out_path.with_suffix("")),  # strip .zip — make_archive adds it
        "zip",
        root_dir=str(ext_dir.parent),   # project root so paths are extension/...
        base_dir="extension",           # only the extension folder
    )

    # Account for make_archive adding .zip
    actual = dist_dir / out_name.replace(".zip", "") / ".." / out_name
    # simpler: just build the path we expect
    final = dist_dir / f"extension"  # make_archive creates this
    # Let me just use zipfile directly for clarity
    import zipfile
    final_zip = dist_dir / out_name
    with zipfile.ZipFile(final_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for fpath in ext_dir.rglob("*"):
            if fpath.is_file():
                arcname = str(fpath.relative_to(ext_dir.parent))
                zf.write(fpath, arcname)

    size_bytes = final_zip.stat().st_size
    if size_bytes < 1_000_000:
        size_str = f"{size_bytes / 1024:.1f} KB"
    else:
        size_str = f"{size_bytes / 1_000_000:.1f} MB"
    print(f"✓ Extension packaged: {final_zip} ({size_str})")
    print()
    print("To install in Chrome:")
    print("  1. Go to chrome://extensions")
    print("  2. Enable 'Developer mode' (top-right toggle)")
    print("  3. Click 'Load unpacked'")
    print(f"  4. Select: {ext_dir}")
    print()
    print("Or unzip and load the extension/ folder manually.")
    return 0


def cmd_install_deps(args):
    """Install system + Python dependencies."""
    print("=== System packages ===")
    if shutil.which("apt-get"):
        sys_pkgs = ["ffmpeg", "zstd", "curl"]
        print(f"Installing: {' '.join(sys_pkgs)}")
        subprocess.check_call(
            ["sudo", "apt-get", "update", "-qq"],
        )
        subprocess.check_call(
            ["sudo", "apt-get", "install", "-y", "-qq"] + sys_pkgs,
        )
    else:
        print("(apt-get not found. Ensure ffmpeg is installed manually.)")

    print()
    print("=== Python dependencies ===")
    # Detect uv vs pip
    uv = shutil.which("uv")
    if uv:
        subprocess.check_call([uv, "pip", "install", "-r",
                               str(PROJECT_ROOT / "requirements.txt")])
    else:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r",
             str(PROJECT_ROOT / "requirements.txt")],
        )

    print()
    print("=== Ollama ===")
    ollama = _find_ollama()
    if ollama:
        print(f"✓ Ollama found: {ollama}")
    else:
        print("! Ollama not installed.")
        print("  Install: curl -fsSL https://ollama.com/install.sh | sh")
        print("  Then pull the model: ollama pull qwen3.5:0.8b")
        return 1

    # Check model
    import urllib.request
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as r:
            models = json.loads(r.read().decode())
            names = [m["name"] for m in models.get("models", [])]
            if "qwen3.5:0.8b" in names:
                print(f"✓ Translation model qwen3.5:0.8b OK")
            else:
                print("! Pulling qwen3.5:0.8b (~1 GB)...")
                subprocess.check_call([ollama, "pull", "qwen3.5:0.8b"])
    except Exception:
        print("! Ollama not running. Run 'ollama serve' then try again.")

    print()
    print("=== Verify Python imports ===")
    for mod, name in [
        ("fastapi", "✓ fastapi"),
        ("uvicorn", "✓ uvicorn"),
        ("moonshine_voice", "✓ moonshine_voice"),
        ("piper_tts", "✓ piper-tts"),
        ("pydub", "✓ pydub"),
        ("aiohttp", "✓ aiohttp"),
    ]:
        try:
            __import__(mod)
            print(f"  {name}")
        except ImportError:
            print(f"  ✗ {name} — NOT FOUND")
    try:
        from paddleocr import PaddleOCR
        print("  ✓ paddleocr (optional)")
    except ImportError:
        print("  - paddleocr (optional — not installed)")
    try:
        import paddle
        print("  ✓ paddlepaddle (optional)")
    except ImportError:
        print("  - paddlepaddle (optional — not installed)")

    print()
    print("✓ Install complete.")
    return 0


# ── log tail ─────────────────────────────────────────────────────

def _tail_log(n: int = 20):
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().splitlines()[-n:]
        for line in lines:
            print(f"  | {line}")


# ── main ─────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="browser-translator",
        description="Local AI-powered browser translation tool.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = sub.add_parser("start", help="Start the backend server")
    p_start.add_argument("--host", default="0.0.0.0")
    p_start.add_argument("--port", type=int, default=8765)
    p_start.add_argument("--log-level", default="info",
                         choices=["debug", "info", "warning", "error"])
    p_start.add_argument("--daemon", action="store_true",
                         help="Run in background (use 'stop' to kill)")
    p_start.add_argument("--reload", action="store_true",
                         help="Auto-reload on code changes (dev only)")

    # stop
    sub.add_parser("stop", help="Stop the daemonized backend")

    # status
    p_status = sub.add_parser("status", help="Check backend health")
    p_status.add_argument("--host", default="0.0.0.0")
    p_status.add_argument("--port", type=int, default=8765)

    # build-ext
    sub.add_parser("build-ext", help="Package Chrome extension as .zip")

    # install-deps
    sub.add_parser("install-deps", help="Install system + Python deps")

    args = parser.parse_args(argv)

    if args.command == "start":
        return cmd_start(args)
    elif args.command == "stop":
        return cmd_stop(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "build-ext":
        return cmd_build_ext(args)
    elif args.command == "install-deps":
        return cmd_install_deps(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
