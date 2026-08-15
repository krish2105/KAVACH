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


# ═══ what counts as hearing the wake phrase ═══
#
# **These asserted "kavach" spellings until 2026-08-16.** The wake word was
# changed to "hey there" by the user after seven attempts at making a
# Sanskrit word readable by whisper — it was dropped 7/7 inside a sentence
# and rendered 'cabbage', 'go watch', 'coverage' or 'कवच' on its own.
#
# The full phrase behaviour lives in `test_wake_phrase.py`; what stays here
# is the pairing with the detector below, so this file still tests one thing
# end to end.

@pytest.mark.parametrize("said", [
    "hey there",
    "Hey there.",
    "Hey there, what time is it?",
    "hey there open notes",
])
def test_the_wake_phrase_is_recognised(said):
    assert matches_wake(said), said


@pytest.mark.parametrize("said", [
    "what time is it",
    "open the notes app",
    "I was just thinking about lunch",
    "hey",                          # half of it
    "is there anything else",       # the other half, in ordinary use
    "Kavach.",                      # the wake word this replaced
])
def test_ordinary_speech_does_not_wake_it(said):
    """A false wake starts a turn nobody asked for. With a phrase made of
    two common words this matters more than it did, which is why adjacency
    is required — see test_wake_phrase.py."""
    assert not matches_wake(said), said


def test_the_threshold_is_stricter_than_it_was_for_a_rare_word():
    """0.70 was safe for "kavach", whose neighbours are nonsense. "hey" sits
    next to "they" and "there" next to "where", so the same looseness would
    wake on ordinary speech."""
    from kavach.voice.wakewhisper import MATCH_RATIO

    assert MATCH_RATIO >= 0.8


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


#: Enough 100ms silent blocks to close a burst, derived from the constant
#: rather than written out.
#:
#: These said `range(6)` — 0.6s — which was "comfortably more than
#: HANG_S=0.35". Raising the hang to 0.7s (a comma pause was splitting
#: "Kavach, what time is it?" into two bursts) made 0.6s no longer enough
#: and three tests went red on their fixture rather than on their claim.
#: Every assertion below is unchanged; only the number of blocks is, and it
#: is now computed so the next change to HANG_S cannot break them again.
def _closing_silence() -> int:
    from kavach.voice.wakewhisper import Segmenter as _S
    return int(_S.HANG_S / 0.1) + 2


def test_a_burst_is_returned_once_it_ends():
    seg = Segmenter()
    for _ in range(3):
        assert seg.push(block(0.1, 0.0005)) is None
    for _ in range(8):                      # ~0.8s of speech
        assert seg.push(block(0.1, 0.08)) is None
    utterance = None
    for _ in range(_closing_silence()):                      # trailing silence closes it
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
    for _ in range(_closing_silence()):
        if first is None:
            first = seg.push(block(0.1, 0.0005))
    assert first is not None

    for _ in range(_closing_silence()):
        assert seg.push(block(0.1, 0.0005)) is None, "the same burst came back"


def test_a_very_short_sound_is_not_a_burst():
    """A key click or a cough should not cost a transcription."""
    seg = Segmenter()
    seg.push(block(0.05, 0.09))
    for _ in range(_closing_silence()):
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

    def __init__(self, text: str = "hey there"):
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
    """Push a burst, then poll until the worker has had its say.

    Scoring moved off the calling thread — a 1.9s inference on the microphone
    thread was eating the words after the wake word — so a wake is reported by
    a later push, not the one that closed the burst. The bounded wait keeps
    this deterministic rather than dependent on scheduling.
    """
    import time

    fired = None
    for _ in range(8):
        if fired is None:
            fired = detector.push(block(0.1, level))
    for _ in range(6):
        if fired is None:
            fired = detector.push(block(0.1, 0.0005))

    deadline = time.time() + 2.0
    while fired is None and time.time() < deadline:
        time.sleep(0.02)
        fired = detector.push(block(0.1, 0.0005))
    return fired


def test_hearing_the_phrase_fires():
    detector = WhisperWakeDetector(stt=FakeSTT("hey there"))

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
# The old corpus here was 90 seconds of the user saying "Kavach", which
# whisper rendered eight different ways ('Gavach.', 'Gaavj.', 'Hey gauj.',
# 'Thik hai vajah.'). That variety is exactly why the wake word was
# replaced, and the corpus went with it.
#
# The negatives are kept as-is. They are real speech from this room,
# including a YouTube advert, and a wake phrase of two common English words
# has more to prove against them than a rare one did.

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


@pytest.mark.parametrize("said", LIVE_NOT)
def test_the_live_negatives_stay_silent(said):
    """Zero false wakes across the whole live run, and it has to stay that
    way: a false wake starts a turn nobody asked for."""
    assert not matches_wake(said), said
