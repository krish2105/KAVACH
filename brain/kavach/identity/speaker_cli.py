"""`kavach-speaker` — turn speaker verification on or off.

    uv run kavach-speaker            # status
    uv run kavach-speaker off        # any voice can command KAVACH
    uv run kavach-speaker on

Before this, the only way to stop KAVACH gating on your voice was to delete the
voiceprint, so lending the machine to someone for five minutes cost you a
re-enrolment. The realistic response to that is to never turn it off — or to
never enrol, which is worse.

Off is deliberately loud. It is the setting that decides whether KAVACH answers
to one person or to the room, and §7 exists because of the second one.
"""

from __future__ import annotations

import argparse
import sys

from ..killswitch.log import ActionLog
from .voiceprint import Voiceprint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Turn speaker verification on or off.")
    parser.add_argument("action", nargs="?", default="status",
                        choices=["status", "on", "off"])
    args = parser.parse_args(argv)

    vp = Voiceprint()

    if not vp.is_enrolled:
        print("✗ no voiceprint enrolled — there is nothing to verify against.")
        print("  uv run kavach-enrol")
        return 1

    if args.action != "status":
        # A security state change, recorded like any other action.
        log = ActionLog()
        (vp.enable if args.action == "on" else vp.disable)(log=log)

    print()
    if vp.gating:
        print(f"  ✓ speaker verification ON  (threshold {vp.threshold:.3f})")
        print("    Only your enrolled voice is acted on.")
    else:
        print("  ⚠ speaker verification OFF")
        print("    Any voice in the room can command KAVACH. The voiceprint is")
        print("    kept — `uv run kavach-speaker on` restores it.")
    print()
    print("  Restart the voice loop for this to take effect:")
    print("    uv run kavach-daemons install --only com.krishna.kavach")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
