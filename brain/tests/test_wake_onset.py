"""Two corrections the user's second `kavach-wakecheck` run forced.

Their run, after `HANG_S` went to 0.7s so the phrase would stay one burst:

    heard  ''                    ✗                        (3.4s)
    heard  'What time is it?'    ✗ 'what'→'kawach' 0.40   (2.1s)
    heard  'What time is it?'    ✗                        (1.9s)
    heard  'What time is it?'    ✗                        (1.3s)
    heard  'What time is it?'    ✗                        (2.0s)

**The merge worked and it did not help.** The bursts are 1.9–2.1s, so the
phrase is whole — and whisper still writes only "What time is it?". The word
is in the audio and not in the transcript.

Their voice, split by case:

    "Kavach" inside a sentence   dropped 7/7, at both hang settings
    "Kavach" on its own          'cabbage, cabbage' ✓, and 17–24 of 42 clips

**Isolated is the only case that works for this voice**, so merging the
phrase works against them. `HANG_S` goes back to 0.35s. The reasoning behind
0.7 was sound in the abstract — a comma pause was splitting the phrase — and
the next measurement contradicted it, which is the measurement's privilege.

`initial_prompt` was tried and is a trap worth recording: it lifts the
isolated clips from 17/42 to 24/42 and **breaks the sentence**, because
whisper will not repeat what is already in its context.

    prompt=None       42 clips 17/42   sentence: 'Kavach, what time is it?'
    prompt='Kavach.'  42 clips 22/42   sentence: 'What time is it?'

Truncating to the head was tried too, and gives 'coverage.' at 1.0s.

The second correction is the onset. `Segmenter.push` only kept a block once
it was **loud**, so every quiet block before the first loud one was
discarded — and a word beginning with a soft consonant starts below the
floor. The burst then begins partway into "kav-ACH", which is a fragment
whisper drops or renders as 'coverage'.

`MicStream.preroll(500)` exists and the main turn path already uses it for
exactly this reason. The wake segmenter had none.
"""

import numpy as np
import pytest

from kavach.voice.wakewhisper import Segmenter


def _blocks(rng, pattern):
    """pattern is a list of (kind, count); 'q' quiet, 'r' ramp, 'l' loud."""
    out = []
    for kind, count in pattern:
        for i in range(count):
            if kind == "q":
                out.append(rng.normal(0, 0.0005, 1600).astype(np.float32))
            elif kind == "r":                       # a soft word onset
                out.append(rng.normal(0, 0.004, 1600).astype(np.float32))
            else:
                out.append(rng.normal(0, 0.25, 1600).astype(np.float32))
    return out


def test_the_hang_is_short_enough_to_isolate_the_wake_word():
    """This user's wake word is only recognised on its own. A hang long
    enough to glue it to the command loses it entirely."""
    assert Segmenter.HANG_S <= 0.45, (
        f"HANG_S={Segmenter.HANG_S} merges 'Kavach,' with the command, and "
        f"whisper drops the leading rare word 7/7 for this voice"
    )


def test_there_is_a_preroll():
    assert Segmenter.PREROLL_BLOCKS >= 1


def test_a_soft_onset_is_kept():
    """The failure: a word starting below the floor had its beginning
    discarded, and whisper was handed a fragment."""
    rng = np.random.default_rng(0)
    seg = Segmenter()

    bursts = []
    for block in _blocks(rng, [("q", 12), ("r", 2), ("l", 8), ("q", 10)]):
        out = seg.push(block)
        if out is not None:
            bursts.append(out)

    assert bursts, "no burst at all"
    seconds = len(bursts[0]) / 16_000
    assert seconds >= 1.0, (
        f"burst is {seconds:.2f}s — the 0.2s ramp before the loud part was "
        f"dropped, which is the start of the word"
    )


def test_the_preroll_does_not_leak_into_a_later_burst():
    """Stale audio prepended to an unrelated burst would put words in front
    of speech that never had them."""
    rng = np.random.default_rng(1)
    seg = Segmenter()

    bursts = []
    for block in _blocks(rng, [("q", 12), ("l", 6), ("q", 12),
                               ("l", 6), ("q", 12)]):
        out = seg.push(block)
        if out is not None:
            bursts.append(out)

    assert len(bursts) == 2, f"{len(bursts)} bursts"
    for burst in bursts:
        assert len(burst) / 16_000 < 2.0, "a burst absorbed the whole gap"


def test_silence_alone_never_produces_a_burst():
    """A pre-roll buffer must not become a way for a quiet room to reach the
    transcriber — that is the ambient capture this project cut."""
    rng = np.random.default_rng(2)
    seg = Segmenter()

    for block in _blocks(rng, [("q", 60)]):
        assert seg.push(block) is None


def test_reset_clears_the_preroll():
    """§7: audio that was not acted on must not linger. `reset()` runs after
    every turn."""
    rng = np.random.default_rng(3)
    seg = Segmenter()
    for block in _blocks(rng, [("q", 5), ("r", 2)]):
        seg.push(block)

    seg.reset()

    assert not seg._parts
    assert not list(seg._preroll)


def test_a_fast_single_word_is_not_dropped_before_transcription():
    """`MIN_UTTERANCE_S` is checked against accumulated *loud* time, so a
    quickly-spoken "Kavach" (~0.3s) was discarded before whisper ever saw it
    — invisibly, since nothing is logged for a burst that never opens.

    That is why the user's runs showed the command burst and no wake-word
    burst at all: not a matching failure, not a transcription failure, a
    burst that was thrown away one layer earlier."""
    rng = np.random.default_rng(4)
    seg = Segmenter()

    bursts = []
    for block in _blocks(rng, [("q", 12), ("l", 3), ("q", 10)]):   # 0.3s loud
        out = seg.push(block)
        if out is not None:
            bursts.append(out)

    assert bursts, (
        f"a 0.3s word was dropped: MIN_UTTERANCE_S={Segmenter.MIN_UTTERANCE_S}"
    )


def test_a_click_is_still_not_a_burst():
    """The floor is lower, not gone. One block is a key press."""
    rng = np.random.default_rng(5)
    seg = Segmenter()

    for block in _blocks(rng, [("q", 12), ("l", 1), ("q", 10)]):   # 0.1s
        assert seg.push(block) is None
