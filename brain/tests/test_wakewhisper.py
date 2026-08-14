"""A wake word that uses the thing which demonstrably hears this microphone.

Four trained ONNX models have failed on this machine, and the reason is in the
project's own measurements. One utterance, one recording, two readers:

    the wake model on the file                 0.858
    the wake model on the mic recording        0.019
    whisper on that same mic recording         "Kavec, Kavec, testing 1, 2, 3."

Whisper reads it perfectly at rms 0.09. The wake model scores it at noise. v4
finally fit the user's voice (median 0.830 on its training takes) and then
failed to generalise to new speech (median 0.034) — 42 unique utterances is
not enough, and copying them 25 times only deepened the memorisation.

So this stops trying to make the deaf model hear and uses the one that already
does. VAD gates the microphone; when a speech burst ends, a small local Whisper
transcribes just that burst and the text is matched against the wake word.

**Privacy (§7).** Audio is dropped as soon as a burst is scored, and the
transcript of a burst that did not match is never logged, published or kept —
"never log or transmit wake-word audio that wasn't acted on" applies just as
much to text derived from it. Nothing here writes to disk.
"""

import numpy as np
import pytest

from kavach.voice.wakewhisper import (
    MATCH_RATIO,
    Segmenter,
    WhisperWakeDetector,
    matches_wake,
)

SAMPLE_RATE = 16_000


# ═══ what counts as hearing the name ═══

@pytest.mark.parametrize("said", [
    "kavach",
    "Kavach.",
    "hey kavach",
    "Hey, KAVACH!",
    # What Whisper actually returned for a real recording of the wake word.
    "Kavec, Kavec, testing 1, 2, 3.",
    "kavac",
    "kawach",
    "cavach",
    "kovach",
    "ok kavach what time is it",
])
def test_the_wake_word_is_recognised_however_it_was_transcribed(said):
    """Whisper spells a Sanskrit word by ear. Requiring an exact match would
    fail on the one transcript we have actually measured."""
    assert matches_wake(said), said


@pytest.mark.parametrize("said", [
    "cover it",
    "car wash",
    "come back",
    "catch it",
    "what time is it",
    "open my calendar for tomorrow",
    "delete the draft in notes",
    "call Vatsal",
    "the coverage was good",
    "package arrived",
    "kayak",
    "",
    "   ",
])
def test_ordinary_speech_does_not_wake_it(said):
    """A false wake starts recording the room unprompted, so these matter more
    than the misses. Every one of these is a phrase the user actually says."""
    assert not matches_wake(said), said


def test_the_threshold_sits_between_what_was_measured():
    """Chosen from data, not taste. Against the targets:

        kavec   0.727   ← the real transcript, must match
        catch   0.667   ← must not
    """
    assert 0.667 < MATCH_RATIO <= 0.727


# ═══ finding the burst to transcribe ═══

def block(seconds: float, level: float) -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.normal(0, level, int(seconds * SAMPLE_RATE)).astype(np.float32)


def test_silence_alone_produces_nothing():
    """Transcribing an empty room, continuously, would be both wasteful and
    exactly the ambient-audio behaviour that was cut from this project."""
    seg = Segmenter()
    for _ in range(50):
        assert seg.push(block(0.1, 0.0005)) is None


def test_a_burst_is_returned_once_it_ends():
    seg = Segmenter()
    for _ in range(3):
        assert seg.push(block(0.1, 0.0005)) is None
    for _ in range(8):                      # ~0.8s of speech
        assert seg.push(block(0.1, 0.08)) is None
    utterance = None
    for _ in range(6):                      # trailing silence closes it
        if utterance is None:
            utterance = seg.push(block(0.1, 0.0005))

    assert utterance is not None
    length = len(utterance) / SAMPLE_RATE
    assert 0.6 < length < 2.0, f"{length:.2f}s"


def test_a_burst_is_only_returned_once():
    seg = Segmenter()
    for _ in range(8):
        seg.push(block(0.1, 0.08))
    first = None
    for _ in range(6):
        if first is None:
            first = seg.push(block(0.1, 0.0005))
    assert first is not None

    for _ in range(6):
        assert seg.push(block(0.1, 0.0005)) is None, "the same burst came back"


def test_a_very_short_sound_is_not_a_burst():
    """A key click or a cough should not cost a transcription."""
    seg = Segmenter()
    seg.push(block(0.05, 0.09))
    for _ in range(6):
        assert seg.push(block(0.1, 0.0005)) is None


def test_a_long_burst_is_cut_rather_than_grown_forever():
    """Someone talking on a call must not accumulate a minute of audio in
    memory waiting for a pause."""
    seg = Segmenter()
    out = None
    for _ in range(60):                     # 6 seconds of unbroken speech
        if out is None:
            out = seg.push(block(0.1, 0.08))

    assert out is not None, "a continuous talker was buffered indefinitely"
    assert len(out) / SAMPLE_RATE <= Segmenter.MAX_UTTERANCE_S + 0.3


# ═══ the detector ═══

class FakeSTT:
    """Stands in for whisper. Records what it was asked to transcribe."""

    def __init__(self, text: str = "kavach"):
        self.text = text
        self.calls = 0

    def transcribe(self, audio, **kwargs):
        self.calls += 1

        class Result:
            pass

        result = Result()
        result.text = self.text
        return result


def speech_then_silence(detector, level: float = 0.08) -> object:
    fired = None
    for _ in range(8):
        if fired is None:
            fired = detector.push(block(0.1, level))
    for _ in range(6):
        if fired is None:
            fired = detector.push(block(0.1, 0.0005))
    return fired


def test_hearing_the_name_fires():
    detector = WhisperWakeDetector(stt=FakeSTT("Hey Kavach"))

    assert speech_then_silence(detector) is not None


def test_hearing_something_else_does_not_fire():
    detector = WhisperWakeDetector(stt=FakeSTT("what time is it"))

    assert speech_then_silence(detector) is None


def test_silence_is_never_transcribed():
    """The cost that decides whether this is viable at all: a quiet room must
    not run whisper at 10 times a second."""
    stt = FakeSTT()
    detector = WhisperWakeDetector(stt=stt)
    for _ in range(60):
        detector.push(block(0.1, 0.0005))

    assert stt.calls == 0


def test_a_burst_costs_exactly_one_transcription():
    stt = FakeSTT()
    detector = WhisperWakeDetector(stt=stt)
    speech_then_silence(detector)

    assert stt.calls == 1


def test_a_failed_transcription_never_takes_down_the_loop():
    """This runs inside the microphone thread. An exception here would end
    listening altogether, which is worse than missing a wake word."""

    class Broken:
        def transcribe(self, audio, **kwargs):
            raise RuntimeError("whisper fell over")

    detector = WhisperWakeDetector(stt=Broken())

    assert speech_then_silence(detector) is None  # must not raise


def test_nothing_is_kept_after_a_burst_is_scored(caplog):
    """§7: what KAVACH heard and did not act on leaves no trace. The transcript
    of a non-matching burst is the same thing as the audio it came from."""
    import logging

    detector = WhisperWakeDetector(stt=FakeSTT("delete the draft in notes"))
    with caplog.at_level(logging.DEBUG):
        speech_then_silence(detector)

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "delete the draft" not in logged, logged
    assert detector.buffered_seconds == 0.0


# ═══ what this microphone actually produces ═══
#
# Measured live, 90 seconds, the user speaking normally — 30 bursts. The wake
# word was heard every time and spelled a different way almost every time:
#
#     'Hai kavach, vah time is it.'      'Gavach.'
#     'Avaj open notes.'                 'Hega vaj.'
#     'Ek avach.'                        'Gaavj.'
#     'Hey gauj.'                        'ayka vaj, what time is it?'
#
# The STT in use is a Hinglish fine-tune, and it renders the Sanskrit कवच with
# a j ending — avaj, gauj, vaj, gavaj. Matching only `kavach` caught 3 of 12.
# These are not invented spellings: every one below is a transcript this
# machine produced for this voice.

LIVE_WAKE = [
    "Hai kavach, vah time is it.",
    "Avaj open notes.",
    "Ek avach.",
    "Hey gauj.",
    "Gavach.",
    "Gaavj.",
    "ey gauj.",
    "Thik hai vajah.",
]

#: From the same 90 seconds. Ordinary speech, and one YouTube advert the room
#: happened to contain — all of which must stay silent.
LIVE_NOT = [
    "reason.",
    "ambi",
    "time is it cover",
    "I'll call you back.",
    "Hai.",
    "Haan. Aapka",
    "Available on Amazon. A YouTube channel.",
    "Double 08:30.",
    "e.",
]


@pytest.mark.parametrize("said", LIVE_WAKE)
def test_the_spellings_this_microphone_produces_are_recognised(said):
    assert matches_wake(said), said


@pytest.mark.parametrize("said", LIVE_NOT)
def test_the_live_negatives_stay_silent(said):
    """Zero false wakes across the whole live run, and it has to stay that
    way: a false wake starts a turn nobody asked for."""
    assert not matches_wake(said), said
