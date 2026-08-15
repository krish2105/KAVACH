"""Searching the web needs a browser, so it must never reach a model without one.

Measured live 2026-08-16, spoken into the microphone:

    said   "search wwe on youtube"
    route  local · "local model classified as simple (heuristics were unsure)"
    said   "I'm unable to access external links or websites."

The turn before it worked — *"open Chrome and search YouTube"* matched
`app control`, reached the tool route, and `mcp__kavach-browser__navigate_to`
opened the page. A **bare** search names no app, so it matched nothing, the
heuristics shrugged, and the 3B local model answered a question about the
web from a model with no web.

This is the fourth instance of one failure: `_SIMPLE_PATTERNS` treating
*simple to understand* as *simple to answer*. It cost "open Notes" (claimed
success, opened nothing), "find the KAVACH master prompt" (claimed to have
looked), the recall questions (would have invented a Friday), and now this.

**This one is the mildest and the most instructive**, because the model
refused honestly instead of lying. `hands/browser.py` was reachable and
working the whole time; nothing routed to it.

Anchored on a **named destination or an explicit web verb**, never on the
bare word "search" — "search my notes" is recall and "find my tax document"
is the filesystem, and both would be broken by a greedy pattern. The
near-misses below are the specification.
"""

import pytest

from kavach.reasoning.router import Route, Router


def route(said):
    return Router(local_client=None).route(said)


@pytest.mark.parametrize("said", [
    "search wwe on youtube",                 # the exact live failure
    "search for wrestling on youtube",
    "google the weather in Delhi",
    "look up the offside rule",
    "search the web for tailscale pricing",
    "search the internet for a good curry recipe",
    "play despacito on youtube",
    "youtube the highlights from last night",
])
def test_a_web_search_reaches_the_tool_route(said):
    decision = route(said)
    assert decision.route is Route.CLAUDE, (
        f"{said!r} → {decision.route} · {decision.reason!r}; a model with no "
        f"browser cannot answer this and will say so, or worse invent it"
    )


def test_the_intent_says_what_it_is():
    """`reason` and `intent` reach the HUD and the log (§13). "app control"
    for a YouTube search would send the next reader to the wrong module."""
    assert route("search wwe on youtube").intent == "web search"


# ═══ what must not be swallowed ═══

@pytest.mark.parametrize("said,expected", [
    ("what did I say about the router", "recall"),
    ("what did I ask you to do this morning", "recall"),
    ("search my notes for the roof quote", "recall"),
    ("find my tax document from last year", "file access"),
    ("look for the file called budget", "file access"),
    ("what time is it", "clock"),
    ("open Notes", "app control"),
])
def test_the_neighbours_are_untouched(said, expected):
    """Every one of these contains a word the web pattern could grab.
    "search", "look up" and "find" belong to three different subsystems, and
    the only thing separating them is what comes after."""
    assert route(said).intent == expected, said


def test_an_ordinary_question_is_still_local():
    """Not everything with a question mark needs a browser. Sending general
    knowledge to the agent costs a 27-second turn for something the local
    model answers in one."""
    decision = route("what is a transformer")
    assert decision.intent != "web search", decision.reason
