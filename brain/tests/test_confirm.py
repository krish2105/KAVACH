"""Spoken confirmation tests (spec §7).

The asymmetry under test: only an unambiguous yes is a yes. Everything else —
no, silence, a misheard word, a timeout — is a denial. Consent must be given,
not merely not-withheld.
"""

import pytest

from kavach.hands.confirm import interpret


@pytest.mark.parametrize("said", [
    "yes", "Yes.", "yeah", "yep", "confirm", "Confirmed.",
    "yes, go ahead", "proceed",
])
def test_clear_yes_is_a_yes(said):
    assert interpret(said) is True


@pytest.mark.parametrize("said", [
    "no", "No.", "nope", "cancel", "stop", "abort", "never mind",
])
def test_clear_no_is_a_no(said):
    assert interpret(said) is False


@pytest.mark.parametrize("said", [
    "", "   ", "what", "hmm", "I'm not sure", "maybe later",
    "Thank you.", "the weather is nice",
])
def test_anything_unclear_is_not_consent(said):
    """None means 'didn't understand', and the caller treats it as a denial.
    An unparsed answer to 'shall I delete this?' is never permission."""
    assert interpret(said) is not True


def test_sure_and_ok_are_deliberately_not_affirmative():
    """They appear far too readily in ordinary speech to authorise a delete.
    If this ever changes it should be a deliberate decision, not a drift."""
    assert interpret("sure") is not True
    assert interpret("ok") is not True
    assert interpret("okay") is not True


def test_negation_is_not_read_as_agreement():
    for said in ["no, don't", "no thanks", "definitely not"]:
        assert interpret(said) is not True, said


# ——— gesture answers (§7 extension) ———

class FakeLoop:
    """Minimal stand-in for VoiceLoop's gesture-answer surface."""

    def __init__(self):
        import threading
        self._gesture_answer = None
        self._gesture_event = threading.Event()

    def answer_confirmation(self, approved: bool) -> None:
        self._gesture_answer = approved
        self._gesture_event.set()

    def take_gesture_answer(self):
        answer, self._gesture_answer = self._gesture_answer, None
        self._gesture_event.clear()
        return answer

    def arm_gesture_answer(self) -> None:
        self._gesture_answer = None
        self._gesture_event.clear()


def test_a_gesture_made_before_the_question_cannot_answer_it():
    """Arming clears stale state. Otherwise a thumbs-up at the orb could
    authorise something the user was never asked about."""
    loop = FakeLoop()
    loop.answer_confirmation(True)      # before any prompt
    loop.arm_gesture_answer()           # question is asked now
    assert loop.take_gesture_answer() is None


def test_gesture_answer_is_consumed_once():
    """A single held gesture answers one question, not every later one."""
    loop = FakeLoop()
    loop.arm_gesture_answer()
    loop.answer_confirmation(True)
    assert loop.take_gesture_answer() is True
    assert loop.take_gesture_answer() is None


def test_thumbs_down_is_carried_through_as_a_denial():
    loop = FakeLoop()
    loop.arm_gesture_answer()
    loop.answer_confirmation(False)
    assert loop.take_gesture_answer() is False
