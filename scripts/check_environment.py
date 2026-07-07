from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
ENV_FILE = BACKEND_DIR / ".env"
LLAMA_HEALTH_URL = "http://127.0.0.1:18181/health"


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
BACKEND_HEALTH_URL = f"http://{BACKEND_HOST}:{BACKEND_PORT}/health"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    required: bool = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Window Copilot environment checker.")
    parser.add_argument(
        "--static",
        action="store_true",
        help="Only check local files and commands; do not probe ports or services.",
    )
    parser.add_argument(
        "--for-start",
        action="store_true",
        help="Check everything required before scripts/start_dev.cmd starts the app.",
    )
    args = parser.parse_args()

    results = static_checks()
    if not args.static:
        results.extend(runtime_checks(for_start=args.for_start))

    print_report(results)
    failed = [item for item in results if item.required and not item.ok]
    return 1 if failed else 0


def static_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []
    checks.append(check_python())
    checks.append(check_command("uv", "Python dependency/environment manager."))
    checks.extend(
        [
            check_path("Backend project", BACKEND_DIR / "pyproject.toml"),
            check_path("Desktop floating window", PROJECT_ROOT / "apps" / "desktop-floating-window" / "desktop_floating_window.py"),
            check_path("Desktop start script", PROJECT_ROOT / "apps" / "desktop-floating-window" / "start_desktop_window.cmd"),
            check_path("Mascot asset", PROJECT_ROOT / "assets" / "mascot" / "rive_import" / "mascot_base_idle.png"),
            check_path("Window analysis prompt", PROJECT_ROOT / "experiments" / "prompts" / "analyze_window_v2.txt"),
            check_path("llama-server.exe", PROJECT_ROOT / "runtime" / "llama.cpp" / "llama-server.exe"),
            check_path("MiniCPM-V F16 model", PROJECT_ROOT / "runtime" / "models" / "minicpm-v4.6" / "MiniCPM-V-4_6-F16.gguf"),
            check_path("MiniCPM-V mmproj", PROJECT_ROOT / "runtime" / "models" / "minicpm-v4.6" / "mmproj-model-f16.gguf"),
        ]
    )
    return checks


def runtime_checks(*, for_start: bool) -> list[CheckResult]:
    checks: list[CheckResult] = []
    checks.append(check_backend_port(for_start=for_start))
    checks.append(check_llama_port())
    return checks


def check_python() -> CheckResult:
    version = sys.version_info
    ok = version >= (3, 13)
    detail = f"{version.major}.{version.minor}.{version.micro}"
    return CheckResult("Python >= 3.13", ok, detail)


def check_command(command: str, description: str) -> CheckResult:
    path = shutil.which(command)
    return CheckResult(command, path is not None, path or f"not found; {description}")


def check_path(name: str, path: Path, *, required: bool = True) -> CheckResult:
    exists = path.exists()
    detail = str(path) if exists else f"missing: {path}"
    return CheckResult(name, exists, detail, required=required)


def check_backend_port(*, for_start: bool) -> CheckResult:
    ok, _detail = http_ok(BACKEND_HEALTH_URL, timeout=0.8)
    name = f"FastAPI {BACKEND_HOST}:{BACKEND_PORT}"
    if ok:
        return CheckResult(name, True, "already running")
    if port_open(BACKEND_HOST, BACKEND_PORT):
        return CheckResult(
            name,
            False,
            "port is occupied but /health is not responding",
        )
    return CheckResult(
        name,
        True,
        "not running; start_dev will start it" if for_start else "not running",
        required=False,
    )


def check_llama_port() -> CheckResult:
    ok, _detail = http_ok(LLAMA_HEALTH_URL, timeout=0.8)
    if ok:
        return CheckResult("llama-server 127.0.0.1:18181", True, "already running")
    if port_open("127.0.0.1", 18181):
        return CheckResult(
            "llama-server 127.0.0.1:18181",
            False,
            "port is occupied but /health is not responding",
        )
    return CheckResult(
        "llama-server 127.0.0.1:18181",
        True,
        "not running; backend will start it on first analysis",
        required=False,
    )


def http_ok(url: str, *, timeout: float) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300, f"HTTP {response.status}"
    except (OSError, urllib.error.URLError) as exc:
        return False, str(exc)


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def print_report(results: list[CheckResult]) -> None:
    print("Local Window Copilot environment check")
    print("=" * 45)
    for item in results:
        status = "OK" if item.ok else ("FAIL" if item.required else "WARN")
        print(f"[{status}] {item.name}")
        print(f"      {item.detail}")


if __name__ == "__main__":
    raise SystemExit(main())
