"""`kavach-ghost` — turn sensing off and on from a terminal.

Talks to the running loop over the local bridge rather than importing it: the
voice loop is a separate process, and a CLI that constructed its own GhostMode
would toggle an object nobody else can see — the mic would stay on and the
command would still print success.

Both directions are available here, unlike `POST /ghost`. This socket is bound
to 127.0.0.1, so reaching it means being at the machine, which is the condition
for being allowed to listen again.
"""

from __future__ import annotations

import argparse
import json
import sys

BRIDGE = "ws://127.0.0.1:8765"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Suspend or resume every KAVACH input.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--on", action="store_true",
                       help="ghost mode ON — mic, camera and logging off")
    group.add_argument("--off", action="store_true",
                       help="ghost mode OFF — resume sensing")
    parser.add_argument("--bridge", default=BRIDGE)
    args = parser.parse_args(argv)

    want = True if args.on else (False if args.off else None)

    try:
        from websockets.sync.client import connect
    except Exception:
        print("✗ websockets is not installed", file=sys.stderr)
        return 1

    try:
        with connect(args.bridge, open_timeout=4) as ws:
            ws.send(json.dumps({"cmd": "ghost", "on": want, "source": "cli"}))

            # Wait for a snapshot that actually REFLECTS the request.
            #
            # Reading the first snapshot reported the state from before the
            # command landed — the bridge streams continuously, so `--on`
            # cheerfully printed "OFF". Reporting the stale value is worse
            # than reporting nothing: it is a privacy control confirming the
            # opposite of what it did.
            seen = None
            for _ in range(40):
                try:
                    snapshot = json.loads(ws.recv(timeout=2))
                except Exception:
                    break
                if not isinstance(snapshot, dict) or "ghost" not in snapshot:
                    continue
                state = bool(snapshot["ghost"])
                if seen is None:
                    seen = state
                settled = state == want if want is not None else state != seen
                if settled:
                    print(f"  ghost mode {'ON — nothing is listening' if state else 'OFF — sensing resumed'}")
                    return 0
    except Exception as exc:
        print(f"✗ could not reach KAVACH at {args.bridge}: {exc}",
              file=sys.stderr)
        print("  is `uv run python -m kavach.voice` running?", file=sys.stderr)
        return 1

    print("✗ no state came back — ghost mode may not have changed",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
