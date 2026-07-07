from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib import request


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / "backend" / ".env"
VALID_STATES = {"idle", "observing", "analyzing", "privacy", "error"}


def _read_env_value(key: str, default: str) -> str:
    if key in os.environ and os.environ[key].strip():
        return os.environ[key].strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if not line.startswith(key + "="):
                continue
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or default
    return default


def backend_base_url() -> str:
    explicit = os.environ.get("LWC_BACKEND_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = _read_env_value("LWC_BACKEND_HOST", "127.0.0.1")
    port = _read_env_value("LWC_BACKEND_PORT", "18081")
    return f"http://{host}:{port}"


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in VALID_STATES:
        print("Usage: python set_state.py idle|observing|analyzing|privacy|error")
        return 2

    payload = json.dumps(
        {"state": sys.argv[1], "reason": "set-state-script"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        f"{backend_base_url()}/api/assistant/state",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=2) as response:
        response.read()
    print(f"state={sys.argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())