"""Web search tests.

Search is the second thing in KAVACH that leaves the machine — the first being
deliberate Claude calls. Spec §2 promises local-first, so the boundary has to
be visible: every query is logged, and nothing reaches the network unless the
request clearly asked for something KAVACH cannot know on its own.

The intent layer is therefore conservative in a specific direction: it would
rather miss a search than send "delete the draft in Notes" to a third party.
"""

import pytest

from kavach.reasoning.search import (
    needs_search,
    shorten_for_speech,
)


# ═══ what should reach the network ═══

@pytest.mark.parametrize("said", [
    "what's the weather today",
    "what is the weather going to be like this weekend",
    "what's in the news",
    "search for the train times to Abu Dhabi",
    "look up the population of Dubai",
    "who won the match last night",
    "what's the exchange rate for the dirham",
])
def test_questions_about_the_world_trigger_a_search(said):
    assert needs_search(said), said


# ═══ what must not ═══

@pytest.mark.parametrize("said", [
    "what time is it",
    "delete the draft in Notes",
    "open Safari",
    "pause the music",
    "what's on my screen",
    "what's playing",
    "remind me to call mum",
])
def test_local_requests_never_leave_the_machine(said):
    """The cost of a false positive here is not a wasted call — it is a
    private instruction sent to a third party."""
    assert not needs_search(said), said


@pytest.mark.parametrize("said", ["", "   ", None])
def test_empty_input_does_not_search(said):
    assert not needs_search(said)


def test_a_personal_question_is_not_a_web_search():
    """'What's on my calendar' is about the user's own data, which is on the
    machine and must not become a search query."""
    assert not needs_search("what's on my calendar tomorrow")
    assert not needs_search("what did I say about the project yesterday")


# ═══ speaking the answer ═══

def test_a_long_answer_is_cut_to_something_speakable():
    long = (
        "Dubai is currently experiencing sunny conditions with a temperature "
        "of 36 degrees Celsius. Humidity sits at around 56 percent. Winds are "
        "light from the west-northwest. Tomorrow is expected to be similar, "
        "with highs of 38 degrees and no rain forecast for the coming week."
    )
    out = shorten_for_speech(long)
    assert len(out) < len(long)
    assert out.endswith((".", "!", "?"))


def test_a_short_answer_is_left_alone():
    short = "It is 36 degrees and sunny in Dubai."
    assert shorten_for_speech(short) == short


def test_markdown_is_stripped_because_it_is_read_aloud():
    assert "*" not in shorten_for_speech("It is **36 degrees** and sunny.")
    assert "[" not in shorten_for_speech("See [the forecast](http://x.com) today.")


@pytest.mark.parametrize("answer", ["", None])
def test_an_empty_answer_says_so_rather_than_nothing(answer):
    """Silence after a question reads as a crash. A short honest line does
    not."""
    out = shorten_for_speech(answer)
    assert out and len(out) < 100
