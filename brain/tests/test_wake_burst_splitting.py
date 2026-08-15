"""The pause after "Kavach," was ending the burst before the command arrived.

`kavach-wakecheck`, run by the user, saying "Kavach, what time is it?":

    heard  'What time is it?'                    ✗ closest 'what'→'kawach' 0.40  (1.1s)
    heard  'cabbage, cabbage'                    ✗ closest 'cabbage'→'gavaj' 0.33 (1.9s)
    heard  'What time is it?'                    ✗ closest 'what'→'kawach' 0.40  (1.0s)
    heard  'What time is it?'                    ✗ closest 'what'→'kawach' 0.40  (1.2s)
    0/8 burst(s) woke it.

**The wake word is missing from the front of three transcripts**, and those
bursts are 1.0–1.2s — too short to contain both the word and the sentence.
`HANG_S` was 0.35s, and the natural comma pause after a wake word is longer
than that, so "Kavach," closed its own burst and "what time is it?" opened a
new one.

A one-second isolated word is whisper's worst case, which is the same reason
20 of the user's 42 one-second recordings transcribed to nothing at all. The
bursts that *did* fire in testing were 2.0–2.1s — the whole phrase together.

So this was never a matching problem. The matcher was being handed audio
with the word already removed.

**This is the third explanation for a missing first word in this project**,
and the first one supported by a measurement. The earlier note recorded that
the segmenter "keeps the onset block at every offset" and told the next
reader not to re-investigate — true, and about a different thing: the onset
of a burst is kept, and the burst simply started too late.

`cabbage` is added as an **exact** target, not a fuzzy one. It is what this
microphone writes for an isolated "Kavach", and it is also an ordinary
English word: fuzzy-matched at 0.70 it would drag in `garbage` (0.714) and
`carriage` (0.667 — close enough to worry). An exact target costs nothing
and cannot spread.
"""

import pytest

from kavach.voice.wakewhisper import (
    EXACT_TARGETS,
    MATCH_RATIO,
    Segmenter,
    matches_wake,
)

import numpy as np


# ═══ the burst must survive a comma ═══

def test_the_hang_bridges_a_natural_pause():
    """0.35s does not. Measured from the user's own run: the wake word and
    the command arrived as separate bursts."""
    assert Segmenter.HANG_S >= 0.6, (
        f"HANG_S={Segmenter.HANG_S} closes on the pause after 'Kavach,' and "
        f"the command becomes a different burst"
    )


def test_there_is_room_for_the_word_plus_the_pause():
    """Written first as `MAX_UTTERANCE_S >= 2.5 + HANG_S`, which is the wrong
    requirement. **The wake word sits at the front of a burst**, so a burst
    cut at MAX loses the tail of the command and never the word — and the
    command is re-recorded by the turn that follows anyway.

    What actually has to hold is that the word and the pause after it fit
    comfortably, or the cut lands inside the thing being detected."""
    assert Segmenter.MAX_UTTERANCE_S >= Segmenter.HANG_S * 3


def test_speech_with_a_pause_in_it_stays_one_burst():
    """The behaviour, not the constant."""
    seg = Segmenter()
    rng = np.random.default_rng(0)
    loud = lambda n: (rng.normal(0, 0.25, n)).astype(np.float32)
    quiet = lambda n: (rng.normal(0, 0.0005, n)).astype(np.float32)

    bursts = []
    for block in ([quiet(1600)] * 10                     # settle the floor
                  + [loud(1600)] * 6                     # "Kavach"
                  + [quiet(1600)] * 4                    # the comma, 0.4s
                  + [loud(1600)] * 8                     # "what time is it"
                  + [quiet(1600)] * 12):                 # done
        out = seg.push(block)
        if out is not None:
            bursts.append(out)

    assert len(bursts) == 1, (
        f"{len(bursts)} bursts — the pause split the phrase, which is how the "
        f"wake word got separated from the command"
    )
    assert len(bursts[0]) / 16_000 >= 1.0


def test_a_long_gap_still_ends_the_burst():
    """The hang is longer, not absent. Someone who stops talking must not
    have their next sentence glued on."""
    seg = Segmenter()
    rng = np.random.default_rng(1)
    loud = lambda n: (rng.normal(0, 0.25, n)).astype(np.float32)
    quiet = lambda n: (rng.normal(0, 0.0005, n)).astype(np.float32)

    bursts = []
    for block in ([quiet(1600)] * 10 + [loud(1600)] * 6
                  + [quiet(1600)] * 25                   # 2.5s — a real stop
                  + [loud(1600)] * 6 + [quiet(1600)] * 15):
        out = seg.push(block)
        if out is not None:
            bursts.append(out)

    assert len(bursts) == 2, f"{len(bursts)} bursts across a 2.5s silence"


# ═══ cabbage, exactly ═══

def test_the_spelling_this_microphone_produces_is_recognised():
    assert matches_wake("cabbage, cabbage")
    assert matches_wake("Cabbage.")


@pytest.mark.parametrize("word", [
    "garbage",      # 0.714 against cabbage — would fuzzy-match
    "carriage",     # 0.667
    "baggage", "luggage", "package", "village", "damage", "courage",
])
def test_words_that_merely_look_like_it_stay_silent(word):
    """This is why `cabbage` is exact-only. A false wake starts a turn
    nobody asked for, and `garbage` is a word people say."""
    assert not matches_wake(f"put the {word} outside please"), word


def test_exact_targets_are_declared_separately():
    """Kept apart so the distinction is visible: the kavach family is
    fuzzy-matched because whisper mangles it in unpredictable ways; an
    ordinary English word is not, because it has neighbours."""
    assert "cabbage" in EXACT_TARGETS
    assert MATCH_RATIO == 0.70, "the fuzzy threshold is measured; see the module"


def test_the_ordinary_negatives_still_do_not_wake_it():
    for said in ("The weather today is quite pleasant and calm.",
                 "Available on Amazon, a YouTube channel.",
                 "I'll call you back in a moment.",
                 "Can you catch the ball please?"):
        assert not matches_wake(said), said
