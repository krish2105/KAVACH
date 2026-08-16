"""The two tools that measure the speaker gate were both measuring wrong.

The gate shipped at 0.300 and refused 31% of the user's speech. Finding that
took computing the distributions by hand, because neither tool built to
report them was reporting them correctly.

**1. `kavach-speaker scores` counted turns that were never scored.**

A clip under `MIN_VERIFY_SECONDS` is logged as `voice.score` with
`similarity: 0.0` and `reason: "clip too short to verify (2.7s)"`. Those
zeros went into the distribution:

    reported   min -0.042   p05 +0.000   median +0.325
    actual     min -0.042   p05 +0.008   median +0.335

The median moves little and **the low percentiles are the whole point** —
they are what a threshold has to sit under. Six placeholder zeros in
thirty-six real scores dragged p05 to zero and made every threshold look
unreachable.

**2. `calibrate()` scored 8 of 400 imposter clips.**

`blocks_of(clips, seconds, count=8)` — a hardcoded cap, not a duration
problem. 400 clips of ~2s is 637 seconds of non-user speech, enough for
~159 four-second blocks, and 8 were used. **The negative side decides
whether the gate is safe to enable at all, and it was measured on 2% of the
available data.**

Eight samples cannot show a tail, and the tail is the only part that
matters: the measured imposter maximum rose from **+0.135 to +0.419** once
the rest of the corpus was included — above the user's own median.
"""

import numpy as np
import pytest

from kavach.identity.verify import blocks_of


# ═══ 1. the scores CLI ═══

def _entries():
    """A log with both kinds of `voice.score`: real ones and placeholders."""
    return [
        {"event": "voice.score", "similarity": 0.42, "reason": "voice matches"},
        {"event": "voice.score", "similarity": 0.0,
         "reason": "clip too short to verify (2.71s)"},
        {"event": "voice.score", "similarity": 0.31,
         "reason": "voice does not match the enrolled speaker"},
        {"event": "voice.score", "similarity": 0.0,
         "reason": "clip too short to verify (2.16s)"},
        {"event": "voice.turn", "similarity": 0.99},          # not a score
    ]


def test_unscored_turns_are_excluded():
    from kavach.identity.speaker_cli import scored_similarities

    assert scored_similarities(_entries()) == [0.42, 0.31]


def test_a_zero_that_is_a_real_score_is_kept():
    """`0.0` is only a placeholder when the reason says so. A genuine
    similarity of exactly zero is a measurement and must survive."""
    from kavach.identity.speaker_cli import scored_similarities

    entries = [{"event": "voice.score", "similarity": 0.0,
                "reason": "voice does not match the enrolled speaker"}]

    assert scored_similarities(entries) == [0.0]


def test_the_count_of_unscored_turns_is_reported():
    """Silently dropping them would hide the other half of the problem —
    turns refused for duration are still turns the user lost."""
    from kavach.identity.speaker_cli import count_unscored

    assert count_unscored(_entries()) == 2


# ═══ 2. blocks_of ═══

def _clips(n, seconds=2.0):
    rng = np.random.default_rng(0)
    return [rng.normal(0, 0.1, int(seconds * 16_000)).astype(np.float32)
            for _ in range(n)]


def test_all_the_corpus_is_used():
    """400 clips of 2s is 800 seconds; at 4s per block that is ~200 blocks.
    Eight was a hardcoded cap."""
    blocks = blocks_of(_clips(400), seconds=4.0)

    assert len(blocks) > 100, (
        f"{len(blocks)} blocks from 400 clips — the cap is still discarding "
        f"most of the imposter corpus"
    )


def test_a_cap_can_still_be_asked_for():
    """Explicitly, by a caller that wants a quick answer — not by default."""
    assert len(blocks_of(_clips(400), seconds=4.0, count=8)) == 8


def test_the_blocks_are_the_length_asked_for():
    for block in blocks_of(_clips(20), seconds=4.0)[:5]:
        assert len(block) == pytest.approx(4.0 * 16_000, rel=0.01)


def test_a_short_corpus_still_yields_what_it_can():
    blocks = blocks_of(_clips(3), seconds=4.0)
    assert 0 < len(blocks) <= 2


def test_no_clip_is_used_twice():
    """Reusing audio inflates the sample count without adding information,
    which is the same lie as the cap in the other direction."""
    clips = _clips(10)
    blocks = blocks_of(clips, seconds=4.0)

    total = sum(len(b) for b in blocks)
    assert total <= sum(len(c) for c in clips)


def test_an_empty_corpus_is_empty_not_an_error():
    assert blocks_of([], seconds=4.0) == []
