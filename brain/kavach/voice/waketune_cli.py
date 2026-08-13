"""`kavach-waketune` — audio-guided wake-word threshold calibration."""

from __future__ import annotations

import argparse
import logging
import sys
import time

import numpy as np

from .loop import DEFAULT_MODELS_DIR, find_wake_model
from .mic import MicStream
from .wake import WakeWordDetector
from .waketune import (
    CALIBRATION_PATH,
    NEGATIVE_PHRASES,
    NEGATIVE_SECONDS,
    POSITIVE_TAKES,
    TAKE_SECONDS,
    best_score,
    choose_threshold,
    load_calibration,
    save_calibration,
)

RULE = "─" * 62


def _record(mic: MicStream, seconds: float) -> np.ndarray:
    blocks: list[np.ndarray] = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        block = mic.read(timeout=0.5)
        if block is not None:
            blocks.append(block)
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate the wake-word threshold.")
    parser.add_argument("--status", action="store_true", help="show current calibration")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    if args.status:
        current = load_calibration()
        if current is None:
            print("✗ not calibrated — the runtime floor is in use")
            print("  run `uv run kavach-waketune` to measure your own voice")
            return 1
        print(f"✓ calibrated threshold {current:.3f}")
        print(f"  {CALIBRATION_PATH}")
        return 0

    model = find_wake_model()
    if not model.exists():
        print(f"✗ no wake-word model at {model}", file=sys.stderr)
        return 1

    detector = WakeWordDetector(model, threshold=0.0)
    detector.load()

    from . import tts as tts_mod

    speaker = tts_mod.TextToSpeech(DEFAULT_MODELS_DIR)
    speaker.load()

    def say(text: str) -> None:
        tts_mod.play(speaker.synthesize(text), blocking=True)

    def tone(freq: int = 880, ms: int = 130) -> None:
        rate = 24000
        t = np.linspace(0, ms / 1000, int(rate * ms / 1000), endpoint=False)
        beep = (0.22 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        tts_mod.play(tts_mod.Speech(audio=beep, sample_rate=rate, voice="tone"), blocking=True)

    print(RULE)
    print("  KAVACH wake-word calibration")
    print(RULE)
    print(f"  {POSITIVE_TAKES} takes of the wake word, then {len(NEGATIVE_PHRASES)} ordinary phrases.")
    print("  Speak exactly as you would in normal use.")
    print(RULE)

    say(
        "Wake word calibration. First, say KAVACH after each tone. "
        "Then I'll ask you to say some ordinary phrases, so I can tell "
        "them apart."
    )

    mic = MicStream().start()
    positives: list[float] = []
    negatives: list[float] = []

    try:
        for i in range(1, POSITIVE_TAKES + 1):
            say(f"Take {i}. Say KAVACH.")
            tone()
            mic.forget()  # never let KAVACH's own voice into the measurement
            clip = _record(mic, TAKE_SECONDS)
            score = best_score(detector, clip)
            rms = float(np.sqrt(np.mean(clip**2))) if len(clip) else 0.0
            if rms < 0.006:
                print(f"  [{i}/{POSITIVE_TAKES}] KAVACH        too quiet (rms {rms:.4f}) — skipped")
                continue
            positives.append(score)
            print(f"  [{i}/{POSITIVE_TAKES}] KAVACH        score {score:.3f}")

        for j, phrase in enumerate(NEGATIVE_PHRASES, 1):
            say(f"Now say: {phrase}")
            tone(freq=660)
            mic.forget()
            clip = _record(mic, NEGATIVE_SECONDS)
            score = best_score(detector, clip)
            rms = float(np.sqrt(np.mean(clip**2))) if len(clip) else 0.0
            if rms < 0.006:
                print(f"  [{j}/{len(NEGATIVE_PHRASES)}] other         too quiet — skipped")
                continue
            negatives.append(score)
            print(f"  [{j}/{len(NEGATIVE_PHRASES)}] other         score {score:.3f}  ({phrase[:28]})")
    finally:
        mic.stop()

    if len(positives) < 3 or len(negatives) < 2:
        print(f"\n✗ not enough usable audio ({len(positives)} wake, {len(negatives)} other).")
        say("I didn't get enough clear audio. Nothing was changed.")
        return 1

    cal = choose_threshold(positives, negatives)

    print()
    print(RULE)
    print(f"  wake word : min {min(positives):.3f}   max {max(positives):.3f}")
    print(f"  other     : min {min(negatives):.3f}   max {max(negatives):.3f}")
    print(f"  margin    : {cal.margin:+.3f}")
    print(RULE)

    if cal.separated:
        save_calibration(cal)
        print(f"  ✓ threshold {cal.threshold:.3f}  (saved)")
        print(f"    {CALIBRATION_PATH}")
        say(f"Calibrated. Threshold {cal.threshold:.2f}.")
    else:
        # Refuse to write a number that only looks calibrated.
        print(f"  ✗ no separation — your wake word and ordinary speech overlap.")
        print(f"    worst wake take {min(positives):.3f} ≤ best other {max(negatives):.3f}")
        print("    NOT saved. Any threshold here either misses you or fires wrongly.")
        print("    Retrain with more varied negatives, or use push-to-talk.")
        say("The wake word and your normal speech overlap too much to separate. "
            "I have not changed anything.")
        return 2

    print(RULE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
