"""Prove the threshold before trusting it (§7 extension).

**This is the step that was missing three times.** Enrolment measures how
similar your voice is to itself *within one sitting*, and that number tells you
almost nothing about how similar it will be tomorrow, sitting differently, at a
normal speaking volume. Every threshold this project has written came from that
first number, and every one of them rejected its owner:

| attempt | threshold | outcome |
|---|---|---|
| resemblyzer | 0.803 | rejected 42 of 42 real recordings |
| ECAPA, first enrolment | 0.577 | would reject 100% — best real score was 0.238 |

The fix is not a better formula. It is a **second sample**: speech recorded
after enrolment, plus voices that are not yours, and a threshold placed in the
gap between them. If there is no gap, nothing is saved and the gate stays off —
`waketune`'s rule, arrived at the same way and for the same reason.

Nothing here writes audio to disk. The clips are embedded and dropped.
"""

from __future__ import annotations

import logging

import numpy as np

from .voiceprint import Voiceprint, choose_threshold, cosine_similarity

log = logging.getLogger("kavach.identity.verify")

SAMPLE_RATE = 16_000

#: Deliberately **not** the enrolment phrases. Re-reading the same sentences
#: reproduces the same delivery, which reproduces the same inflated
#: self-similarity — the exact measurement error this exists to avoid.
#:
#: Ordinary sentences rather than commands, because the gate has to accept you
#: sounding normal, not you sounding like someone dictating to a computer.
VERIFY_SENTENCES = [
    "I think it might rain later this afternoon.",
    "Could you put that on the list for next week?",
    "The train was late again, which is not surprising.",
    "I'll finish the rest of it tomorrow morning.",
    "That restaurant near the station is worth trying.",
    "Remind me to call them back before five.",
]

#: How much speech each sentence should yield. Below `MIN_VERIFY_SECONDS` an
#: embedding is unstable, and a threshold measured from unstable embeddings is
#: a threshold measured from noise.
SECONDS_PER_SENTENCE = 4.0


def score_clips(voiceprint: Voiceprint, clips: list[np.ndarray],
                sample_rate: int = SAMPLE_RATE) -> list[float]:
    """Cosine similarity of each clip against the enrolled mean."""
    voiceprint._load_encoder()
    scores = []
    for clip in clips:
        try:
            scores.append(
                cosine_similarity(voiceprint._embed(clip, sample_rate),
                                  voiceprint._mean)
            )
        except Exception:
            log.debug("could not embed a verification clip", exc_info=True)
    return scores


def load_other_voices(limit: int = 400) -> list[np.ndarray]:
    """Clips of people who are not the enrolled speaker.

    Sourced from the wake-word training corpus, which is on disk already and
    is the only large set of non-user speech this machine has. **Their absence
    is a refusal, not a shrug** — a threshold measured with no negatives
    accepts everyone below it for no reason anyone measured.
    """
    from pathlib import Path

    import soundfile as sf

    root = Path(__file__).resolve().parents[2] / "wakeword" / "output" / "kavach"
    clips: list[np.ndarray] = []
    for path in sorted((root / "negative_test").glob("*.wav"))[:limit]:
        try:
            wav, rate = sf.read(path, dtype="float32")
        except Exception:
            continue
        if rate != SAMPLE_RATE or len(wav) < 1600:
            continue
        clips.append(wav.mean(axis=1) if wav.ndim > 1 else wav)
    return clips


def blocks_of(clips: list[np.ndarray], seconds: float,
              count: int | None = None) -> list[np.ndarray]:
    """Concatenate `clips` into chunks of about `seconds` each.

    Short clips are joined because an embedding needs several seconds to be
    stable — measured: the same speaker scores 0.42 at 0.8s and 0.82 at 14s.

    **`count` used to default to 8, and that cap was a real defect.** The
    imposter corpus is 400 clips of ~2s — 800 seconds of non-user speech,
    enough for roughly 200 four-second blocks — and `calibrate()` scored
    eight of them. The negative side decides whether the gate is safe to
    enable at all, and it was measured on 2% of the available data.

    Eight samples cannot show a tail, and the tail is the only part that
    matters. Including the rest moved the measured imposter maximum from
    **+0.135 to +0.419**, above the user's own median — which is the fact
    that decided the gate could not be enabled.

    `None` means use everything. A caller wanting a quick answer can still
    ask for a cap, explicitly.

    **A caveat this corpus cannot escape, and it points the dangerous way.**
    The clips are ~2s each and come from different speakers, so a 4s block
    is two people blended. That embedding sits between them and scores
    *lower* than either would alone — measured on the same corpus and the
    same profile::

        400 clips scored individually (2s, one speaker)   max +0.419
        128 blocks of 4s (two speakers concatenated)      max +0.254

    Neither is a realistic imposter: one is a real voice measured too
    briefly to be stable, the other is a chimera. **A real imposter is one
    person speaking for four seconds, and this corpus contains none.** Treat
    the higher figure as the honest floor, because underestimating what a
    stranger scores is the error that lets one in.
    """
    need = int(seconds * SAMPLE_RATE)
    out, index = [], 0
    while (count is None or len(out) < count) and index < len(clips):
        piece, taken = [], index
        while sum(len(c) for c in piece) < need and taken < len(clips):
            piece.append(clips[taken])
            taken += 1
        if piece and sum(len(c) for c in piece) >= need * 0.8:
            out.append(np.concatenate(piece)[:need])
        index = taken if taken > index else index + 1
    return out


def calibrate(voiceprint: Voiceprint, fresh_clips: list[np.ndarray],
              other_clips: list[np.ndarray] | None = None,
              ) -> tuple[float | None, str, dict]:
    """Measure separation and return `(threshold, reason, detail)`.

    `threshold` is None when the two distributions overlap, and **that is a
    result**: it means no number can accept you and refuse everyone else, so
    writing one would only decide which of those two failures you get.
    """
    if other_clips is None:
        other_clips = load_other_voices()

    genuine = score_clips(voiceprint, fresh_clips)
    others = score_clips(voiceprint, blocks_of(other_clips, SECONDS_PER_SENTENCE))

    threshold, reason = choose_threshold(genuine, others)
    detail = {
        "genuine": [round(s, 4) for s in sorted(genuine)],
        "others": [round(s, 4) for s in sorted(others)],
        "genuine_n": len(genuine),
        "others_n": len(others),
    }
    return threshold, reason, detail


def apply(voiceprint: Voiceprint, threshold: float, log_=None) -> None:
    """Write a **measured** threshold and mark the profile calibrated."""
    voiceprint.threshold = float(threshold)
    voiceprint.calibrated = True
    voiceprint._save()
    if log_ is not None:
        log_.append("voiceprint.calibrated", threshold=round(threshold, 4))
    log.info("voiceprint threshold set to %.4f from measurement", threshold)


__all__ = ["VERIFY_SENTENCES", "calibrate", "apply", "score_clips",
           "load_other_voices", "blocks_of", "SECONDS_PER_SENTENCE"]
