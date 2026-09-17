"""Read the local API without putting its bearer token in shell history."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="/v1/status")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    path = urlsplit(args.path)
    if path.scheme or path.netloc or not path.path.startswith("/v1/") or "\\" in args.path:
        parser.error("Supply a local /v1/ API path, not an external URL.")
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535.")
    state = Path(os.environ.get("IS_MU_STATE_DIR", ".ismu-state"))
    token = (state / "api-token").read_text().strip()
    with requests.Session() as session:
        session.trust_env = False  # Keep localhost requests out of ambient HTTP proxies.
        response = session.get(
            f"http://127.0.0.1:{args.port}{args.path}",
            headers={"Authorization": "Bearer " + token},
            timeout=(5, 600),
            allow_redirects=False,
        )
    result = response.json()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    complete = not isinstance(result, dict) or result.get("complete", True)
    return 0 if response.status_code == 200 and complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
