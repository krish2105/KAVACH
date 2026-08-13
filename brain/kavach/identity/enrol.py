"""Voiceprint enrolment (spec §7 extension).

    uv run kavach-enrol            # record 5 phrases, build the profile
    uv run kavach-enrol --status   # is a profile enrolled? what threshold?
    uv run kavach-enrol --forget   # delete it

Needs the user physically present, so it is a deliberate one-off step rather
than anything automatic. Until it is run, confirmations check *what* was said
but not *who* said it, and the daemon says so at startup rather than implying
otherwise.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from ..voice.mic import TARGET_RATE, MicStream
from .voiceprint import MIN_ENROLMENT_SECONDS, Voiceprint

#: Varied phonetically so the profile is not overfitted to one sentence.
PHRASES = [
    "KAVACH, open my calendar for tomorrow",
    "The quick brown fox jumps over the lazy dog",
    "Delete the draft in Notes — yes, confirm that",
    "What is the weather going to be like this weekend",
    "Six thick slabs of blue granite were quarried early",
]

CLIP_SECONDS = 3.5


def _record(mic: MicStream, seconds: float) -> np.ndarray:
    """Record a fixed window. Fixed rather than silence-detected because an
    enrolment clip should be a known length, not however long you paused."""
    wanted = int(TARGET_RATE * seconds)
    chunks: list[np.ndarray] = []
    collected = 0
    deadline = time.monotonic() + seconds + 3.0

    while collected < wanted and time.monotonic() < deadline:
        block = mic.read(timeout=1.0)
        if block is None:
            continue
        chunks.append(block)
        collected += len(block)

    return np.concatenate(chunks)[:wanted] if chunks else np.zeros(0, dtype=np.float32)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kavach-enrol",
        description="Bind KAVACH's confirmation gate to your voice (§7).",
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--forget", action="store_true")
    parser.add_argument("--clips", type=int, default=len(PHRASES))
    args = parser.parse_args(argv)

    voiceprint = Voiceprint()

    if args.status:
        if voiceprint.is_enrolled:
            print(f"✓ enrolled from {voiceprint.enrolled_seconds:.1f}s of speech")
            print(f"  threshold: {voiceprint.threshold:.3f} "
                  f"({'calibrated' if voiceprint.calibrated else 'fallback'})")
            print(f"  profile:   {voiceprint.path}")
        else:
            print("✗ not enrolled — confirmations check WHAT you said, not WHO")
            print("  run `uv run kavach-enrol` to fix that")
        return 0

    if args.forget:
        voiceprint.forget()
        print("✓ voiceprint deleted. Confirmations no longer check identity.")
        return 0

    if voiceprint.is_enrolled:
        print(f"A profile already exists ({voiceprint.enrolled_seconds:.1f}s, "
              f"threshold {voiceprint.threshold:.3f}).")
        if input("Replace it? [y/N] ").strip().lower() not in {"y", "yes"}:
            print("Left unchanged.")
            return 0

    phrases = PHRASES[: max(2, args.clips)]
    print("─" * 64)
    print("  KAVACH voiceprint enrolment")
    print("─" * 64)
    print(f"  {len(phrases)} phrases, {CLIP_SECONDS:.0f}s each "
          f"(~{len(phrases) * CLIP_SECONDS:.0f}s total).")
    print("  Speak normally, at the distance and volume you'd actually use.")
    print("  Recording starts right after you press Enter.")
    print("─" * 64)

    mic = MicStream().start()
    clips: list[np.ndarray] = []
    try:
        for i, phrase in enumerate(phrases, 1):
            input(f"\n  [{i}/{len(phrases)}]  \"{phrase}\"\n         press Enter, then speak…")
            mic.forget()  # drop whatever was buffered while you were reading
            print("         ● recording…", end="", flush=True)
            clip = _record(mic, CLIP_SECONDS)
            rms = float(np.sqrt(np.mean(clip**2))) if len(clip) else 0.0
            if rms < 0.01:
                print(f" too quiet (rms {rms:.4f}) — retaking")
                continue
            clips.append(clip)
            print(f" done ({len(clip)/TARGET_RATE:.1f}s, level {rms:.3f})")
    finally:
        mic.stop()

    total = sum(len(c) for c in clips) / TARGET_RATE
    if total < MIN_ENROLMENT_SECONDS:
        print(f"\n✗ only got {total:.1f}s of usable audio "
              f"(need {MIN_ENROLMENT_SECONDS:.0f}s). Check the mic and retry.")
        return 1

    print(f"\n  building profile from {total:.1f}s…")
    voiceprint.enrol(clips, sample_rate=TARGET_RATE)

    print("─" * 64)
    print(f"  ✓ enrolled. threshold {voiceprint.threshold:.3f} "
          f"({'calibrated from your clips' if voiceprint.calibrated else 'fallback'})")
    print(f"    profile: {voiceprint.path} (0600, gitignored)")
    print()
    print("  Destructive actions now require an affirmative answer IN YOUR VOICE.")
    print("  If it ever refuses you wrongly, every attempt is logged with its")
    print("  similarity score — check ~/.kavach/logs/actions.jsonl and retune.")
    print("─" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
