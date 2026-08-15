"""Choosing a threshold from the distribution instead of from the enrolment.

The gate has been **off** since 2026-08-14 because it rejected its owner all
night. From `~/.kavach/logs/actions.jsonl`, the user's own voice scored
`0.528 0.361 0.613 0.669 0.781` against a threshold of **0.803**.

The cause is structural, not a bad constant. `_calibrate` computed
``sims.mean() - 3*sims.std() - 0.05`` over the enrolment clips — which are
recorded back to back, same seat, same distance, same minute. They cluster
tightly, so `std` is tiny and the threshold lands just under the mean.

That measures **self-similarity within one session** and uses it as a proxy
for **self-similarity across sessions**. They are different distributions, and
the first is not evidence about the second: tight clustering at enrolment is
evidence the clips were recorded together, nothing more.

`MIN_THRESHOLD = 0.55` makes it worse — real speech reaches 0.52, so even
maximum clamping would still have locked the user out.

The rule this file encodes is `waketune`'s, which the wake word arrived at the
hard way: **a threshold that does not separate is not written.** Refusing to
save is a working tool reporting an honest result, and it beats saving a
number that will silently reject someone at 2am.
"""

import pytest

from kavach.identity.voiceprint import MAX_THRESHOLD, MIN_THRESHOLD, choose_threshold

#: Recorded live, 2026-08-14/15, from actions.jsonl and the enrolment run.
#: These are the user's own voice being rejected.
REAL_USER = [0.528, 0.361, 0.613, 0.669, 0.781, 0.52, 0.78]


# ═══ the case that broke ═══

def test_the_users_real_voice_is_accepted():
    others = [0.11, 0.19, 0.22, 0.08]
    threshold, why = choose_threshold(REAL_USER, others)

    assert threshold is not None, why
    assert threshold < min(REAL_USER), (
        f"{threshold:.3f} would reject the user's own voice, which is what "
        f"0.803 did for a whole night"
    )
    assert threshold > max(others), f"{threshold:.3f} would accept a stranger"


def test_it_sits_in_the_gap_rather_than_hugging_either_edge():
    """A threshold at the very edge of the genuine range fails the first time
    the user has a cold. Halfway is the most forgiving place it can be
    without accepting the nearest impostor."""
    threshold, _ = choose_threshold([0.60, 0.70, 0.80], [0.10, 0.20])
    assert 0.25 < threshold < 0.58


# ═══ refusing to save ═══

def test_no_separation_is_not_saved():
    """waketune's precedent, and the reason it exists: 0.803 WAS saved, and
    the failure surfaced hours later as "KAVACH ignores me"."""
    threshold, why = choose_threshold([0.30, 0.42], [0.35, 0.55])

    assert threshold is None
    assert "overlap" in why.lower()


def test_it_says_which_side_failed():
    """`diagnose()` used to blame the negatives every time. Being told the
    wrong half is broken sends you re-recording the wrong thing."""
    _, why = choose_threshold([0.90, 0.95], [])
    assert "negative" in why.lower() or "other" in why.lower()

    _, why = choose_threshold([], [0.1, 0.2])
    assert "genuine" in why.lower() or "wake" in why.lower() or "your" in why.lower()


def test_a_single_sample_is_not_a_distribution():
    """One clip cannot tell you anything about spread, and pretending it can
    is how the original calibration went wrong."""
    threshold, why = choose_threshold([0.8], [0.1])
    assert threshold is None
    assert "enough" in why.lower() or "more" in why.lower()


# ═══ bounds ═══

def test_the_result_stays_inside_the_sane_range():
    """A threshold outside these bounds means the measurement went wrong, and
    clamping fails safer than trusting it."""
    threshold, _ = choose_threshold([0.99, 0.98, 0.99], [0.01, 0.02])
    assert threshold is not None
    assert MIN_THRESHOLD <= threshold <= MAX_THRESHOLD


def test_the_floor_does_not_lock_the_user_out():
    """MIN_THRESHOLD was 0.55 while real speech reached 0.52 — the floor
    itself would have rejected them. Whatever the floor is, it must sit below
    what this user actually measures."""
    assert MIN_THRESHOLD < min(REAL_USER)


# ═══ the reason is always given ═══

@pytest.mark.parametrize("genuine,others", [
    ([0.6, 0.7], [0.1, 0.2]),
    ([0.3, 0.4], [0.35, 0.5]),
    ([], []),
    ([0.5], []),
])
def test_a_reason_is_returned_either_way(genuine, others):
    """Whether it saved or refused, the user has to be told what was measured
    — a silent refusal is indistinguishable from a crash."""
    _, why = choose_threshold(genuine, others)
    assert isinstance(why, str) and why.strip()
