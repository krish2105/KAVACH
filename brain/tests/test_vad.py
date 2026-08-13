"""Voice-activity detection tests.

An energy threshold was not enough. The room measures rms 0.0365, the gate sat
at 0.006, so ordinary room noise reached Whisper — which does not return empty
for non-speech, it confabulates. Real turns logged from an empty room:

    'The problem was that the case was not coming.'
    "Will you other person I hope or don't like..."
    'Legend and legend do it.'

Harmless while KAVACH only echoes. From Phase 4 those strings reach a router
that can act, so "did a human actually speak" has to be answered before
transcription, not after.
"""

import numpy as np
import pytest

from kavach.voice.vad import (
    FRAME_MS,
    SpeechGate,
    frames_of,
    has_speech,
)

SR = 16_000


def tone(seconds: float, freq: float = 180.0, amp: float = 0.3) -> np.ndarray:
    """A periodic waveform. Loud and tonal, but not speech."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def noise(seconds: float, amp: float = 0.04) -> np.ndarray:
    """Broadband room noise at roughly the level measured in the real room."""
    rng = np.random.default_rng(11)
    return (rng.normal(0, amp, int(SR * seconds))).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.float32)


# ═══ framing ═══

def test_frames_are_the_size_webrtcvad_demands():
    """webrtcvad accepts only 10, 20 or 30 ms frames; anything else raises."""
    got = list(frames_of(silence(1.0), SR))
    expected = int(SR * FRAME_MS / 1000)
    assert all(len(f) == expected for f in got)
    assert len(got) == int(1000 / FRAME_MS)


def test_a_partial_trailing_frame_is_dropped_not_padded():
    """Padding with zeros would invent non-speech at the end of every clip."""
    audio = silence(0.105)  # 105 ms → 5 whole 20 ms frames, 5 ms left over
    assert len(list(frames_of(audio, SR))) == 5


# ═══ what must never pass ═══

def test_silence_is_rejected():
    assert not has_speech(silence(2.0), SR)


def test_room_noise_at_the_measured_level_is_rejected():
    """The exact failure that produced 'Legend and legend do it.'"""
    assert not has_speech(noise(2.0, amp=0.04), SR)


def test_loud_noise_is_still_rejected():
    """Loudness is not speech. An energy gate cannot tell these apart."""
    assert not has_speech(noise(2.0, amp=0.15), SR)


def test_a_pure_tone_is_rejected():
    assert not has_speech(tone(2.0), SR)


def test_an_empty_clip_is_rejected():
    assert not has_speech(np.zeros(0, dtype=np.float32), SR)


# ═══ the gate's own logic, independent of webrtcvad ═══

def test_a_single_voiced_frame_is_not_enough():
    """One frame is 20 ms. A door closing can look voiced for 20 ms; the
    shortest real word cannot."""
    gate = SpeechGate(min_voiced_frames=8)
    assert not gate.verdict([True] + [False] * 40)


def test_scattered_voiced_frames_do_not_count():
    """Speech is contiguous. Noise that trips the detector does so at random,
    so require a run rather than a total."""
    gate = SpeechGate(min_voiced_frames=8)
    assert not gate.verdict([True, False] * 30)


def test_a_solid_run_of_voiced_frames_passes():
    gate = SpeechGate(min_voiced_frames=8)
    assert gate.verdict([False] * 5 + [True] * 12 + [False] * 5)


def test_the_run_may_be_split_by_a_brief_gap():
    """Stop consonants produce short unvoiced gaps mid-word. Breaking the run
    on a single frame would reject ordinary speech."""
    gate = SpeechGate(min_voiced_frames=8, max_gap_frames=2)
    assert gate.verdict([True] * 5 + [False, False] + [True] * 5)


def test_a_long_gap_does_break_the_run():
    gate = SpeechGate(min_voiced_frames=8, max_gap_frames=2)
    assert not gate.verdict([True] * 5 + [False] * 10 + [True] * 5)


# ═══ strictness is a deliberate choice ═══

def test_strict_mode_needs_more_evidence_than_lenient():
    frames = [False] * 3 + [True] * 6 + [False] * 3
    assert SpeechGate(min_voiced_frames=4).verdict(frames)
    assert not SpeechGate(min_voiced_frames=12).verdict(frames)


@pytest.mark.parametrize("aggressiveness", [0, 1, 2, 3])
def test_every_aggressiveness_still_rejects_the_real_room(aggressiveness):
    """Whatever the tuning, measured room noise must not read as speech."""
    assert not has_speech(noise(2.0, amp=0.04), SR, aggressiveness=aggressiveness)
