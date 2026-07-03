from __future__ import annotations

import json
import sys
from urllib import request


BACKEND_STATE_URL = "http://127.0.0.1:18080/api/assistant/state"
VALID_STATES = {"idle", "observing", "analyzing", "privacy", "error"}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in VALID_STATES:
        print("Usage: python set_state.py idle|observing|analyzing|privacy|error")
        return 2

    payload = json.dumps(
        {"state": sys.argv[1], "reason": "set-state-script"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = request.Request(
        BACKEND_STATE_URL,
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
