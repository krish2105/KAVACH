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
#: 2.0s of that is the scoring window, so this is really "how late may you
#: start". At 2.6 it was 0.6s — less than a reaction time plus the word, so
#: speaking a beat after the tone pushed the end of the word off the tape and
#: the take scored as a bad voice. See `score_take`.
TAKE_SECONDS = 4.0
NEGATIVE_SECONDS = 4.0

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


#: The rate everything here assumes — the detector's window is defined in
#: samples at this rate.
SAMPLE_RATE = 16_000

#: How far the sliding window advances between evaluations, in samples.
_SLIDE_SAMPLES = 2560           # 160 ms


@dataclass
class TakeScore:
    """One take, and enough context to know whether the number means anything."""

    score: float
    #: Where the best-scoring window started, in seconds.
    offset_s: float
    #: The best window was the last one on the tape, so the word may have run
    #: off the end. A low score from a clipped take says nothing about the
    #: voice or the model — it says the speaker was slow — and the two have
    #: opposite remedies, so they must not look alike.
    clipped: bool


def score_take(detector: WakeWordDetector, audio: np.ndarray) -> TakeScore:
    """Score a take, and report where the word was and whether it fitted.

    `best_score` answers "how well did this score". It cannot answer "was this
    a fair test", and five takes spanning 20x were read as evidence about a
    voice when some of it may have been evidence about timing.
    """
    if len(audio) < WINDOW_SAMPLES:
        # Shorter than one window: nothing slid, and the whole take is the
        # last window by definition.
        return TakeScore(detector.score_window(audio), 0.0, clipped=True)

    starts = list(range(0, len(audio) - WINDOW_SAMPLES + 1, _SLIDE_SAMPLES))
    scores = [detector.score_window(audio[i : i + WINDOW_SAMPLES]) for i in starts]
    best = max(range(len(scores)), key=lambda i: scores[i])

    return TakeScore(
        score=scores[best],
        offset_s=starts[best] / SAMPLE_RATE,
        clipped=best == len(starts) - 1,
    )


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


def diagnose(cal: Calibration) -> str:
    """Why a calibration failed, and what would actually change it.

    The original refusal said "retrain with more varied negatives" whatever the
    numbers were. Measured against kavach_v2 with synthetic speech, ordinary
    phrases score 0.002–0.013 — so for the real failure seen on this machine
    that advice pointed at the one side that was already fine, and the wake
    takes scoring low went unmentioned.

    Returns "" when the run separated cleanly and there is nothing to say.
    """
    if cal.separated:
        return ""

    worst_positive = min(cal.positives) if cal.positives else 0.0
    best_negative = max(cal.negatives) if cal.negatives else 0.0

    if worst_positive < FLOOR:
        # No arrangement of these numbers produces a usable threshold: a saved
        # one is clamped up to FLOOR, and the wake word would never reach it.
        return (
            f"Your wake takes score {worst_positive:.3f}–{max(cal.positives):.3f}, "
            f"below the {FLOOR:.2f} floor a saved threshold is clamped to — so "
            f"calibrating again cannot fix this. The model is not recognising "
            f"your voice saying the word.\n"
            f"    Try: speak closer to the mic, and say it the way you actually "
            f"would.\n"
            f"    If it stays low, the model needs retraining on voices like "
            f"yours — push-to-talk works meanwhile and is the safer default."
        )

    if best_negative >= worst_positive:
        return (
            f"Ordinary speech scores as high as {best_negative:.3f}, at or above "
            f"your quietest wake take ({worst_positive:.3f}) — the model fires on "
            f"speech generally, not on the word.\n"
            f"    Try: retraining with more varied negatives. Push-to-talk works "
            f"meanwhile."
        )

    return (
        f"Wake takes {worst_positive:.3f}–{max(cal.positives):.3f} and ordinary "
        f"speech up to {best_negative:.3f} are too close to call "
        f"(margin {cal.margin:+.3f}, needs > 0.05).\n"
        f"    Try: more takes, spoken as you normally would. Push-to-talk works "
        f"meanwhile."
    )


#: Every attempt, including the refusals. Scores only — never audio (§7).
HISTORY_PATH = CALIBRATION_PATH.parent / "wake-calibration-history.jsonl"


def record_attempt(cal: Calibration, model_name: str, saved: bool) -> None:
    """Append what this run measured, whether or not it was good enough.

    A refused calibration used to write nothing at all. That is right about the
    threshold — a number that only looks calibrated is worse than none — and
    wrong about the measurement, which is the only evidence of whether a
    retrain moved anything. Three separate times the only copy of these numbers
    was a terminal that scrolled away, and comparing two models on the user's
    real voice then meant recording it all again.

    Never raises: this is a convenience, and it must not be able to fail the
    run it is describing.
    """
    import datetime
    import json

    row = {
        "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": model_name,
        "positives": [round(p, 4) for p in cal.positives],
        "negatives": [round(n, 4) for n in cal.negatives],
        "worst_positive": round(min(cal.positives), 4) if cal.positives else None,
        "best_negative": round(max(cal.negatives), 4) if cal.negatives else None,
        "margin": round(cal.margin, 4),
        "separated": cal.separated,
        "threshold": round(cal.threshold, 4),
        "saved": saved,
    }
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_PATH.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
    except Exception:
        log.debug("could not record the calibration attempt", exc_info=True)
