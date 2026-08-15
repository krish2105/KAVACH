"""Proving a threshold before trusting it.

Three thresholds have been written by enrolment and all three rejected their
owner: 0.803 (rejected 42 of 42 real recordings) and 0.577 (would reject 100%
— the best real score was 0.238). Enrolment cannot produce a valid threshold.
It only ever sees one sitting, and a threshold is a claim about the drift
*between* sittings.

This is the second sitting, and the rules it has to hold to.
"""

import numpy as np
import pytest

from kavach.identity import verify


class FakePrint:
    """A voiceprint whose embedding is derived from the clip itself —
    deterministic and separable without loading a 20MB model."""

    def __init__(self):
        self._mean = np.array([1.0, 0.0], dtype="float32")

    def _load_encoder(self):
        return None

    def _embed(self, wav, sample_rate):
        return np.array([float(np.mean(wav)), float(np.std(wav))],
                        dtype="float32")


def clip(value, seconds=4.0):
    out = np.full(int(seconds * 16000), value, dtype="float32")
    out[0] += 0.01
    return out


# ═══ the sentences ═══

def test_the_verification_sentences_differ_from_enrolment():
    """Re-reading the enrolment phrases reproduces the same delivery, which
    reproduces the same inflated self-similarity — the measurement error this
    module exists to avoid."""
    from kavach.identity.enrol import PHRASES

    assert not (set(verify.VERIFY_SENTENCES) & set(PHRASES))
    assert len(verify.VERIFY_SENTENCES) >= 4


def test_they_are_ordinary_sentences_not_commands():
    """The gate must accept you sounding normal, not sounding like someone
    dictating to a computer."""
    joined = " ".join(verify.VERIFY_SENTENCES).lower()
    for word in ("kavach", "open ", "delete ", "confirm"):
        assert word not in joined


# ═══ refusing is a result ═══

def test_overlap_saves_nothing():
    threshold, reason, _ = verify.calibrate(
        FakePrint(), [clip(0.5), clip(0.5), clip(0.5)], [clip(0.5), clip(0.5)])

    assert threshold is None
    assert "overlap" in reason.lower()


def test_no_negatives_saves_nothing():
    """A threshold measured with no other voices accepts everyone below it
    for a reason nobody measured."""
    threshold, _, _ = verify.calibrate(FakePrint(), [clip(0.9), clip(0.9)], [])

    assert threshold is None


def test_clear_separation_produces_a_threshold():
    threshold, reason, detail = verify.calibrate(
        FakePrint(), [clip(0.9), clip(0.92), clip(0.88)],
        [clip(0.1), clip(0.12), clip(0.08)])

    assert threshold is not None, reason
    assert detail["genuine_n"] == 3 and detail["others_n"] == 3


def test_the_detail_reports_both_distributions():
    """A number with no distribution behind it is how 0.803 got written."""
    _, _, detail = verify.calibrate(
        FakePrint(), [clip(0.9), clip(0.9)], [clip(0.1), clip(0.1)])

    assert detail["genuine"] and detail["others"]
    assert detail["genuine"] == sorted(detail["genuine"])


# ═══ applying it ═══

def test_apply_marks_the_profile_calibrated(tmp_path):
    """`calibrated` must mean "measured against a second sitting". Enrolment
    reports False precisely so this flag means something."""
    from kavach.identity.voiceprint import Voiceprint

    vp = Voiceprint(path=tmp_path / "vp.npz")
    vp._mean = np.ones(192, dtype="float32")
    vp.calibrated = False

    verify.apply(vp, 0.42)

    assert vp.threshold == pytest.approx(0.42)
    assert vp.calibrated is True


def test_blocks_are_long_enough_to_embed_stably():
    """Measured: the same speaker scores 0.42 at 0.8s and 0.82 at 14s. Short
    blocks measure noise, and a threshold from noise is what this replaces."""
    clips = [np.zeros(16000, dtype="float32") for _ in range(20)]

    blocks = verify.blocks_of(clips, seconds=4.0, count=3)

    assert blocks
    for block in blocks:
        assert len(block) / 16000 >= 3.0
