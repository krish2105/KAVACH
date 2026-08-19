""""Hey there." alone transcribes as "Heed Elm." — the phrase needs its command.

The wake word stopped firing. The daemon was hearing fine: eight bursts
transcribed at 11:14, none matched, and the user fell back to push-to-talk a
minute later. The machinery was not the problem — a `say` test through the
speakers fired on the first attempt, and the phrase transcribes 10/10 across
ten voices including Indian-accented ones.

The difference is whether a command follows it:

    voice     "Hey there."          "Hey there, open Notes."
    Rishi     'Heed Elm.'      ✗    'Hey there, open notes.'   ✓
    Veena     'Heed Elm.'      ✗    'Hey there, open notes.'   ✓
    Alex      'Heed Elm.'      ✗    'Hey there, open notes.'   ✓
    Daniel    'Hey there.'     ✓    'Hey there, open notes.'   ✓

**Three of four voices, including an American one, so this is not accent.**
A ~0.6s two-word burst is too little for whisper to commit to, and it
guesses. With the command attached the burst is ~2s and it is right every
time.

`HANG_S` was 0.35s, which closes on the pause after "hey there" and hands
the matcher the isolated phrase — its worst case.

**This is the exact opposite of the conclusion reached for "Kavach", and
correctly so.** The failure modes are opposite:

* a **rare** word is *dropped* when a sentence follows it, because whisper
  has better candidates for the audio — so "Kavach" needed isolation;
* **common** words are *mangled* when they stand alone, because two short
  words carry no context — so "hey there" needs the sentence.

The lesson generalises: the right hang depends on whether the wake phrase
gains or loses from context, and that is a property of the phrase, not a
constant to tune by feel.

`heed elm` is also added as an **exact adjacent pair**, so the isolated case
works too — nobody says it, and adding `heed` and `elm` to the per-word
lists instead would have matched "heed their advice".
"""

import numpy as np
import pytest

from kavach.voice.wakewhisper import PHRASE_ALTERNATIVES, Segmenter, matches_wake


def _blocks(rng, pattern):
    out = []
    for kind, count in pattern:
        for _ in range(count):
            scale = 0.0005 if kind == "q" else 0.25
            out.append(rng.normal(0, scale, 1600).astype(np.float32))
    return out


# ═══ the phrase and its command must arrive together ═══

def test_the_hang_keeps_the_command_attached():
    """0.35s closes on the pause after "hey there" and leaves the matcher a
    0.6s burst, which three of four voices transcribe as "Heed Elm."."""
    assert Segmenter.HANG_S >= 0.6, (
        f"HANG_S={Segmenter.HANG_S} splits 'hey there' from the command, and "
        f"the phrase alone is whisper's worst case"
    )


def test_a_natural_pause_does_not_split_the_burst():
    """The behaviour, not the constant. ~0.4s between phrase and command."""
    rng = np.random.default_rng(0)
    seg = Segmenter()

    bursts = []
    for block in _blocks(rng, [("q", 12), ("l", 6), ("q", 4), ("l", 8),
                               ("q", 14)]):
        out = seg.push(block)
        if out is not None:
            bursts.append(out)

    assert len(bursts) == 1, (
        f"{len(bursts)} bursts — the phrase was separated from its command"
    )


def test_a_real_stop_still_ends_the_burst():
    """Longer, not absent. Someone who finishes speaking must not have their
    next sentence glued on."""
    rng = np.random.default_rng(1)
    seg = Segmenter()

    bursts = []
    for block in _blocks(rng, [("q", 10), ("l", 6), ("q", 25),
                               ("l", 6), ("q", 15)]):
        out = seg.push(block)
        if out is not None:
            bursts.append(out)

    assert len(bursts) == 2, f"{len(bursts)} across a 2.5s silence"


# ═══ the isolated case, covered by what it actually produces ═══

@pytest.mark.parametrize("said", [
    "Heed Elm.",
    "heed elm",
    "Heed Elm, what time is it?",
])
def test_the_observed_mishearing_wakes_it(said):
    """What this microphone and three of four voices produce for the phrase
    spoken alone. Observed, not invented."""
    assert matches_wake(said), said


@pytest.mark.parametrize("said", [
    "heed their advice about the roof",
    "you should heed the warning",
    "the elm tree is over there",
    "heed it, and there we are",
])
def test_the_pair_does_not_leak_into_ordinary_speech(said):
    """Why it is an exact adjacent pair rather than `heed` and `elm` added to
    the per-word lists: "heed their advice" would have matched, and people
    say that."""
    assert not matches_wake(said), said


def test_the_pairs_are_declared_and_exact():
    assert ("heed", "elm") in PHRASE_ALTERNATIVES
    for pair in PHRASE_ALTERNATIVES:
        assert len(pair) == 2, "a pair is two adjacent words, in order"


# ═══ the real phrase still works, and ordinary speech still does not ═══

@pytest.mark.parametrize("said", [
    "hey there",
    "Hey there, what time is it?",
    "Hey there, open notes.",
])
def test_the_phrase_still_wakes_it(said):
    assert matches_wake(said), said


@pytest.mark.parametrize("said", [
    "hey, are you there?",
    "is there anything else",
    "they were there yesterday",
    "put it over there",
    "hey Sam, how are you",
    "Available on Amazon, a YouTube channel.",
])
def test_ordinary_speech_stays_silent(said):
    assert not matches_wake(said), said
