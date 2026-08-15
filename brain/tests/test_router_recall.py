"""A question about the past must not reach a model with no memory.

The same rule as the clock, and for the same reason. A model with no index
does not decline — it invents a plausible Friday. KAVACH once answered
"twenty past four" at 8pm because a transposed "what time it is" missed a
regex and fell through to a model with no clock; a question about yesterday
is the identical failure in different clothes.

Anchored on a **past-tense verb plus a first- or second-person subject**, so
"what is the weather tomorrow" cannot match. Placed above the clock patterns
so "what did I do at 5" is recall rather than a time query.
"""

import pytest

from kavach.reasoning.router import Route, Router


def route(said):
    return Router(local_client=None).route(said)


@pytest.mark.parametrize("said", [
    "what did I do yesterday",
    "what did I ask you to do this morning",
    "when did I last open Chrome",
    "what did you say about the router",
    "what have we talked about today",
    "have I already asked you that",
    "what did I do at 5",
])
def test_a_question_about_the_past_is_routed_to_recall(said):
    assert route(said).intent == "recall", f"{said!r} → {route(said).intent!r}"


def test_recall_reaches_the_tool_route_not_the_local_model():
    """`_ACTIONABLE_INTENTS` exists because a 3B model with no hands narrates
    having done things. One with no index narrates having remembered them."""
    assert route("what did I do yesterday").route is Route.CLAUDE


@pytest.mark.parametrize("said", [
    "what time is it",
    "what is the weather tomorrow",
    "open Notes",
    "what is a transformer",
    "what did the report say",          # third person — not KAVACH's history
])
def test_ordinary_questions_are_not_recall(said):
    """Over-matching sends every question through an index lookup that will
    find nothing, and answers "I don't have that" to "what is a transformer"."""
    assert route(said).intent != "recall", said


def test_the_clock_still_wins_for_actual_clock_questions():
    """Recall patterns sit above the clock ones, so this asserts they did not
    swallow it — the clock must never reach a language model."""
    assert route("what time is it").intent == "clock"
