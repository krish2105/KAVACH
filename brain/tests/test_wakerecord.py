"""Recording real wake-word audio to train on.

Every KAVACH wake model so far has been trained entirely on synthesised
speech, and all three are deaf to this microphone — the same utterance scores
0.858 as a file and 0.019 through the mic. Measured tonight with a corrected
ruler, the user's own takes score 0.026 to 0.152 against a 0.30 floor, with
ordinary speech reaching 0.048. The model has never heard a human being.

This module records the audio that fixes that. Its whole job is to refuse bad
takes, because a training set is the one place where a quietly-wrong sample is
never noticed again: it does not fail, it just makes the model slightly worse
in a way nothing downstream can attribute.

Four ways a take is wrong, and each is checked separately:

* **too quiet** — a whisper teaches the model that the wake word is quiet
* **clipping** — a shout teaches it that the wake word is distorted
* **nothing said** — silence labelled "kavach" is a lie in the label column
* **cut off** — speech touching the end of the tape is half a word, which is
  what the calibration ruler was just fixed for

Clips come out **tight around the word**, not padded to a fixed length, and
that was corrected after reading what the trainer actually does:

    if round_idx == 0:
        if is_positive:
            audio = align_clip_to_end(audio, target_length)   # word at the END
        else:
            ...centre-pad or crop...

The library places the clip itself, from a source clip of any length — TTS
positives are just the word. A pre-padded 2.0s clip therefore lands *centred*
while every synthetic positive lands at the end, and the model would see real
and synthetic speech in systematically different positions. Emitting the bare
utterance makes real takes indistinguishable from TTS ones to the pipeline,
which is the entire point.
"""

import numpy as np
import pytest

from kavach.voice.wakerecord import (
    CLIP_SECONDS,
    RECORD_SECONDS,
    SAMPLE_RATE,
    check_take,
    next_index,
    speech_bounds,
)


def take(speech_at: float, speech_len: float = 0.7, level: float = 0.25,
         length: float = RECORD_SECONDS, noise: float = 0.001) -> np.ndarray:
    """A tape with a burst of 'speech' at a given offset, over room tone."""
    rng = np.random.default_rng(0)
    audio = (rng.normal(0, noise, int(length * SAMPLE_RATE))).astype(np.float32)
    start = int(speech_at * SAMPLE_RATE)
    end = min(len(audio), start + int(speech_len * SAMPLE_RATE))
    t = np.arange(end - start) / SAMPLE_RATE
    audio[start:end] += (level * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    return audio


# ═══ finding the speech ═══

def test_speech_bounds_finds_the_burst():
    bounds = speech_bounds(take(speech_at=1.0, speech_len=0.7))

    assert bounds is not None
    start_s, end_s = bounds[0] / SAMPLE_RATE, bounds[1] / SAMPLE_RATE
    assert 0.85 <= start_s <= 1.15, start_s
    assert 1.55 <= end_s <= 1.95, end_s


def test_silence_has_no_speech_bounds():
    rng = np.random.default_rng(1)
    room_tone = rng.normal(0, 0.001, int(RECORD_SECONDS * SAMPLE_RATE)).astype(np.float32)

    assert speech_bounds(room_tone) is None


# ═══ the four refusals ═══

def test_a_silent_take_is_refused():
    """Silence labelled 'kavach' is a wrong label, not a quiet sample."""
    rng = np.random.default_rng(2)
    result = check_take(rng.normal(0, 0.001, int(RECORD_SECONDS * SAMPLE_RATE)).astype(np.float32))

    assert result.ok is False
    assert "nothing" in result.reason.lower() or "quiet" in result.reason.lower()
    assert result.clip is None


def test_a_whisper_is_refused():
    result = check_take(take(speech_at=1.0, level=0.004))

    assert result.ok is False
    assert result.clip is None


def test_a_clipping_take_is_refused():
    """A sample that hit the converter's ceiling teaches the model that the
    wake word is distorted."""
    loud = take(speech_at=1.0, level=0.9)
    loud[int(1.2 * SAMPLE_RATE):int(1.3 * SAMPLE_RATE)] = 1.0

    result = check_take(loud)

    assert result.ok is False
    assert "clip" in result.reason.lower() or "loud" in result.reason.lower()


def test_speech_running_to_the_end_of_the_tape_is_refused():
    """The same fault the calibration ruler was just fixed for. Half a word in
    the training set is worse than in a measurement — it is learned."""
    result = check_take(take(speech_at=RECORD_SECONDS - 0.3, speech_len=0.7))

    assert result.ok is False
    assert "late" in result.reason.lower() or "cut" in result.reason.lower()


def test_a_long_ramble_is_refused():
    """Two seconds is the whole clip. Anything longer is not the wake word,
    and centring it would cut the ends off whatever was said."""
    result = check_take(take(speech_at=0.5, speech_len=2.6))

    assert result.ok is False
    assert "long" in result.reason.lower()


# ═══ what a good take produces ═══

def test_a_good_take_is_accepted():
    result = check_take(take(speech_at=1.0, speech_len=0.7))

    assert result.ok is True, result.reason
    assert result.reason == ""
    assert result.clip is not None


def test_the_clip_is_tight_around_the_word():
    """`align_clip_to_end` positions the clip itself, taking the LAST samples
    of whatever it is given. A clip pre-padded to 2.0s therefore stays where
    the padding put it, while every TTS positive is pushed to the window's
    end — a systematic difference between real and synthetic speech, which is
    the one thing this corpus exists to remove."""
    result = check_take(take(speech_at=1.0, speech_len=0.7))

    length_s = len(result.clip) / SAMPLE_RATE
    assert length_s < CLIP_SECONDS, (
        f"clip is {length_s:.2f}s — padded clips land in a different place "
        f"from the TTS ones"
    )
    assert 0.7 <= length_s <= 1.2, f"{length_s:.2f}s is not tight around a 0.7s word"


def test_the_clip_holds_the_whole_word_wherever_it_was_spoken():
    """The margin exists so a slightly early or late voiced frame is not
    shaved off the word."""
    for offset in (0.6, 1.0, 1.6):
        result = check_take(take(speech_at=offset, speech_len=0.7))
        assert result.ok is True, f"{offset}: {result.reason}"

        bounds = speech_bounds(result.clip)
        assert bounds is not None
        held = (bounds[1] - bounds[0]) / SAMPLE_RATE
        assert held >= 0.6, f"only {held:.2f}s of the 0.7s word survived"


def test_the_clip_never_clips():
    result = check_take(take(speech_at=1.0, level=0.5))

    assert float(np.max(np.abs(result.clip))) <= 1.0


# ═══ picking up where a session left off ═══

def test_the_first_take_is_zero(tmp_path):
    assert next_index(tmp_path) == 0


def test_recording_resumes_after_the_last_take(tmp_path):
    """A hundred takes is more than one sitting. Restarting must not overwrite
    what was already recorded."""
    for name in ("take_000.wav", "take_001.wav", "take_007.wav"):
        (tmp_path / name).touch()

    assert next_index(tmp_path) == 8


def test_unrelated_files_do_not_shift_the_count(tmp_path):
    (tmp_path / "notes.txt").touch()
    (tmp_path / "take_003.wav").touch()

    assert next_index(tmp_path) == 4


# ═══ one utterance, not everything between the first and last sound ═══
#
# Measured through the real microphone, 4s of an ordinary room:
#
#     frame rms p20 0.00521 → threshold 0.02085, 45/201 frames above
#     speech_bounds: (0.36, 3.5)
#     check_take -> ok=False  reason='too long (3.1s)'
#
# Nothing was wrong with the take. `speech_bounds` returned the first and last
# voiced frame, so a chair creak at 0.4s and a word at 3.0s read as one
# 3.1-second utterance. In a real room that is the common case, and it refused
# every take on the first session — zero of a hundred landed.

def take_with_stray(word_at: float, stray_at: float) -> np.ndarray:
    """A tape with the wake word, plus one unrelated sound elsewhere."""
    audio = take(speech_at=word_at, speech_len=0.7)
    start = int(stray_at * SAMPLE_RATE)
    click = int(0.06 * SAMPLE_RATE)
    rng = np.random.default_rng(3)
    audio[start : start + click] += rng.normal(0, 0.12, click).astype(np.float32)
    return audio


def test_a_sound_before_the_word_is_not_part_of_it():
    bounds = speech_bounds(take_with_stray(word_at=2.0, stray_at=0.4))

    assert bounds is not None
    start_s = bounds[0] / SAMPLE_RATE
    assert start_s > 1.5, (
        f"bounds start at {start_s:.2f}s — the stray sound at 0.4s was swallowed "
        f"into the utterance"
    )


def test_a_sound_after_the_word_is_not_part_of_it():
    bounds = speech_bounds(take_with_stray(word_at=0.8, stray_at=3.2))

    assert bounds is not None
    end_s = bounds[1] / SAMPLE_RATE
    assert end_s < 2.5, f"bounds end at {end_s:.2f}s — the stray sound was included"


def test_a_take_with_room_noise_is_still_accepted():
    """The whole failure, as one assertion: this is what a real room does."""
    result = check_take(take_with_stray(word_at=1.6, stray_at=0.3))

    assert result.ok is True, result.reason


def test_a_short_pause_inside_the_word_does_not_split_it():
    """'kav-ACH' has a stop in the middle. Splitting on it would keep only
    half the word."""
    audio = take(speech_at=1.0, speech_len=0.4)
    second = take(speech_at=1.5, speech_len=0.4)
    audio = audio + second

    bounds = speech_bounds(audio)

    assert bounds is not None
    span = (bounds[1] - bounds[0]) / SAMPLE_RATE
    assert span > 0.7, f"span {span:.2f}s — the two halves were treated separately"
