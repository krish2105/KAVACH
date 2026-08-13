"""The local model narrates its reasoning. These strip it back to a sentence.

Observed live, spoken aloud and printed in the HUD transcript:

    "We are in a scenario where I am KAVACH, a voice assistant on a Mac. I must
     reply in ONE short spoken sentence... The user asks: "What is the weather
     today?" As an AI, I don't have real-time weather data access. However, in
     the context of this exercise... But note: The problem says... So,"

`think: false` does not prevent this: Qwen3 emits reasoning as ordinary prose,
not inside <think> tags, so there is nothing structural to remove. The prompt
is the first line of defence and this is the second — a voice assistant that
reads its own deliberation aloud is unusable regardless of whose fault it is.
"""

import pytest

from kavach.reasoning.cleanup import clean_reply, looks_like_reasoning


REASONING = (
    'We are in a scenario where I am KAVACH, a voice assistant on a Mac. '
    'I must reply in ONE short spoken sentence, no markdown, no preamble.\n\n'
    'The user asks: "What is the weather today?"\n\n'
    "As an AI, I don't have real-time weather data access. However, in the "
    "context of this exercise, I should be honest.\n\n"
    "So, my reply is: I can't check the weather — I have no internet access."
)


# ═══ detection ═══

def test_narrated_reasoning_is_detected():
    assert looks_like_reasoning(REASONING)


@pytest.mark.parametrize("reply", [
    "It's 6:07 a.m.",
    "Three events tomorrow, the first at nine.",
    "I can't check the weather without a search tool.",
    "Safari is open on the front window.",
])
def test_a_normal_reply_is_not_flagged(reply):
    """False positives here truncate good answers, which is worse than the
    problem being solved."""
    assert not looks_like_reasoning(reply)


# ═══ extraction ═══

def test_the_actual_answer_is_recovered():
    out = clean_reply(REASONING)
    assert "scenario" not in out
    assert "As an AI" not in out
    assert "weather" in out.lower()


def test_a_clean_reply_passes_through_untouched():
    assert clean_reply("It's 6:07 a.m.") == "It's 6:07 a.m."


def test_output_is_one_sentence():
    out = clean_reply(REASONING)
    assert out.count(".") <= 2, out


def test_meta_prefixes_are_removed():
    for prefix in ("So, my reply is:", "Answer:", "Response:", "I would say:"):
        assert clean_reply(f"{prefix} It is nine o'clock.").startswith("It is")


def test_think_tags_are_stripped_when_present():
    """Some builds do emit tags. Handle both rather than assuming."""
    assert clean_reply("<think>weighing it up</think>It is nine.") == "It is nine."


# ═══ degenerate input ═══

@pytest.mark.parametrize("reply", ["", "   ", None])
def test_empty_input_yields_empty_output(reply):
    assert clean_reply(reply) == ""


def test_pure_reasoning_with_no_answer_yields_something_speakable():
    """If the model only deliberated and never answered, saying nothing is
    better than reading the deliberation — but silence is confusing, so it
    must return a short honest line instead."""
    out = clean_reply("We are in a scenario where I must decide. The user asks: hello.")
    assert out
    assert "scenario" not in out
    assert len(out) < 120
