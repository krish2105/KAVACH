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
