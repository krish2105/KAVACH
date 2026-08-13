"""`kavach-export` — write the rolling session buffer to a file.

The buffer lives in the running loop's memory, so this asks it over the local
bridge rather than constructing its own recorder — a fresh SessionRecorder in
this process would be empty, and the command would cheerfully export nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BRIDGE = "ws://127.0.0.1:8765"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export the last ~15 minutes of transcript and actions.")
    parser.add_argument("path", nargs="?", default=None,
                        help="where to write (default: ~/.kavach/exports/)")
    parser.add_argument("--bridge", default=BRIDGE)
    args = parser.parse_args(argv)

    try:
        from websockets.sync.client import connect
    except Exception:
        print("✗ websockets is not installed", file=sys.stderr)
        return 1

    try:
        with connect(args.bridge, open_timeout=4) as ws:
            ws.send(json.dumps({"cmd": "export",
                                "path": str(args.path) if args.path else None}))
            for _ in range(20):
                try:
                    message = json.loads(ws.recv(timeout=3))
                except Exception:
                    break
                if message.get("export") is not None:
                    result = message["export"]
                    if result.get("error"):
                        print(f"✗ {result['error']}", file=sys.stderr)
                        return 1
                    print(f"  {result['entries']} entries → {result['path']}")
                    return 0
    except Exception as exc:
        print(f"✗ could not reach KAVACH at {args.bridge}: {exc}",
              file=sys.stderr)
        print("  is `uv run python -m kavach.voice` running?", file=sys.stderr)
        return 1

    print("✗ no answer from KAVACH", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
