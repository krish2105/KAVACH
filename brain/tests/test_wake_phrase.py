"""The wake word is "hey there" — a phrase, chosen because "Kavach" cannot be heard.

Seven attempts, four trained ONNX models and five whisper fixes did not make
"Kavach" work on this microphone. The final measurements say why, and none
of them is about the code:

    "Kavach" inside a sentence   dropped 7/7 by whisper
    "Kavach" on its own          17–24 of 42, and often ''
    whisper's spellings          'cabbage', 'go watch', 'coverage', 'कवच'

It is a Sanskrit word whisper has no representation for, so it either drops
it, transliterates it inconsistently, or writes it in the wrong script.
Every fix downstream was fixing a symptom.

**"hey there" is two ordinary English words.** Whisper transcribes them the
same way every time, they take ~0.6s so nothing drops them for being too
short, and there is no rare token to lose.

**The cost is the obvious one and it is real: people say "hey there".** So
matching is stricter than it was for a single rare word:

* the words must be **adjacent**, in order — "hey" and "there" scattered in
  a sentence is not the wake phrase;
* each word is matched at a **higher ratio** than the old single-word
  threshold, because these have real English neighbours ("hey" / "they",
  "there" / "their" / "where") where "kavach" had only nonsense ones.

The speaker gate at 0.300 sits behind this, so a false wake from a video or
another person still cannot act. That is the second line, not the first.
"""

import pytest

from kavach.voice.wakewhisper import WAKE_PHRASE, matches_wake


def test_the_phrase_is_what_was_asked_for():
    assert WAKE_PHRASE == "hey there"


# ═══ it wakes ═══

@pytest.mark.parametrize("said", [
    "Hey there.",
    "hey there",
    "Hey there, what time is it?",
    "Hey there. Open Notes.",
    "hey there, search youtube for wrestling",
    "Hey, there!",                       # whisper's comma
    "Hey there what time is it",         # no punctuation at all
])
def test_the_phrase_wakes_it(said):
    assert matches_wake(said), said


@pytest.mark.parametrize("said", [
    "Hey their, what time is it?",       # homophone whisper really produces
    "Hey there's the time",              # 'there's' — same start
    "Hay there, open notes.",
])
def test_near_spellings_still_wake_it(said):
    """Whisper is consistent on common words but not perfect, and the
    homophones of "there" are exactly what it reaches for."""
    assert matches_wake(said), said


# ═══ it must not wake ═══

@pytest.mark.parametrize("said", [
    "The weather today is quite pleasant and calm.",
    "Available on Amazon, a YouTube channel.",
    "I'll call you back in a moment.",
    "Can you catch the ball please?",
    "What time is it?",
    "Cabbage.",                          # the old wake word's spelling
    "go watch it",
    "coverage",
])
def test_ordinary_speech_stays_silent(said):
    assert not matches_wake(said), said


@pytest.mark.parametrize("said", [
    "hey",                               # one half
    "there",                             # the other half
    "hey, are you there?",               # both words, not adjacent
    "is there anything else",
    "over there by the door",
    "they were there yesterday",
    "hey Sam, put it over there",        # both present, four words apart
])
def test_the_words_must_be_adjacent(said):
    """"hey" and "there" are common enough that either alone, or both
    scattered, must not be enough. This is the whole false-wake surface."""
    assert not matches_wake(said), said


def test_it_is_not_case_or_punctuation_sensitive():
    for said in ("HEY THERE", "Hey, there.", "  hey   there  "):
        assert matches_wake(said), said


# ═══ the old wake word is gone ═══

def test_kavach_no_longer_wakes_it():
    """Replaced, not added alongside. Keeping 'cabbage' and 'go watch' as
    targets would carry the old false-wake surface into a phrase chosen to
    avoid it."""
    for said in ("Kavach.", "kavach what time is it", "cabbage, cabbage"):
        assert not matches_wake(said), said
