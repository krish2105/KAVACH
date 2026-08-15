"""A file request must never reach a model with no filesystem.

Measured live 2026-08-15:

    "find the KAVACH master prompt file on my desktop and tell me the
     first line"
    → route=local
    → "Maine aisi koi file nahi dhundh saka."   ("I couldn't find any such file")

It never searched. The local 3B model has no tools, and asked about a file it
does not decline — it **narrates** having looked. This is the identical failure
that `_ACTIONABLE_INTENTS` was created for when `open Notes` was answered with
"Notes are now open" and Notes was not open.

It was masked because the first live test used an explicit path
(`/tmp/kavach_read_test.txt`), which made the utterance look complex enough to
escalate on other grounds. The capability worked; the routing to it did not.

`file` is a hard word to pattern-match — "file a complaint", "profile",
"filed away" — so the patterns are anchored on file *operations*, and the
near-misses below are as much the point as the matches.
"""

import pytest

from kavach.reasoning.router import Route, Router


@pytest.mark.parametrize("said", [
    "find the KAVACH master prompt file on my desktop",
    "find my tax document",
    "search my documents for the invoice",
    "read the file called notes",
    "open the file budget.xlsx and read it",
    "what files are on my desktop",
    "list the files in my downloads folder",
    "show me the contents of that text file",
    "delete the file called draft",
    "save this to a file on my desktop",
    "how many files are in my documents folder",
])
def test_a_file_request_reaches_the_tool_route(said):
    """Anything that needs the disk needs tools. A model with no hands must
    never answer it — being wrong here means a confident lie about what was
    searched."""
    decision = Router(local_client=None).route(said)
    assert decision.route is Route.CLAUDE, (
        f"{said!r} went to {decision.route} — a model with no filesystem"
    )


@pytest.mark.parametrize("said", [
    "file a complaint about the service",
    "what is my profile picture",
    "tell me about the Rockefeller files",
    "I filed my taxes last year",
    "what time is it",
    "open Notes",
])
def test_ordinary_speech_is_not_dragged_onto_the_tool_route(said):
    """Over-matching costs a model call and a slow turn for every sentence
    containing "file". The word is common and mostly not about the disk."""
    decision = Router(local_client=None).route(said)
    assert decision.route is not Route.CLAUDE or decision.intent != "file access", (
        f"{said!r} was treated as a file request"
    )


def test_the_intent_is_named_so_the_hud_can_show_it():
    """§13 — the reason shown must be the one that acted."""
    decision = Router(local_client=None).route("find my tax document")
    assert decision.intent == "file access"


# ═══ a path in the utterance is the strongest signal there is ═══

@pytest.mark.parametrize("said", [
    "write the text hello kavach to the file /tmp/notes.txt",
    "read /Users/me/Documents/report.md",
    "what is in ~/Downloads",
    "delete /tmp/scratch.txt",
    "put this in ~/Desktop/todo.txt",
])
def test_an_utterance_containing_a_path_reaches_the_tool_route(said):
    """Measured live 2026-08-15, after the first fix:

        "write the text hello kavach to the file /tmp/kavach_propose_test.txt"
        → route=local → "kya kiye? nahin ho sakte."

    The pattern allowed 24 characters between the verb and the word "file";
    that sentence has 29. Tuning the number would fix this sentence and miss
    the next one.

    A path is not a hint. Nothing that is not a file operation says
    `/tmp/notes.txt`, and it survives any phrasing wrapped around it — which
    is what the word-distance patterns cannot do.
    """
    decision = Router(local_client=None).route(said)
    assert decision.route is Route.CLAUDE, f"{said!r} went to {decision.route}"


@pytest.mark.parametrize("said", [
    "what is the time",
    "and/or something",
    "the ratio is 3/4",
    "open Notes",
])
def test_ordinary_speech_with_a_slash_is_not_a_path(said):
    """`3/4` and `and/or` are not filesystem paths. A path starts at the root
    or at home."""
    decision = Router(local_client=None).route(said)
    assert decision.intent != "file access", said
