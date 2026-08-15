"""A one-word answer must be able to reach a pending confirmation.

Measured live 2026-08-15, with everything else finally working — the lead-in
fix landed, the turn captured "Delete the note called draft.", the router
escalated it, §7 spoke it back and waited:

    22:00:44  awaiting confirmation: "That would delete the note called
              draft. Say confirm if you want me to."
    22:01:50  no speech in the clip (10/61 voiced frames, rms 0.0276, 1.2s),
              discarding turn

The user said "confirm". `rms 0.0276` is well above the silence floor, so it
was captured. `SpeechGate` rejected it afterwards: it wants **8 voiced frames
inside one run** with a 4-frame unbroken core, and "confirm" has an unvoiced
/f/ in the middle that splits it into two short runs. Neither is long enough.

So the gate rejects precisely the utterances the confirmation flow asks for.
The answer could never have got through.

**Relaxing it only while a confirmation is pending is safe, and the reason is
specific.** The strict gate exists because whisper confabulates on room noise
— an empty room produced "Legend and legend do it." — and a confabulated
*command* can act. A confabulated *answer* cannot: `interpret()` returns None
for anything that is not an unambiguous yes or no, and the caller treats None
as a denial. The blast radius of a false positive here is one repeated
question.
"""

import pytest

from kavach.voice.vad import SHORT_ANSWER_GATE, SpeechGate


def frames(pattern: str) -> list[bool]:
    """`"..###.##"` → voiced where `#`."""
    return [c == "#" for c in pattern]


# ═══ the word that could not get through ═══

def test_the_word_confirm_passes_the_short_answer_gate():
    """Roughly what "confirm" looks like: a short voiced run, the unvoiced
    /f/, then another short run.

    The gap is three frames because /f/ runs 60-80ms and a frame is 20ms.
    That matters: `max_gap_frames` is 2, so a shorter gap would let the two
    runs merge into one long enough to pass — which is why the first version
    of this test failed. The fricative is exactly wide enough to split them.
    """
    spoken = frames("...####...#####....")

    assert not SpeechGate().verdict(spoken), (
        "the strict gate is supposed to reject this — that is the bug"
    )
    assert SHORT_ANSWER_GATE.verdict(spoken)


@pytest.mark.parametrize("pattern", [
    "..###...###..",      # con-firm, split by the /f/
    "....####.....",      # yes
    "..##..##.....",      # no
    "...#####.....",      # yeah
])
def test_short_answers_pass(pattern):
    assert SHORT_ANSWER_GATE.verdict(frames(pattern)), pattern


# ═══ but it is still a gate ═══

def test_silence_still_fails():
    assert not SHORT_ANSWER_GATE.verdict(frames("............"))


def test_a_single_click_still_fails():
    """One frame is 20ms. A key press is not an answer."""
    assert not SHORT_ANSWER_GATE.verdict(frames("....#......."))


def test_intermittent_noise_still_fails():
    """Alternating voiced/unvoiced is what noise looks like to webrtcvad. The
    unbroken-core requirement is what rejects it, and relaxing the run length
    must not remove that."""
    assert not SHORT_ANSWER_GATE.verdict(frames("#.#.#.#.#.#.#.#."))


def test_it_is_strictly_more_permissive_than_the_command_gate():
    """A confirmation gate that rejected something the command gate accepts
    would be an inconsistency nobody could reason about."""
    strict, short = SpeechGate(), SHORT_ANSWER_GATE

    assert short.min_voiced_frames <= strict.min_voiced_frames
    assert short.min_consecutive_frames <= strict.min_consecutive_frames


def test_the_command_gate_is_unchanged():
    """The strict gate protects the path where a confabulation can ACT. It
    must not drift looser as a side effect of fixing the answer path."""
    assert SpeechGate().min_voiced_frames == 8
    assert SpeechGate().min_consecutive_frames == 4
