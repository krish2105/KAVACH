"""`kavach-meetings` — see what the call detector actually sees.

Exists because Phase 15's heuristics can only be honestly verified against a
real call, and "it should work" is not evidence. Run this, join a meeting, and
it prints what it detected and how confident it is.
"""

from __future__ import annotations

import argparse
import time

from .meetings import POLL_SECONDS, detect_call, visible_windows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print what the call detector sees, live.")
    parser.add_argument("--once", action="store_true",
                        help="check once and exit")
    parser.add_argument("--interval", type=float, default=POLL_SECONDS)
    args = parser.parse_args(argv)

    print("  Watching for calls. Join one — Ctrl-C to stop.\n")
    last = object()
    try:
        while True:
            windows = visible_windows()
            found = detect_call(windows)
            if not windows:
                print("  ⚠ no windows readable — Screen Recording permission?")

            key = (found.app, found.confidence) if found else None
            if key != last:
                stamp = time.strftime("%H:%M:%S")
                if found:
                    mark = "✓" if found.confidence == "high" else "~"
                    print(f"  {stamp}  {mark} IN A CALL — {found.app} "
                          f"({found.confidence} confidence)")
                    print(f"            window: {found.title!r}")
                    print(f"            → wake word would be SUSPENDED")
                else:
                    print(f"  {stamp}  · no call detected "
                          f"({len(windows)} windows)")
                last = key

            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n  stopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
