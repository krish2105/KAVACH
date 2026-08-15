"""A question about the past must not become part of the past.

Found live. Asked the battery level, then asked what I had just asked:

    "what is the battery level"          → "Battery is at 37%."
    "what did I just ask you about the battery"
                                         → "I don't have anything about that."

The write was fine — 11 turns, all present. The *field* was the problem.
Every recall question had itself been stored as a turn, so the index held:

    0.0653  "User said: what did I just ask you about the battery / …"
    0.0637  "User said: what did I ask you about the battery / …"
    0.0579  "User said: what is the battery level / Battery is at 41%."

A recall question is worded almost exactly like the turn it is looking for,
so it scores near the top and *flattens the lead* the answer needs. Two of
those questions were enough to push the real answer under the threshold.

`recall.py` requires a match to beat the field by a measured margin rather
than to clear an absolute score, which is what makes this the failure mode:
adding near-duplicates does not raise the winner, it raises everything.

**So recall turns are not remembered.** The content of one is either a fact
already stored (the answer came from a turn that is in the index) or the
absence of one ("I don't have anything about that") — a memory recording
that a memory was missing. Neither is worth a row, and both crowd the field
for every later question on the topic.
"""

import pytest

from kavach.voice.loop import remember_turn


class FakeStore:
    def __init__(self):
        self.stored = []

    def remember(self, text, collection="turns", source=""):
        self.stored.append((text, collection, source))
        return len(self.stored)


def test_an_ordinary_turn_is_remembered():
    store = FakeStore()

    remember_turn(store, "what is the battery level", "Battery is at 37%.",
                  origin="api")

    assert len(store.stored) == 1
    assert "battery" in store.stored[0][0].lower()


def test_a_recall_turn_is_not_remembered():
    """The measurement above, as a test."""
    store = FakeStore()

    remember_turn(store, "what did I just ask you about the battery",
                  "You asked about the battery level.",
                  origin="api", route="recall")

    assert store.stored == [], "a question about memory became a memory"


def test_a_recall_refusal_is_not_remembered():
    """"I don't have anything about that" is the worst row of all — it says
    nothing, and it looks like every question on the subject."""
    store = FakeStore()

    remember_turn(store, "what did I do last March",
                  "I don't have anything about that.",
                  origin="voice", route="recall")

    assert store.stored == []


def test_route_is_optional():
    """Callers that predate this must keep working, and must keep storing —
    defaulting to "skip" would silently stop remembering everything."""
    store = FakeStore()

    remember_turn(store, "open Notes", "Opened Notes.", origin="voice")

    assert len(store.stored) == 1


def test_no_memory_still_does_not_raise():
    remember_turn(None, "said", "replied", origin="voice", route="recall")


def test_both_callers_actually_pass_the_route():
    """`route` defaults to "" so old callers keep storing — which means a
    caller that forgets to pass it stores recall turns and nothing complains.
    That is the built-but-unwired shape this project has now hit nine times,
    so it is asserted rather than assumed.

    Parsed with `ast`, not split on `")"`. The first version did the latter
    and failed against correctly wired code: `getattr(loop, "memory", None)`
    carries an inner paren, so the slice ended before it reached the keyword.
    Reading source with string surgery is how a grep test reports a defect
    that is really a bug in the grep.
    """
    import ast
    import inspect

    from kavach.api import app as api_module
    from kavach.voice import loop as loop_module

    for module in (loop_module, api_module):
        calls = [
            node for node in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "remember_turn"
        ]
        assert calls, f"{module.__name__} no longer remembers turns at all"
        for call in calls:
            assert any(kw.arg == "route" for kw in call.keywords), (
                f"{module.__name__}:{call.lineno} calls remember_turn without "
                f"a route, so recall turns are stored and pollute the index"
            )


@pytest.mark.parametrize("route", ["local", "claude", "action", "clock"])
def test_every_other_route_is_remembered(route):
    """Only recall is skipped. A blocklist that grows quietly ends with an
    assistant that remembers nothing."""
    store = FakeStore()

    remember_turn(store, "do the thing", "Did the thing.", origin="voice",
                  route=route)

    assert len(store.stored) == 1, route
