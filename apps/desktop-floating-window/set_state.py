from __future__ import annotations

import json
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
STATE_FILE = APP_DIR / "state_bridge.json"
VALID_STATES = {"idle", "observing", "analyzing", "privacy", "error"}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in VALID_STATES:
        print("Usage: python set_state.py idle|observing|analyzing|privacy|error")
        return 2

    STATE_FILE.write_text(
        json.dumps({"state": sys.argv[1]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"state={sys.argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
