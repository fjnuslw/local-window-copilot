from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
ENV_FILE = BACKEND_DIR / ".env"


def read_env_value(key: str, default: str) -> str:
    value = os.environ.get(key, "").strip()
    if value:
        return value
    if ENV_FILE.exists():
        for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() != key:
                continue
            value = value.strip().strip('"').strip("'")
            return value or default
    return default


def read_backend_port() -> int:
    raw_port = read_env_value("LWC_BACKEND_PORT", "18081")
    try:
        return int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"Invalid LWC_BACKEND_PORT={raw_port!r} in {ENV_FILE}") from exc


BACKEND_HOST = read_env_value("LWC_BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = read_backend_port()
BACKEND_BASE_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}"
BACKEND_HEALTH_URL = f"{BACKEND_BASE_URL}/health"


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
        print(f"FastAPI backend is already running on {BACKEND_HOST}:{BACKEND_PORT}.")
    else:
        start_backend()
        if not wait_for_backend(timeout_seconds=45):
            print("FastAPI backend did not become ready within 45 seconds.")
            print("Check the backend terminal window for the exact error.")
            return 1

    print("[3/4] Starting desktop floating window...")
    start_desktop_window()

    print("[4/4] Development environment is ready.")
    print(f"Backend docs: {BACKEND_BASE_URL}/docs")
    print(f"WebUI 控制台: {BACKEND_BASE_URL}/webui/")
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
    python_exe = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
    runner = str(python_exe) if python_exe.exists() else sys.executable
    args = [
        runner,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        BACKEND_HOST,
        "--port",
        str(BACKEND_PORT),
        "--reload",
        "--no-access-log",
    ]
    env = os.environ.copy()
    env.setdefault("LWC_BACKEND_HOST", BACKEND_HOST)
    env.setdefault("LWC_BACKEND_PORT", str(BACKEND_PORT))
    print("Starting backend:", subprocess.list2cmdline(args))
    subprocess.Popen(
        args,
        cwd=BACKEND_DIR,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        env=env,
    )


def start_desktop_window() -> None:
    script = PROJECT_ROOT / "apps" / "desktop-floating-window" / "start_desktop_window.cmd"
    env = os.environ.copy()
    env.setdefault("LWC_BACKEND_BASE_URL", BACKEND_BASE_URL)
    subprocess.Popen(
        ["cmd", "/c", str(script)],
        cwd=PROJECT_ROOT,
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        env=env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
