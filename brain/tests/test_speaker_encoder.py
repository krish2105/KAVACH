"""Which encoder produces the embeddings, and why it changed.

The gate rejected its owner 42 times out of 42, and the first two diagnoses
were both wrong. It was not the threshold, and it was not the duration alone.
Measured 2026-08-15 on identical data with an identical held-out split:

    audio   resemblyzer: you / strangers      ECAPA: you / strangers
      1s    0.741-0.954 / 0.559               0.220-0.728 / 0.107
      2s    0.542-0.725 / 0.490               0.323-0.391 / 0.130
      3s    0.563-0.587 / 0.586  ← overlap    0.314-0.406 / 0.136
      7s    0.562-0.618 / 0.552               0.334-0.403 / 0.102

**resemblyzer's strangers reach 0.49-0.59; ECAPA's never exceed 0.14.** Pooled,
resemblyzer's worst genuine (0.542) sits BELOW its best stranger (0.586), so no
threshold exists at any duration. ECAPA's worst genuine (0.220) clears its best
stranger (0.136).

The encoder was the fault. resemblyzer is a 2019-era model and this is what
its limits look like from the outside: a gate that cannot be tuned into
working, and two plausible wrong explanations before the right one.

Caveat recorded honestly: that comparison enrolled and tested on takes from the
same recording session, so the genuine numbers are optimistic for both. The
comparison BETWEEN encoders is fair — identical data — but the absolute figures
are not a field estimate, and a cross-session check still needs the user.
"""

import numpy as np
import pytest

from kavach.identity.voiceprint import ENCODER, Voiceprint


def test_the_encoder_is_named_in_one_place():
    """Two places naming the model is how they diverge — the same defect that
    made agent.py refuse an app it was allowed to drive."""
    import inspect

    from tests._sourcecheck import code_text

    source = code_text(inspect.getmodule(Voiceprint))
    assert source.count("resemblyzer") <= 1, (
        "resemblyzer is named more than once in voiceprint.py"
    )


def test_the_embedding_is_deterministic():
    """The same audio must score the same twice, or no threshold means
    anything."""
    vp = Voiceprint()
    rng = np.random.default_rng(0)
    wav = rng.normal(0, 0.05, 16_000 * 4).astype(np.float32)

    a = vp._embed(wav, 16_000)
    b = vp._embed(wav, 16_000)

    assert np.allclose(a, b, atol=1e-5)


def test_an_embedding_has_a_stable_shape():
    vp = Voiceprint()
    rng = np.random.default_rng(1)
    short = vp._embed(rng.normal(0, 0.05, 16_000 * 3).astype(np.float32), 16_000)
    longer = vp._embed(rng.normal(0, 0.05, 16_000 * 8).astype(np.float32), 16_000)

    assert short.shape == longer.shape
    assert short.ndim == 1


def test_the_encoder_choice_is_recorded_not_implicit():
    """A future reader has to be able to tell which model produced a saved
    profile — an embedding from one encoder is meaningless to another."""
    assert isinstance(ENCODER, str) and ENCODER


def test_a_profile_from_a_different_encoder_is_refused(tmp_path):
    """The same rule the wake word learned: a calibration carries a hash of
    the model it measured, and a threshold from a different model is refused
    rather than applied. An embedding is not portable across encoders."""
    path = tmp_path / "voiceprint.npz"
    np.savez(path, mean=np.zeros(192, dtype="float32"), threshold=0.6,
             seconds=30.0, calibrated=True, enabled=True, encoder="some-other-model")

    vp = Voiceprint(path=path)

    assert not vp.is_enrolled, (
        "a profile built by a different encoder was loaded and would be "
        "compared against embeddings it has no relationship to"
    )
