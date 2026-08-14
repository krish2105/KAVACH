"""Turning speaker verification off without throwing the voiceprint away.

Until now the only way to stop KAVACH gating on your voice was `forget()`,
which deletes the enrolment. So "let my colleague try it for five minutes"
meant re-recording your voiceprint afterwards, and the realistic response was
to never turn it off — or to run without ever enrolling, which is worse.

This is a **security** control, not a preference, so the shape matters more
than the feature:

* enrolled means on. A toggle that defaults to off would quietly undo §7 for
  anyone who upgrades.
* it survives a restart, or "off" means "off until something restarts and
  surprises you".
* turning it off is written to the action log, because it is the moment KAVACH
  stops caring who is speaking.
* the enrolment is untouched either way.
"""

import numpy as np
import pytest

from kavach.identity.voiceprint import Voiceprint
from kavach.killswitch.log import ActionLog


@pytest.fixture
def enrolled(tmp_path):
    vp = Voiceprint(path=tmp_path / "voiceprint.npz")
    # Enrolment needs the encoder; write the state directly instead so these
    # stay fast and deterministic.
    vp._mean = np.ones(256, dtype=np.float32) / 16.0
    vp.threshold = 0.613
    vp._save()
    return vp


def reloaded(vp):
    return Voiceprint(path=vp.path)


# ═══ the default ═══

def test_an_enrolled_voiceprint_is_on(enrolled):
    """§7 by default. Anyone upgrading keeps the gate they had."""
    assert enrolled.enabled is True
    assert enrolled.gating is True


def test_gating_is_off_when_nothing_is_enrolled(tmp_path):
    """Nothing to compare against, so nothing to gate on — and it must not
    claim otherwise."""
    vp = Voiceprint(path=tmp_path / "none.npz")

    assert vp.is_enrolled is False
    assert vp.gating is False


# ═══ the toggle ═══

def test_disabling_keeps_the_enrolment(enrolled):
    """The whole point. `forget()` was the only way to do this before."""
    enrolled.disable()

    assert enrolled.enabled is False
    assert enrolled.gating is False
    assert enrolled.is_enrolled is True, "the voiceprint was thrown away"


def test_the_setting_survives_a_restart(enrolled):
    enrolled.disable()

    assert reloaded(enrolled).enabled is False


def test_it_can_be_turned_back_on(enrolled):
    enrolled.disable()
    enrolled.enable()

    assert reloaded(enrolled).gating is True


def test_forget_still_removes_everything(enrolled):
    enrolled.disable()
    enrolled.forget()

    assert enrolled.is_enrolled is False
    assert not enrolled.path.exists()


# ═══ it is a security change, so it is recorded ═══

def test_turning_it_off_is_logged(enrolled, tmp_path):
    """The moment KAVACH stops caring who is speaking is worth a line."""
    log = ActionLog(tmp_path / "actions.jsonl")

    enrolled.disable(log=log)

    events = [e for e in log.read_all() if e.get("event", "").startswith("voiceprint")]
    assert events, "disabling speaker verification left no record"
    assert events[-1].get("enabled") is False


def test_turning_it_on_is_logged_too(enrolled, tmp_path):
    log = ActionLog(tmp_path / "actions.jsonl")
    enrolled.disable(log=log)

    enrolled.enable(log=log)

    assert [e for e in log.read_all() if e.get("enabled") is True]


def test_the_record_survives_ghost_mode():
    """Ghost hides what KAVACH perceived, never what it did — and switching
    off the speaker gate is something it did."""
    assert "voiceprint.disabled" not in ActionLog.SUPPRESSED_IN_GHOST
    assert "voiceprint.enabled" not in ActionLog.SUPPRESSED_IN_GHOST


def test_the_voice_loop_asks_whether_it_is_gating():
    """`is_enrolled` answers "do we know this voice", not "are we checking it".
    Asking the wrong one makes the toggle decorative."""
    loop = (__import__("pathlib").Path(__file__).resolve().parents[1]
            / "kavach" / "voice" / "loop.py").read_text()

    assert "self.voiceprint.gating" in loop
    assert "self.voiceprint.is_enrolled:" not in loop, \
        "the turn gate still keys off enrolment, so disabling does nothing"
