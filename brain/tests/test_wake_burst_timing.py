"""Burst timing — what survived the move from "Kavach" to "hey there".

This replaces `test_wake_burst_splitting.py`, which was written for the old
wake word and was mostly about symptoms of a word whisper could not read:
`cabbage` as an exact target, and an argument about whether the comma pause
after "Kavach," should split it from the command.

**What stays is the timing, because it is about speech and not about which
words are in it.** A wake phrase still has to reach the transcriber: not
discarded for being too short, onset not clipped, and a key click still not
worth a transcription.

The deleted argument is worth one paragraph as a record. It ran: 0.35s
splits the phrase and loses the word → 0.7s merges it and whisper drops the
word anyway → 0.35s again, because isolated was the only case that worked
for this voice. **All three observations were true and none of them
mattered**, because the word itself was unreadable. Two rounds of timing
work went into a problem that was never timing.

"hey there" takes ~0.6s as one unit, so it needs neither merging nor
splitting. The timing simply has to not throw it away.
"""

import numpy as np
import pytest

from kavach.voice.wakewhisper import Segmenter, matches_wake


def _blocks(rng, pattern):
    out = []
    for kind, count in pattern:
        for _ in range(count):
            if kind == "q":
                out.append(rng.normal(0, 0.0005, 1600).astype(np.float32))
            elif kind == "r":
                out.append(rng.normal(0, 0.004, 1600).astype(np.float32))
            else:
                out.append(rng.normal(0, 0.25, 1600).astype(np.float32))
    return out


def test_a_two_word_phrase_clears_the_minimum():
    """"hey there" is ~0.6s of speech. The floor must leave room for someone
    saying it quickly, which is what dropped the old one-word wake."""
    assert Segmenter.MIN_UTTERANCE_S <= 0.3


def test_the_phrase_reaches_the_transcriber():
    rng = np.random.default_rng(0)
    seg = Segmenter()

    bursts = []
    for block in _blocks(rng, [("q", 12), ("l", 6), ("q", 10)]):
        out = seg.push(block)
        if out is not None:
            bursts.append(out)

    assert len(bursts) == 1, f"{len(bursts)} bursts for one phrase"
    assert len(bursts[0]) / 16_000 >= 0.6


def test_a_soft_onset_is_still_kept():
    """"hey" starts with a breathy /h/ that sits below the floor. The
    pre-roll is what keeps it."""
    rng = np.random.default_rng(1)
    seg = Segmenter()

    bursts = []
    for block in _blocks(rng, [("q", 12), ("r", 2), ("l", 6), ("q", 10)]):
        out = seg.push(block)
        if out is not None:
            bursts.append(out)

    assert bursts, "no burst at all"
    assert len(bursts[0]) / 16_000 >= 0.8, "the soft start was dropped"


def test_a_long_gap_ends_the_burst():
    rng = np.random.default_rng(2)
    seg = Segmenter()

    bursts = []
    for block in _blocks(rng, [("q", 10), ("l", 6), ("q", 25),
                               ("l", 6), ("q", 15)]):
        out = seg.push(block)
        if out is not None:
            bursts.append(out)

    assert len(bursts) == 2, f"{len(bursts)} bursts across a 2.5s silence"


def test_a_click_is_not_a_burst():
    rng = np.random.default_rng(3)
    seg = Segmenter()

    for block in _blocks(rng, [("q", 12), ("l", 1), ("q", 10)]):
        assert seg.push(block) is None


# ═══ the old wake word must not linger ═══

@pytest.mark.parametrize("said", [
    "cabbage, cabbage",
    "put the garbage outside",
    "go watch it",
    "coverage",
    "Kavach.",
])
def test_nothing_from_the_old_wake_word_still_fires(said):
    """`cabbage` was a wake spelling for one commit, because it is what this
    microphone wrote for "Kavach". Carrying those forward would keep the old
    false-wake surface under a phrase chosen to avoid it."""
    assert not matches_wake(said), said
