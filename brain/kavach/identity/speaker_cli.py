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
from .voiceprint import MIN_VERIFY_SECONDS, Voiceprint


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


#: What `verify()` writes when it declined to look rather than judged.
#:
#: A clip under `MIN_VERIFY_SECONDS` is logged as a `voice.score` with
#: `similarity: 0.0`. Those are not measurements and must not reach a
#: distribution — six of them in thirty-six real scores dragged the reported
#: p05 from +0.008 to +0.000 and made every threshold look unreachable.
_NOT_A_SCORE = "too short"


def scored_similarities(entries) -> list[float]:
    """Every genuine similarity in a run of log entries.

    A `0.0` is only a placeholder when the reason says the clip was never
    judged. A real similarity of exactly zero is a measurement and is kept.
    """
    out = []
    for entry in entries:
        if entry.get("event") != "voice.score":
            continue
        if _NOT_A_SCORE in (entry.get("reason") or ""):
            continue
        value = entry.get("similarity")
        if isinstance(value, (int, float)):
            out.append(float(value))
    return out


def count_unscored(entries) -> int:
    """Turns refused before they were judged.

    Reported rather than silently dropped: a turn lost to the duration floor
    is still a turn the user lost, and hiding it hides half the problem.
    """
    return sum(1 for entry in entries
               if entry.get("event") == "voice.score"
               and _NOT_A_SCORE in (entry.get("reason") or ""))


def _report_scores(vp) -> int:
    """What real turns actually scored, and what threshold that implies.

    The point of shadow mode. Every previous threshold came from a sample
    collected on purpose; these come from the user simply using the thing.
    """
    import json

    from ..killswitch.log import ActionLog

    entries = list(ActionLog().read_all())
    scores = scored_similarities(entries)
    unscored = count_unscored(entries)

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
    if unscored:
        # Reported, not dropped. These were refused before they were judged,
        # and they are turns the user lost just as surely as a low score.
        print(f"    plus {unscored} turn(s) refused before scoring — under "
              f"the {MIN_VERIFY_SECONDS}s floor")
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
