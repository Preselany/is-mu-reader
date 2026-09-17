"""Collect private data; run after `ismu login` using the same state directory."""

import json
import os
from pathlib import Path

from ismu import AuthRequired, Client, ISMUError
from ismu.transport import private_write


def main():
    state = Path(os.environ.get("IS_MU_STATE_DIR", ".ismu-state"))
    try:
        snapshot = Client(state).snapshot()
        destination = state / "snapshot.json"
        private_write(destination, json.dumps(snapshot, ensure_ascii=False, indent=2).encode())
    except (ISMUError, OSError) as exc:
        print(f"Collection failed: {exc}")
        return 2 if isinstance(exc, AuthRequired) else 1
    print(f"Saved {len(snapshot['courses'])} courses to {destination}")
    if not snapshot["complete"]:
        print(
            f"Partial collection: {len(snapshot['errors'])} resource errors; inspect the private file."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
