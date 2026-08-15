"""`kavach-verify-voice` — prove the threshold, then turn the gate on.

Enrolment gives KAVACH a voiceprint. It cannot give it a *threshold*, because
a threshold is a statement about how far your voice drifts between sittings and
enrolment only ever sees one sitting. This is the second sitting.

Refusing to save is the expected outcome when the microphone or the room will
not support verification, and it is strictly better than the alternative: a
saved number that rejects you at 2am with no explanation.
"""

from __future__ import annotations

import sys

import numpy as np

from ..killswitch.log import ActionLog
from .verify import (
    SECONDS_PER_SENTENCE,
    VERIFY_SENTENCES,
    apply,
    calibrate,
)
from .voiceprint import Voiceprint

SAMPLE_RATE = 16_000
RULE = "─" * 64


def _record(seconds: float) -> np.ndarray:
    import sounddevice as sd

    frames = int(seconds * SAMPLE_RATE)
    audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return audio[:, 0]


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="kavach-verify-voice",
        description="Measure the speaker threshold from a second sitting (§7).",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="measure and report, but do not save or enable")
    args = parser.parse_args(argv)

    voiceprint = Voiceprint()
    if not voiceprint.is_enrolled:
        print("No usable voiceprint. Run `uv run kavach-enrol --replace` first.")
        return 1

    print(RULE)
    print(f"  {len(VERIFY_SENTENCES)} sentences, {SECONDS_PER_SENTENCE:.0f}s each.")
    print("  These are NOT the enrolment phrases, on purpose — re-reading")
    print("  those reproduces the same delivery and the same wrong answer.")
    print("  Speak normally. Sit however you actually sit.")
    print(RULE)
    print()

    clips: list[np.ndarray] = []
    for index, sentence in enumerate(VERIFY_SENTENCES, start=1):
        print(f'  [{index}/{len(VERIFY_SENTENCES)}]  "{sentence}"')
        try:
            input("          press Enter, then speak… ")
        except EOFError:
            print("\n  Needs a terminal — nothing measured, nothing changed.")
            return 1
        clip = _record(SECONDS_PER_SENTENCE)
        level = float(np.sqrt(np.mean(clip.astype(np.float64) ** 2)))
        print(f"          recorded ({SECONDS_PER_SENTENCE:.1f}s, level {level:.3f})")
        clips.append(clip)

    print("\n  measuring against other voices…", flush=True)
    threshold, reason, detail = calibrate(voiceprint, clips)

    print()
    print(RULE)
    print(f"  you     n={detail['genuine_n']:<3} {_span(detail['genuine'])}")
    print(f"  others  n={detail['others_n']:<3} {_span(detail['others'])}")
    print(RULE)

    if threshold is None:
        print(f"\n  ✗ NOT SAVED — {reason}")
        print("\n  The gate stays off. That is the honest outcome, not a bug:")
        print("  a threshold that does not separate would reject you instead,")
        print("  silently, later. Retry somewhere quieter or closer to the mic.")
        return 2

    print(f"\n  ✓ {reason}")
    if args.dry_run:
        print("\n  --dry-run: nothing saved, gate unchanged.")
        return 0

    log = ActionLog()
    apply(voiceprint, threshold, log_=log)
    voiceprint.enable(log=log)
    print(f"\n  ✓ threshold {threshold:.3f} saved, speaker gate ON.")
    print("    Every rejection is logged with its score:")
    print("      ~/.kavach/logs/actions.jsonl")
    print("    Turn it off any time: uv run kavach-speaker off")
    return 0


def _span(values: list[float]) -> str:
    if not values:
        return "(none)"
    return (f"min {values[0]:+.3f}   median {values[len(values)//2]:+.3f}   "
            f"max {values[-1]:+.3f}")


if __name__ == "__main__":
    sys.exit(main())
