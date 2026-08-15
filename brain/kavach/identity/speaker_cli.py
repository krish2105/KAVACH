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
                        choices=["status", "on", "off", "shadow", "scores"])
    args = parser.parse_args(argv)

    vp = Voiceprint()

    if not vp.is_enrolled:
        print("✗ no voiceprint enrolled — there is nothing to verify against.")
        print("  uv run kavach-enrol")
        return 1

    if args.action == "scores":
        return _report_scores(vp)

    if args.action != "status":
        # A security state change, recorded like any other action.
        log = ActionLog()
        if args.action == "shadow":
            vp.set_shadow(True, log=log)
        else:
            vp.set_shadow(False, log=log)
            (vp.enable if args.action == "on" else vp.disable)(log=log)

    print()
    if vp.shadow:
        print("  ◐ speaker verification SHADOW — measuring, not enforcing")
        print("    Every turn is scored and logged; nothing is rejected.")
        print("    Use KAVACH normally for a few days, then:")
        print("      uv run kavach-speaker scores")
        print("    Three thresholds have been set from samples that turned")
        print("    out not to represent real use. This collects the real one.")
    elif vp.gating:
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


def _report_scores(vp) -> int:
    """What real turns actually scored, and what threshold that implies.

    The point of shadow mode. Every previous threshold came from a sample
    collected on purpose; these come from the user simply using the thing.
    """
    import json

    from ..killswitch.log import ActionLog

    scores = []
    for entry in ActionLog().read_all():
        if entry.get("event") == "voice.score":
            value = entry.get("similarity")
            if isinstance(value, (int, float)):
                scores.append(float(value))

    print()
    if len(scores) < 10:
        print(f"  only {len(scores)} scored turn(s) so far — not enough to "
              f"read a threshold from.")
        print("  Keep using KAVACH; this needs a few days of ordinary use,")
        print("  including the turns spoken while distracted or far away.")
        print()
        return 1

    scores.sort()
    p05 = scores[max(0, int(len(scores) * 0.05))]
    print(f"  {len(scores)} scored turns")
    print(f"    min {scores[0]:+.3f}   p05 {p05:+.3f}   "
          f"median {scores[len(scores)//2]:+.3f}   max {scores[-1]:+.3f}")
    print()
    print("  These are YOUR turns — every one of them should be accepted, so")
    print("  a threshold has to sit below the minimum, not below the median.")
    print(f"  Nothing here says what an IMPOSTER scores; run")
    print("    uv run kavach-verify-voice --dry-run")
    print("  to measure that side against 400 other voices before enabling.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
