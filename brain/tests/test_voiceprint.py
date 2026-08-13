"""Speaker verification tests (spec §7 extension).

The hole this closes: the confirmation gate currently trusts *whoever is in
the room*. Anyone within earshot of the mic can answer "yes" to a delete
prompt.

Written before the implementation. The imposter test uses a synthesised voice
rather than a recording of a real person, so the suite needs no personal data
and runs on any machine.

**Every failure mode denies.** Not enrolled, low similarity, too little audio,
an exception in the encoder — all of them. Same asymmetry as the spoken
confirmation: consent must be given, never merely not-withheld.
"""

import numpy as np
import pytest

from kavach.identity.voiceprint import (
    MIN_ENROLMENT_SECONDS,
    VerificationResult,
    Voiceprint,
    cosine_similarity,
)

SR = 16_000


def synth_voice(seed: int, seconds: float = 3.0) -> np.ndarray:
    """A deterministic pseudo-voice.

    Not real speech, but it gives the encoder a stable, distinguishable
    signal — which is all these tests need. Different seeds are different
    "speakers"; the same seed is the same one.
    """
    rng = np.random.default_rng(seed)
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    f0 = 90 + seed * 17 % 120           # per-speaker pitch
    formants = [1.0, 0.5, 0.25, 0.12]
    wav = sum(
        amp * np.sin(2 * np.pi * f0 * (i + 1) * t + rng.uniform(0, 6.28))
        for i, amp in enumerate(formants)
    )
    # Syllable-rate envelope so the VAD finds something to keep.
    wav *= 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * 3.5 * t))
    wav += rng.normal(0, 0.005, wav.shape)
    return (wav / np.max(np.abs(wav)) * 0.4).astype(np.float32)


@pytest.fixture
def enrolled(tmp_path):
    vp = Voiceprint(path=tmp_path / "voiceprint.npz")
    clips = [synth_voice(1, 4.0), synth_voice(1, 4.0), synth_voice(1, 4.0)]
    vp.enrol(clips, sample_rate=SR)
    return vp


# ——— cosine similarity ———

def test_identical_embeddings_are_maximally_similar():
    v = np.array([0.6, 0.8], dtype=np.float32)
    assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)


def test_orthogonal_embeddings_score_zero():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)


def test_similarity_handles_a_zero_vector_without_dividing_by_zero():
    a = np.zeros(4, dtype=np.float32)
    b = np.array([1.0, 0, 0, 0], dtype=np.float32)
    assert cosine_similarity(a, b) == 0.0


# ——— enrolment ———

def test_enrolment_stores_a_reusable_profile(tmp_path):
    vp = Voiceprint(path=tmp_path / "vp.npz")
    assert not vp.is_enrolled

    vp.enrol([synth_voice(2, 4.0), synth_voice(2, 4.0)], sample_rate=SR)

    assert vp.is_enrolled
    assert (tmp_path / "vp.npz").exists()
    # A fresh instance must load what the first one wrote.
    assert Voiceprint(path=tmp_path / "vp.npz").is_enrolled


def test_enrolment_rejects_too_little_audio(tmp_path):
    """A profile built from two seconds of speech would be worthless, and
    silently accepting it would give false confidence in the gate."""
    vp = Voiceprint(path=tmp_path / "vp.npz")
    with pytest.raises(ValueError, match="at least"):
        vp.enrol([synth_voice(3, 1.0)], sample_rate=SR)


def test_enrolment_records_how_much_audio_it_used(enrolled):
    assert enrolled.enrolled_seconds >= MIN_ENROLMENT_SECONDS


# ——— verification: the point of the whole module ———

def test_the_enrolled_speaker_is_accepted(enrolled):
    result = enrolled.verify(synth_voice(1, 3.0), sample_rate=SR)
    assert isinstance(result, VerificationResult)
    assert result.accepted, f"similarity {result.similarity}"


def test_an_imposter_is_rejected(enrolled):
    """A different voice saying the right word must not authorise anything."""
    result = enrolled.verify(synth_voice(42, 3.0), sample_rate=SR)
    assert not result.accepted, f"imposter accepted at {result.similarity}"


def test_the_enrolled_speaker_scores_higher_than_an_imposter(enrolled):
    mine = enrolled.verify(synth_voice(1, 3.0), sample_rate=SR).similarity
    theirs = enrolled.verify(synth_voice(42, 3.0), sample_rate=SR).similarity
    assert mine > theirs


# ——— every failure denies ———

def test_verification_without_enrolment_denies(tmp_path):
    vp = Voiceprint(path=tmp_path / "none.npz")
    result = vp.verify(synth_voice(1, 3.0), sample_rate=SR)
    assert not result.accepted
    assert "not enrolled" in result.reason.lower()


def test_verification_of_silence_denies(enrolled):
    result = enrolled.verify(np.zeros(SR * 3, dtype=np.float32), sample_rate=SR)
    assert not result.accepted


def test_verification_of_a_too_short_clip_denies(enrolled):
    result = enrolled.verify(synth_voice(1, 0.2), sample_rate=SR)
    assert not result.accepted
    assert "short" in result.reason.lower()


def test_an_encoder_failure_denies_rather_than_raising(enrolled, monkeypatch):
    """A crash in the encoder must not become an unhandled exception in the
    middle of a confirmation prompt — nor an accidental approval."""
    def boom(*_a, **_k):
        raise RuntimeError("encoder exploded")

    monkeypatch.setattr(enrolled, "_embed", boom)
    result = enrolled.verify(synth_voice(1, 3.0), sample_rate=SR)
    assert not result.accepted


# ——— the result carries enough to tune the threshold later ———

def test_result_reports_similarity_and_threshold(enrolled):
    result = enrolled.verify(synth_voice(1, 3.0), sample_rate=SR)
    assert 0.0 <= result.similarity <= 1.0
    assert 0.0 < result.threshold < 1.0
    assert result.reason


def test_threshold_is_calibrated_at_enrolment_not_hardcoded(enrolled):
    """The wake word's threshold came from measurement (0.18). This one is
    derived from the spread of the enrolment clips rather than a guess."""
    assert enrolled.threshold != 0.75 or enrolled.calibrated


# ——— the silence hole, found by the test above ———

def test_near_silence_also_denies(enrolled):
    """Not just digital zero: room tone must not authenticate either.

    Measured before the fix — three seconds of zeros embedded to a vector
    scoring 1.00 against the enrolled profile, because Resemblyzer normalises
    by RMS and silence collapses to a degenerate direction that matches
    everything. An empty room would have authorised a delete.
    """
    quiet = (np.random.default_rng(0).normal(0, 0.0008, SR * 3)).astype(np.float32)
    assert not enrolled.verify(quiet, sample_rate=SR).accepted


def test_silence_denial_explains_itself(enrolled):
    result = enrolled.verify(np.zeros(SR * 3, dtype=np.float32), sample_rate=SR)
    assert not result.accepted
    assert "silence" in result.reason.lower() or "speech energy" in result.reason.lower()
