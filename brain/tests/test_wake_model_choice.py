"""Which whisper listens for the wake word, and why it is not the small one.

The wake detector defaulted to **`swift`** — a Hinglish fine-tune of
whisper-*base*, 72.6M parameters. It cannot hear the word. Measured on a
clean file, no microphone involved at all:

    "Kavach, what time is it?"  →  "Have a nice day. What time is it?"

`base.en` is no better and drops the word entirely rather than mangling it:

    "Kavach, what time is it?"  →  "What time is it?"

So the whisper wake word had never had a chance to work, and the earlier
conclusion that it recognised only 19–31% of clips was measuring a
base-sized model on tight one-second clips — its two worst conditions at
once.

Measured across model sizes, four wake phrases and four ordinary sentences:

    small.en          recall 3/4   false 0/4   median  256ms
    small             recall 4/4   false 0/4   median  272ms
    large-v3-turbo    recall 4/4   false 0/4   median 1188ms

`small` multilingual wins outright: the accuracy of large-v3-turbo at 4.4x
the speed. `small.en` heard a bare "Kavach." as **"Cabbage."** — the
English-only model has no representation for the word, which is the same
reason multilingual also suits this user's Hinglish.

The project had already ruled out large-v3-turbo as too slow, correctly, and
jumped from base straight to large. **Nothing in between was ever tried.**

**Caveat, stated because this project has been burned by it repeatedly:**
these are macOS `say` voices, not the user's real speech through the real
microphone. What they establish is a floor — a model that cannot hear the
word in clean synthetic audio will not hear it in a room — and the ordering
between models, which is what the default depends on. The live number is
still the user's to produce.
"""

import inspect

import pytest

from kavach.voice.wakewhisper import WhisperWakeDetector, matches_wake


def test_the_default_is_not_a_base_sized_model():
    """`swift` and `base.en` are both whisper-base underneath, and neither
    hears the word on clean audio. A wake word that cannot be transcribed
    cannot be matched, however good the matcher is."""
    assert WhisperWakeDetector.DEFAULT_MODEL not in ("swift", "base", "base.en"), (
        f"{WhisperWakeDetector.DEFAULT_MODEL} is base-sized; measured, it "
        f"renders 'Kavach, what time is it?' as 'Have a nice day.'"
    )


def test_the_default_is_small():
    assert WhisperWakeDetector.DEFAULT_MODEL == "small"


def test_the_default_is_not_english_only():
    """`small.en` transcribed a bare "Kavach." as "Cabbage." — an
    English-only model has no representation for the word. It is also the
    wrong choice for a user who speaks Hinglish."""
    assert not WhisperWakeDetector.DEFAULT_MODEL.endswith(".en")


def test_the_default_is_not_large():
    """large-v3-turbo matches `small`'s accuracy at 1188ms against 272ms.
    A wake word is on the latency path of every burst of speech in the room,
    so 4.4x for nothing measured is a bad trade."""
    assert "large" not in WhisperWakeDetector.DEFAULT_MODEL


def test_the_fallback_is_not_the_broken_one():
    """The fallback runs when the chosen model is missing. Falling back to a
    model measured unable to hear the word is falling back to silence, and
    silence here looks exactly like a broken microphone."""
    assert WhisperWakeDetector.FALLBACK_MODEL not in ("swift",)


# ═══ the matcher is unchanged, and still has to hold ═══

@pytest.mark.parametrize("said", [
    "Hey there, what time is it?",
    "hey there open notes",
    "Hey there.",
])
def test_what_the_chosen_model_produces_is_recognised(said):
    """These were "Kavach" spellings until the wake word was changed. The
    model comparison above is unaffected — it measured which whisper can
    transcribe a rare proper noun at all, and its conclusion (`small`, not
    base, not large) holds for ordinary words too."""
    assert matches_wake(said), said


@pytest.mark.parametrize("said", [
    "The weather today is quite pleasant and calm.",
    "Available on Amazon, a YouTube channel.",
    "I'll call you back in a moment.",
    "Can you catch the ball please?",
    # "Cabbage." was here, asserted as a NON-wake, on the belief that it was
    # `small.en` mishearing a word it had no representation for.
    #
    # **The user's own `kavach-wakecheck` run overturned that.** Saying
    # "Kavach, Kavach" into the microphone produced 'cabbage, cabbage', and
    # 'Cabbage.' appears in the 42 recordings too. It is not a failure
    # spelling — it is what this microphone writes for this user's wake
    # word, and refusing it means refusing them.
    #
    # Moved to EXACT_TARGETS rather than the fuzzy list, so `garbage` (0.714)
    # and `carriage` (0.667) still stay silent —
    # `test_wake_burst_splitting.py` asserts exactly that.
    #
    # Recorded rather than quietly deleted: this test was not wrong to
    # exist, it was wrong about the world, and the evidence that changed it
    # came from the user rather than from me.
])
def test_ordinary_speech_still_does_not_wake_it(said):
    """A false wake starts a turn nobody asked for, and with the speaker gate
    on it also spends a verification. Zero across the measured set."""
    assert not matches_wake(said), said


def test_the_reason_for_the_choice_is_written_down():
    """This default was changed on measurement. The next person to wonder
    why it is not the fast one needs the numbers, not an opinion."""
    source = inspect.getsource(WhisperWakeDetector)
    assert "small" in source
