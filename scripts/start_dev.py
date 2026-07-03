from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
BACKEND_HEALTH_URL = "http://127.0.0.1:18080/health"


def main() -> int:
    print("[1/4] Checking environment...")
    check = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "check_environment.py"), "--for-start"],
        cwd=PROJECT_ROOT,
    )
    if check.returncode != 0:
        print("Environment check failed. Fix the failed items above first.")
        return check.returncode

    print("[2/4] Starting FastAPI backend...")
    if backend_is_ready():
        print("FastAPI backend is already running on 127.0.0.1:18080.")
    else:
        start_backend()
        if not wait_for_backend(timeout_seconds=45):
            print("FastAPI backend did not become ready within 45 seconds.")
            print("Check the backend terminal window for the exact error.")
            return 1

    print("[3/4] Starting desktop floating window...")
    start_desktop_window()

    print("[4/4] Development environment is ready.")
    print("Backend docs: http://127.0.0.1:18080/docs")
    print("WebUI 控制台: http://127.0.0.1:18080/webui/")
    return 0


def backend_is_ready() -> bool:
    try:
        with urllib.request.urlopen(BACKEND_HEALTH_URL, timeout=0.8) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def wait_for_backend(*, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if backend_is_ready():
            return True
        time.sleep(1)
    return False


def start_backend() -> None:
    command = "uv run uvicorn app.main:app --host 127.0.0.1 --port 18080 --reload --no-access-log"
    subprocess.Popen(
        ["cmd", "/k", command],
        cwd=BACKEND_DIR,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def start_desktop_window() -> None:
    script = PROJECT_ROOT / "apps" / "desktop-floating-window" / "start_desktop_window.cmd"
    subprocess.Popen(
        ["cmd", "/c", str(script)],
        cwd=PROJECT_ROOT,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


if __name__ == "__main__":
    raise SystemExit(main())
