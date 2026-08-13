"""Deterministic handlers for genuinely simple intents (spec §5).

§5 lists "check time" among the simple intents, and the obvious reading is
"send it to the small model". Measured, that turns out to be wrong twice over:
asked the time, qwen3:4b spent 3.4 seconds explaining that it has no access to
the system clock. It is both slower than reading the clock and unable to
answer.

So the truly deterministic intents are answered here — no model, no network,
sub-millisecond — and the local model handles only the simple-but-open-ended
ones. This is the cheapest tier of the routing hierarchy:

    deterministic handler  →  local model  →  Claude Agent SDK

Replies are kept short on purpose: they are spoken, and Kokoro's synthesis
time scales with length (a rambling reply cost 5 seconds of TTS).
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from typing import Callable

log = logging.getLogger("kavach.reasoning.handlers")


def _clock(utterance: str) -> str:
    now = datetime.now()
    text = (utterance or "").lower()

    if "date" in text or "day" in text:
        # "%-d" (no zero padding) reads better aloud than "%d".
        return now.strftime("It's %A, %B %-d.")
    # "a.m." already ends in a period — don't add a second one.
    meridiem = "a.m." if now.strftime("%p") == "AM" else "p.m."
    return now.strftime(f"It's %-I:%M {meridiem}")


def _battery(utterance: str) -> str:
    try:
        output = subprocess.run(
            ["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3
        ).stdout
        for token in output.split():
            if token.rstrip(";").endswith("%"):
                return f"Battery is at {token.rstrip(';')}."
    except Exception:
        log.debug("battery lookup failed", exc_info=True)
    return "I couldn't read the battery level."


#: intent label (from the router's simple-intent match) → handler
_HANDLERS: dict[str, Callable[[str], str]] = {
    "clock": _clock,
    "system query": _battery,
}


def has_handler(intent: str) -> bool:
    return intent in _HANDLERS


def handle(intent: str, utterance: str) -> str | None:
    """Answer deterministically, or None if this intent needs a model.

    Never raises: a handler that throws would take down a voice turn, and the
    fallback (ask a model) is always available.
    """
    handler = _HANDLERS.get(intent)
    if handler is None:
        return None
    try:
        return handler(utterance)
    except Exception:
        log.exception("handler for %r failed", intent)
        return None
