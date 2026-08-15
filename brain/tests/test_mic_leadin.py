"""Silence before you speak is not the same as silence after you finish.

Measured live 2026-08-15, twice in a row, with the speaker gate freshly
verified and everything downstream working:

    21:48:57  overlay:  chord accepted → talk requested
    21:49:04  daemon:   no speech in the clip (5/61 voiced frames,
                        rms 0.0071, 1.2s), discarding turn

The hotkey fired, the daemon opened the mic, and the turn closed **before the
user started speaking**. Nothing reached the router, so the action log was
empty and it looked as though the gate had failed. It had not; the recording
contained no speech to gate.

`record_utterance` ended a turn after `silence_ms` of quiet once
`min_utterance_ms` had passed — 700ms and 350ms, so about 1.05s. It could not
tell *"has not started yet"* from *"has finished"*, and those want opposite
answers: be patient for the first, prompt for the second.

CLAUDE.md recorded the symptom back in August — "clicking TALK and *then*
gathering your thoughts closes it before you speak" — as a fact about how to
use it. It is a fact about the endpointer.
"""

import numpy as np
import pytest

from kavach.voice.mic import EndpointConfig, Recorder

RATE = 16_000
BLOCK = int(RATE * 0.032)


def quiet(blocks=1):
    return [np.full(BLOCK, 0.001, dtype="float32") for _ in range(blocks)]


def loud(blocks=1):
    return [np.full(BLOCK, 0.09, dtype="float32") for _ in range(blocks)]


class FakeMic:
    """Replays a fixed sequence of blocks, then silence forever."""

    def __init__(self, blocks):
        self.blocks = list(blocks)
        self.reads = 0

    def preroll(self, ms):
        return np.zeros(0, dtype="float32")

    def read(self, timeout=1.0):
        self.reads += 1
        if self.blocks:
            return self.blocks.pop(0)
        return np.full(BLOCK, 0.001, dtype="float32")


def seconds(audio):
    return len(audio) / RATE


# ═══ the bug ═══

def test_it_waits_for_you_to_start_speaking():
    """Two seconds of thinking, then a sentence. The old endpointer returned
    at ~1.05s with nothing in it."""
    mic = FakeMic(quiet(62) + loud(30) + quiet(40))
    audio = Recorder(mic, EndpointConfig()).record_utterance(preroll_ms=0)

    loud_frames = int((np.abs(audio) > 0.05).sum())
    assert loud_frames > 0, "the turn closed before the speech arrived"
    assert seconds(audio) > 2.0


def test_it_gives_up_eventually_on_an_empty_room():
    """Patience is not forever. A key pressed by accident must not hold the
    microphone open indefinitely — §7 cares about how long it listens."""
    mic = FakeMic(quiet(400))
    config = EndpointConfig()
    audio = Recorder(mic, config).record_utterance(preroll_ms=0)

    assert seconds(audio) <= config.lead_in_ms / 1000 + 1.0


# ═══ what must not change ═══

def test_a_pause_after_speech_still_ends_the_turn_promptly():
    """The whole point of endpointing. Once you have spoken, 700ms of quiet
    means you are done — waiting longer makes every turn feel slow."""
    mic = FakeMic(loud(30) + quiet(60))
    config = EndpointConfig()
    audio = Recorder(mic, config).record_utterance(preroll_ms=0)

    assert seconds(audio) < 2.5, f"{seconds(audio):.2f}s — too slow to end"


def test_a_mid_sentence_pause_does_not_cut_you_off():
    mic = FakeMic(loud(20) + quiet(15) + loud(20) + quiet(60))
    audio = Recorder(mic, EndpointConfig()).record_utterance(preroll_ms=0)

    assert seconds(audio) > 1.6, "it cut at the comma"


def test_the_maximum_is_still_enforced():
    """A held key or a talkative room must not record forever."""
    config = EndpointConfig(max_utterance_ms=1500)
    mic = FakeMic(loud(200))
    audio = Recorder(mic, config).record_utterance(preroll_ms=0)

    assert seconds(audio) <= 2.0


def test_the_lead_in_is_longer_than_the_trailing_silence():
    """They answer different questions, so they must not be one number."""
    config = EndpointConfig()
    assert config.lead_in_ms > config.silence_ms * 2


# ═══ one implementation, not two ═══

def test_the_endpointing_logic_exists_in_exactly_one_place():
    """The sixth instance of this codebase's recurring defect, and the one
    that wasted the most time: `loop._record_with_meter` carried a hand-copied
    duplicate of `Recorder`'s endpointing, and the copy is what actually runs.

    Fixing `mic.py` turned the tests green and changed nothing live. The user
    pressed the key, spoke, and got "no speech in the clip" again — with a
    freshly verified speaker gate that was working the whole time.

    So this greps for the giveaway: a second place deciding when a turn ends.
    """
    import ast
    import inspect

    from kavach.voice import loop as loop_mod

    source = inspect.getsource(loop_mod)
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {
            "silence_ms", "lead_in_ms", "silence_rms",
        }:
            raise AssertionError(
                f"loop.py reads cfg.{node.attr} directly — endpointing "
                f"belongs to TurnEndpointer, and a second copy of it is how "
                f"the live behaviour and the tests stopped agreeing."
            )
