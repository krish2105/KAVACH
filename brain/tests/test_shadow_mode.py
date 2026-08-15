"""Measure the speaker gate in production before enforcing it.

Three thresholds have been calibrated and all three were wrong, each from a
sample that did not represent real use:

| threshold | measured from | what real speech did |
|---|---|---|
| 0.803 | resemblyzer, enrolment clips | rejected 42 of 42 |
| 0.577 | ECAPA, enrolment clips | best real score was 0.238 |
| 0.383 | ECAPA, 6 read-aloud sentences | commands scored 0.373 and -0.001 |

The third is the instructive one. It was measured properly — a second sitting,
scored against 400 other voices, a 0.672 margin, refusing to save if it did not
separate. It still failed, because **reading a prepared sentence is not the
same act as giving a command.** The sample was honest and unrepresentative.

There is no fourth sample that fixes this by being cleverer. The only
representative sample of how the user talks to KAVACH is *the user talking to
KAVACH*, which cannot be collected until the gate is off.

So: score every turn, log every score, reject nothing. After a few days of
ordinary use the action log **is** the calibration set — real commands, real
distances, real times of day, including the ones spoken while distracted. Then
`choose_threshold` has something worth reading.

Shadow mode is not a safety feature and does not pretend to be. It is off by
default and reports honestly that nothing is being enforced.
"""

import numpy as np
import pytest

from kavach.identity.voiceprint import Voiceprint


@pytest.fixture
def profile(tmp_path):
    vp = Voiceprint(path=tmp_path / "vp.npz")
    vp._mean = np.ones(192, dtype="float32")
    vp.threshold = 0.5
    return vp


# ═══ the three states ═══

def test_a_new_profile_is_not_in_shadow_mode(profile):
    """Shadow is a deliberate choice, like every other widening here."""
    assert profile.shadow is False


def test_shadow_and_gating_are_different_questions(profile):
    """`gating` answers 'will a turn be rejected'. `shadow` answers 'will it
    be scored'. Conflating them is how `is_enrolled` once meant both 'we know
    your voice' and 'we are checking it', leaving `forget()` as the only way
    to stop checking."""
    profile.enable()
    assert profile.gating and not profile.shadow

    profile.set_shadow(True)
    assert profile.shadow
    assert not profile.gating, "shadow mode must not reject anything"


def test_turning_shadow_off_does_not_silently_enable_the_gate(profile):
    """Leaving shadow should return to off, not to enforcing. A mode change
    that quietly starts rejecting turns is the failure this whole file is
    about."""
    profile.set_shadow(True)
    profile.set_shadow(False)

    assert not profile.gating
    assert not profile.shadow


def test_shadow_survives_a_reload(tmp_path):
    """A mode that resets on restart would collect a few minutes of data and
    silently stop."""
    vp = Voiceprint(path=tmp_path / "vp.npz")
    vp._mean = np.ones(192, dtype="float32")
    vp.set_shadow(True)

    assert Voiceprint(path=tmp_path / "vp.npz").shadow is True


# ═══ what the loop asks ═══

def test_the_loop_scores_in_shadow_mode_but_does_not_reject(profile):
    """`should_verify_speaker` decides whether to REJECT. `should_score`
    decides whether to MEASURE. In shadow they disagree, which is the point."""
    from kavach.voice.loop import should_score_speaker, should_verify_speaker

    profile.set_shadow(True)

    assert should_score_speaker(profile)
    assert not should_verify_speaker(gating=profile.gating, pending=None)


def test_nothing_is_scored_when_the_profile_is_fully_off(profile):
    """Scoring costs an ECAPA embedding per turn. Not free, and not worth
    spending when nobody asked for the data.

    `disable()` explicitly, because a fresh `Voiceprint` defaults to
    `enabled=True` — enrolled means gated, so that an upgrade cannot silently
    drop the §7 speaker gate.
    """
    from kavach.voice.loop import should_score_speaker

    profile.disable()

    assert not should_score_speaker(profile)


def test_an_enforcing_profile_is_also_scored(profile):
    """The log is the calibration set either way — a threshold that starts
    rejecting the user should leave the evidence to prove it."""
    from kavach.voice.loop import should_score_speaker

    profile.enable()
    assert should_score_speaker(profile)
