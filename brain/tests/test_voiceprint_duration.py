"""Speaker verification has a floor, and it is measured in seconds.

The gate rejected its owner 42 times out of 42. The threshold looked like the
culprit; it was not. Same audio, same speaker, scored at increasing durations::

    0.8s → 0.423    7.2s → 0.774    27.5s → 0.807
    2.7s → 0.579   13.8s → 0.816

Resemblyzer cannot embed a one-second clip stably. And the decisive control —
scoring 400 clips of *other* speakers the same way — shows they plateau while
the enrolled speaker climbs:

    duration    you     strangers (max)   margin
        1s     0.581        0.543         +0.038   ← noise
        3s     0.698        0.561         +0.138
        7s     0.774        0.540         +0.234
       14s     0.811        0.552         +0.258

**So the voiceprint works and the sampling did not.** Verification runs on
"open Notes" — about a second — where the margin between the enrolled user and
a total stranger is 0.038. No threshold separates those. It is not a tuning
problem; it is the embedding having nothing to work with.

Below `MIN_VERIFY_SECONDS` the honest answer is "I cannot tell", and this file
exists so that answer can never quietly become "yes".
"""

import numpy as np
import pytest

from kavach.identity.voiceprint import MIN_VERIFY_SECONDS, is_long_enough_to_verify


def test_a_one_second_command_is_not_verifiable():
    """Measured margin at 1s is +0.038, against +0.234 at 7s. Claiming to
    have identified a speaker from this is a claim the data cannot support."""
    assert not is_long_enough_to_verify(np.zeros(16_000), 16_000)


def test_the_floor_sits_where_separation_actually_appears():
    """+0.138 at 3s is the first duration with a margin worth anything."""
    assert 2.0 <= MIN_VERIFY_SECONDS <= 4.0


@pytest.mark.parametrize("seconds", [3.0, 5.0, 12.0])
def test_a_real_sentence_is_verifiable(seconds):
    assert is_long_enough_to_verify(np.zeros(int(seconds * 16_000)), 16_000)


@pytest.mark.parametrize("bad", [None, np.zeros(0)])
def test_nothing_is_never_long_enough(bad):
    assert not is_long_enough_to_verify(bad, 16_000)


def test_the_sample_rate_is_respected_not_assumed():
    """48kHz audio of the same length is a quarter of the duration."""
    samples = np.zeros(int(3.5 * 16_000))
    assert is_long_enough_to_verify(samples, 16_000)
    assert not is_long_enough_to_verify(samples, 48_000)


# ═══ the floor was set for an encoder that no longer exists ═══

def test_the_floor_was_re_measured_against_the_current_encoder():
    """3.0 came from resemblyzer, which was replaced by ECAPA because it
    could not separate this user at any duration. The constant outlived the
    measurement that justified it, and cost six real turns (2.16–3.0s)
    refused before they were ever scored.

    Re-measured on ECAPA with the user's own recordings against 400 other
    speakers, separation first appears at 1.5s:

        1.0s   you +0.064..+0.650   others +0.247   -0.184  overlap
        1.5s   you +0.333..+0.633   others +0.211   +0.122  separated
    """
    assert MIN_VERIFY_SECONDS <= 2.0, (
        f"MIN_VERIFY_SECONDS={MIN_VERIFY_SECONDS} still reflects the "
        f"resemblyzer measurement; ECAPA separates from 1.5s"
    )


def test_the_turns_that_were_being_refused_now_score():
    """The six real turns lost to the old floor: 2.16, 2.16, 2.58, 2.71,
    2.77 and 3.00 seconds."""
    for seconds in (2.16, 2.58, 2.77, 3.00):
        assert is_long_enough_to_verify(
            np.zeros(int(seconds * 16_000)), 16_000), seconds


def test_a_one_second_clip_is_still_refused():
    """The floor moved on evidence, and the evidence says 1.0s overlaps.
    Lowering it further would be a guess in the direction that lets a
    stranger in."""
    assert not is_long_enough_to_verify(np.zeros(16_000), 16_000)
