"""Calibrate the wake-word threshold against the voice that will actually use it.

Training measured an optimal threshold of 0.18 — genuinely optimal *against
the training negatives*, where non-wake audio scores ~0.004. Live audio is not
the training set. Measured on this machine with synthetic speech, ordinary
non-wake phrases scored as high as 0.917 and digital silence 0.705, either of
which would trip a 0.18 threshold constantly.

So rather than guess a stricter number, measure the real one: record the user
saying the wake word, record them saying other things, and pick a threshold
with actual separation between the two. If there is no separation, say so
plainly instead of shipping a number that looks calibrated but isn't.

Runs in spoken mode so it needs no keyboard — same reasoning as enrolment.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .mic import MicStream
from .wake import WINDOW_SAMPLES, WakeWordDetector

log = logging.getLogger("kavach.voice.waketune")

CALIBRATION_PATH = Path.home() / ".kavach" / "wake_threshold.json"

#: Say the wake word this many times.
POSITIVE_TAKES = 5
#: Non-wake phrases, to find what "not the wake word" scores like.
NEGATIVE_PHRASES = [
    "what time is it",
    "open my calendar for tomorrow",
    "the quick brown fox jumps over the lazy dog",
    "delete the draft in notes",
]
TAKE_SECONDS = 2.6
NEGATIVE_SECONDS = 3.4

#: Never accept a calibrated threshold below this. A very low number here
#: means the recording produced no real separation, not that the wake word is
#: exquisitely sensitive.
FLOOR = 0.30


@dataclass
class Calibration:
    threshold: float
    positives: list[float]
    negatives: list[float]
    separated: bool
    margin: float

    def as_dict(self) -> dict:
        return {
            "threshold": round(self.threshold, 4),
            "positives": [round(p, 4) for p in self.positives],
            "negatives": [round(n, 4) for n in self.negatives],
            "separated": self.separated,
            "margin": round(self.margin, 4),
        }


def best_score(detector: WakeWordDetector, audio: np.ndarray) -> float:
    """Highest score over every 2 s window in the clip, hopping 160 ms.

    A single window is the wrong unit — the wake word lands somewhere inside
    the take, and where exactly depends on when the speaker started.
    """
    if len(audio) < WINDOW_SAMPLES:
        return detector.score_window(audio)
    return max(
        detector.score_window(audio[i : i + WINDOW_SAMPLES])
        for i in range(0, len(audio) - WINDOW_SAMPLES + 1, 2560)
    )


def choose_threshold(positives: list[float], negatives: list[float]) -> Calibration:
    """Pick a threshold that separates the two sets, or report that it can't.

    Uses the *worst* positive and the *best* negative, not the averages — a
    wake word that works on average is one that ignores you every few tries.
    """
    worst_positive = min(positives) if positives else 0.0
    best_negative = max(negatives) if negatives else 0.0
    margin = worst_positive - best_negative
    separated = margin > 0.05

    if separated:
        # Sit nearer the negatives: missing a wake word costs one repeat,
        # while a false wake starts recording the room unprompted.
        threshold = best_negative + margin * 0.35
    else:
        # Overlapping. Favour not firing spuriously, and say so loudly.
        threshold = max(best_negative + 0.02, worst_positive)

    return Calibration(
        threshold=max(FLOOR, min(0.95, threshold)),
        positives=positives,
        negatives=negatives,
        separated=separated,
        margin=margin,
    )


def model_fingerprint(model: Path) -> str:
    """Content hash of a model file.

    Content rather than path or mtime: retraining writes a different model to
    the same path, and a threshold measured against the old weights says
    nothing about the new ones.
    """
    import hashlib

    return hashlib.sha256(Path(model).read_bytes()).hexdigest()[:16]


def load_calibration(model: Path | None = None) -> float | None:
    """The calibrated threshold, or None if there isn't a usable one.

    Passing the model is what makes this safe. A threshold is a property of a
    *specific* model — v1's optimum was 0.70 and v2's is 0.20 — so a
    calibration measured against different weights is not a worse answer, it is
    a wrong one, and applying it silently is the failure mode this guards.
    """
    try:
        data = json.loads(CALIBRATION_PATH.read_text())
        threshold = float(data["threshold"])
    except Exception:
        return None

    if model is None:
        return threshold

    recorded = data.get("model_fingerprint")
    if recorded is None:
        # Written before calibrations recorded a model. Refused rather than
        # trusted: it probably belongs to v1, and we cannot tell.
        log.warning("calibration predates model tracking — recalibrate")
        return None
    try:
        if recorded != model_fingerprint(model):
            log.warning("calibration was measured on a different model (%s) "
                        "— ignoring it. Run kavach-waketune.",
                        data.get("model", "unknown"))
            return None
    except Exception:
        return None
    return threshold


def save_calibration(cal: Calibration, model: Path | None = None) -> None:
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = cal.as_dict()
    if model is not None:
        payload["model"] = str(model)
        payload["model_fingerprint"] = model_fingerprint(model)
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PATH.write_text(json.dumps(payload, indent=2))
    CALIBRATION_PATH.chmod(0o600)
