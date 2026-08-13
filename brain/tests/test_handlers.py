"""Deterministic intent handlers (spec §5's "simple intent" path).

Discovered live: asked "what time is it", qwen3:4b replied "We are in a
simulated environment... I don't have real-time access to the system clock",
after 3.4 seconds. A language model is the wrong tool for reading a clock —
it is slower AND it cannot actually answer.

These handlers run before any model. Sub-millisecond and correct.
"""

import re

import pytest

from kavach.reasoning.handlers import handle, has_handler


def test_time_is_answered_from_the_system_clock():
    reply = handle("clock", "what time is it")
    assert reply
    # A real spoken time, not an apology about lacking access.
    assert re.search(r"\d{1,2}:\d{2}", reply) or re.search(r"\d{1,2}\s*(am|pm)", reply, re.I)
    assert "don't have" not in reply.lower()
    assert "cannot" not in reply.lower()


def test_date_is_answered_from_the_system_clock():
    reply = handle("clock", "what's the date today")
    assert reply
    assert any(month in reply for month in
               ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"])


def test_handler_replies_are_short_enough_to_speak():
    """Kokoro synthesis time scales with text length; a rambling reply cost
    5 seconds of TTS in the measured run."""
    for intent, utterance in [("clock", "what time is it"),
                              ("clock", "what is the date")]:
        reply = handle(intent, utterance)
        assert len(reply) < 120, reply


def test_unknown_intent_has_no_handler():
    assert not has_handler("comprehension")
    assert handle("comprehension", "summarise this") is None


def test_known_intents_are_advertised():
    assert has_handler("clock")


def test_handlers_never_raise_on_junk():
    """A handler that throws would take down a voice turn."""
    for intent in ["clock", "system query", "", "nonsense"]:
        handle(intent, "")  # must not raise
