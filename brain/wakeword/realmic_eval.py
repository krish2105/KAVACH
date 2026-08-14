"""Score wake-word models on audio that has been through a real microphone.

    uv run python wakeword/realmic_eval.py --clips 40

## Why this exists

Every number v2 ever reported was computed on synthetic held-out clips made by
the same generator as its training data. It scored recall 0.835, FPPH 0.00 —
and 0.019 on the same utterance played through a speaker and recorded back,
while whisper transcribed that recording perfectly. The metric and the reality
disagreed completely, and the metric was the one everybody read.

So this measures the only thing that matters: does the model fire on a wake
word **after** it has been through a speaker, a room, and this laptop's
microphone.

## How

Each clip is played and recorded once, then scored by *every* model in the same
pass. That matters for fairness — the room, the noise floor and the position of
the machine are identical for each model, so the comparison is between models
rather than between recordings.

Nothing is written to disk (§7). The recordings exist only in memory for as
long as it takes to score them.

The room should be quiet and the volume normal. This measures your actual
conditions, so measuring them badly makes the result meaningless.
"""

from __future__ import annotations

import argparse
import random
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kavach.voice import tts as tts_mod          # noqa: E402
from kavach.voice.mic import MicStream           # noqa: E402
from kavach.voice.wake import WakeWordDetector   # noqa: E402
from kavach.voice.waketune import best_score     # noqa: E402

OUTPUT = Path(__file__).resolve().parent / "output"

#: Long enough for a 2s clip plus the room's tail and a little slack.
RECORD_SECONDS = 3.4

#: Below this a recording is too quiet to mean anything, and counting it would
#: flatter or punish a model for the room rather than for the model.
MIN_RMS = 0.006


def models() -> dict[str, Path]:
    found = {}
    for onnx in sorted(OUTPUT.glob("*/*.onnx")):
        found[onnx.stem] = onnx
    return found


def play_and_record(audio: np.ndarray, rate: int, mic: MicStream) -> np.ndarray:
    speech = tts_mod.Speech(audio=audio, sample_rate=rate, voice="clip")
    mic.forget()
    thread = threading.Thread(target=lambda: tts_mod.play(speech, blocking=True))
    thread.start()
    blocks, deadline = [], time.time() + RECORD_SECONDS
    while time.time() < deadline:
        block = mic.read(timeout=0.5)
        if block is not None:
            blocks.append(block)
    thread.join()
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips", type=int, default=40,
                        help="positives to play (an equal number of negatives)")
    parser.add_argument("--model", action="append", default=None,
                        help="limit to these model names")
    args = parser.parse_args()

    available = models()
    if args.model:
        available = {k: v for k, v in available.items() if k in args.model}
    if not available:
        print("✗ no exported models under wakeword/output/*/", file=sys.stderr)
        return 1

    detectors = {}
    for name, path in available.items():
        detector = WakeWordDetector(path, threshold=0.0)
        detector.load()
        detectors[name] = detector
    print(f"  models: {', '.join(detectors)}")

    # The *test* splits, never the training ones: a model that has memorised
    # its own training clips would otherwise score beautifully here too.
    newest = max(available.values(), key=lambda p: p.stat().st_mtime).parent
    positives = sorted((newest / "positive_test").glob("clip_[0-9]*.wav"))
    negatives = sorted((newest / "negative_test").glob("clip_[0-9]*.wav"))
    if not positives or not negatives:
        print(f"✗ no test clips under {newest}", file=sys.stderr)
        return 1

    random.seed(20260814)          # same clips every run, so runs compare
    positives = random.sample(positives, min(args.clips, len(positives)))
    negatives = random.sample(negatives, min(args.clips, len(negatives)))

    print(f"  playing {len(positives)} wake words and {len(negatives)} "
          f"non-wake clips through the speakers")
    print("  keep the room quiet — this measures your actual conditions\n")

    scores: dict[str, dict[str, list[float]]] = {
        name: {"positive": [], "negative": []} for name in detectors
    }
    skipped = 0
    mic = MicStream().start()
    try:
        for label, paths in (("positive", positives), ("negative", negatives)):
            for i, path in enumerate(paths, 1):
                audio, rate = sf.read(str(path), dtype="float32")
                if audio.ndim > 1:
                    audio = audio[:, 0]
                heard = play_and_record(audio, rate, mic)
                rms = float(np.sqrt(np.mean(heard**2))) if len(heard) else 0.0
                if rms < MIN_RMS:
                    skipped += 1
                    continue
                for name, detector in detectors.items():
                    scores[name][label].append(best_score(detector, heard))
                print(f"\r  {label} {i}/{len(paths)}  rms {rms:.3f}", end="")
            print()
    finally:
        mic.stop()

    if skipped:
        print(f"\n  {skipped} clips were too quiet to count (rms < {MIN_RMS})")

    print("\n" + "─" * 66)
    print(f"  {'model':16} {'wake (worst→best)':26} {'other (best)':14} margin")
    print("─" * 66)
    for name, sets in scores.items():
        pos, neg = sets["positive"], sets["negative"]
        if not pos or not neg:
            print(f"  {name:16} no usable audio")
            continue
        worst, best = min(pos), max(pos)
        loudest_negative = max(neg)
        margin = worst - loudest_negative
        verdict = "separated" if margin > 0.05 else "OVERLAPS"
        print(f"  {name:16} {worst:.3f} → {best:.3f}  (med {np.median(pos):.3f})"
              f"   {loudest_negative:.3f}        {margin:+.3f}  {verdict}")
    print("─" * 66)
    print("  A model is only usable here if the margin is positive AND the\n"
          "  worst wake score clears FLOOR (0.30) — see waketune.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
