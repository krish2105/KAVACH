"""When calibration fails, which side failed — and what to do about it.

`kavach-waketune` refused to save, correctly: the wake takes and ordinary
phrases overlapped, and any threshold there either misses you or fires at your
normal speech. Refusing beats writing a number that only looks calibrated.

The advice attached to that refusal was wrong for the actual failure, though.
It said "retrain with more varied negatives", which addresses ordinary speech
scoring too *high*. Measured against this model with synthetic speech, the
negatives score 0.002–0.013 — essentially zero. There is nothing to fix on that
side. The overlap comes from the wake takes scoring *low*, which is a different
problem with a different remedy.

There is also a floor: a saved threshold is never below FLOOR (0.30). So wake
takes that score under 0.30 cannot be made to work by calibrating — the fix is
the recording or the model, and the tool should say so rather than send you
round the loop again.
"""

import pytest

from kavach.voice.waketune import FLOOR, choose_threshold, diagnose


def cal(pos, neg):
    return choose_threshold(pos, neg)


# ═══ which side actually failed ═══

def test_low_wake_scores_are_named_as_the_problem():
    """The measured case: ordinary speech near zero, wake word barely above."""
    advice = diagnose(cal([0.04, 0.06, 0.03], [0.01, 0.02]))

    assert "wake" in advice.lower()
    assert "negative" not in advice.lower(), \
        "blames ordinary speech when ordinary speech scored near zero"


def test_high_ordinary_speech_is_named_when_that_is_the_problem():
    """The other shape entirely: the model fires at everything."""
    advice = diagnose(cal([0.72, 0.68, 0.70], [0.66, 0.71]))

    assert "ordinary" in advice.lower() or "negative" in advice.lower()


def test_scores_below_the_floor_say_so():
    """Calibration cannot rescue these, and looping the user is not a remedy.

    A saved threshold is clamped to FLOOR, so a wake take of 0.06 would never
    reach it however the numbers are arranged.
    """
    advice = diagnose(cal([0.05, 0.06, 0.04], [0.01, 0.01]))

    assert str(FLOOR) in advice or "0.30" in advice
    assert "push-to-talk" in advice.lower(), \
        "no fallback offered for a wake word that cannot work"


def test_a_separated_run_needs_no_advice():
    advice = diagnose(cal([0.80, 0.75, 0.78], [0.02, 0.03]))

    assert advice == ""


def test_advice_never_invents_a_cause_it_cannot_see():
    """Both sides mediocre is genuinely ambiguous, and saying so is honest."""
    advice = diagnose(cal([0.40, 0.38], [0.36, 0.37]))

    assert advice, "silence is not a diagnosis"


# ═══ the refusal itself must stay ═══

def test_an_unseparated_run_is_still_refused():
    """The advice is cosmetic; refusing to save is the substance."""
    assert cal([0.04, 0.06], [0.01, 0.02]).separated is False
    assert cal([0.80, 0.78], [0.02]).separated is True


def test_the_floor_still_clamps_a_saved_threshold():
    assert cal([0.80, 0.78], [0.02]).threshold >= FLOOR
