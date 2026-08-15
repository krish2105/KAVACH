"""An API turn is something you did, and must be remembered too.

The memory write lives at loop.py:745, inside the voice-turn path.
`respond()` starts at line 762, so a command from the phone or the API never
reached it — the turn happened, was logged, and left no memory.

That gap was found by the plan's own verification failing: it sent an API
command and checked the store, and the store stayed at 0. The wiring was
correct; the check was exercising a path that does not call it.

A command typed from your phone is as much a thing you asked for as one
spoken into the room, and recall that only knows half your history answers
"I don't have that" to things you definitely did.
"""

import pytest


class FakeMemory:
    def __init__(self):
        self.stored = []

    def remember(self, text, collection="turns", source=""):
        self.stored.append((text, collection, source))
        return len(self.stored)

    def search(self, query, limit=5, collection=None):
        return []


def test_the_helper_stores_a_turn():
    from kavach.voice.loop import remember_turn

    memory = FakeMemory()
    remember_turn(memory, "open Notes", "Opened Notes.", origin="api")

    assert len(memory.stored) == 1
    text, collection, source = memory.stored[0]
    assert "open Notes" in text and "Opened Notes" in text
    assert collection == "turns"
    assert "api" in source


def test_the_origin_is_recorded():
    """"You asked this from your phone" and "you said this out loud" are
    different facts, and provenance is the whole promise of recall."""
    from kavach.voice.loop import remember_turn

    memory = FakeMemory()
    remember_turn(memory, "q", "a", origin="voice")

    assert "voice" in memory.stored[0][2]


def test_no_memory_is_not_an_error():
    """Ollama may be down. A missing store degrades to no recall, never to a
    broken turn."""
    from kavach.voice.loop import remember_turn

    remember_turn(None, "q", "a", origin="api")   # must not raise


def test_a_failing_store_does_not_break_the_turn():
    """The turn already succeeded. Failing to remember it must not undo that."""
    from kavach.voice.loop import remember_turn

    class Broken:
        def remember(self, *a, **k):
            raise RuntimeError("ollama is not running")

    remember_turn(Broken(), "q", "a", origin="api")   # must not raise
