"""Web search, via Tavily.

This is the second thing in KAVACH that leaves the machine. The first is a
deliberate Claude call; spec §2 promises everything else stays local. So the
boundary is drawn narrowly and made visible:

* a request must clearly ask about the world before anything is sent;
* every query is written to the action log, so what left is auditable;
* the query alone is sent — never the transcript, never surrounding context.

The intent test is conservative in one direction on purpose. Missing a search
costs a repeat. A false positive sends a private instruction to a third party,
and "delete the draft in Notes" is not a thing to hand to a search engine.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("kavach.reasoning.search")

ENDPOINT = "https://api.tavily.com/search"
TIMEOUT = 12.0

#: Phrases that mean "something I cannot know from this machine".
_WORLD = (
    "weather", "forecast", "temperature", "raining",
    "news", "headlines", "happening in the world",
    "search for", "search the web", "look up", "google",
    "who won", "score", "match", "election",
    "exchange rate", "stock price", "share price",
    "population of", "capital of", "how far is",
    "what time is it in",
)

#: Anything about the user's own machine or data. Checked first, because
#: several of these contain words that otherwise look like world questions —
#: "what's on my calendar" is not a search for calendars.
_PERSONAL = (
    "my calendar", "my notes", "my screen", "my files", "my email",
    "my inbox", "my reminders", "my photos", "my documents",
    "remind me", "did i say", "i said", "my meeting", "my schedule",
    "on my mac", "my desktop", "playing",
)

#: Local capabilities that must never become a search.
_LOCAL_ACTION = (
    "what time is it", "open ", "close ", "quit ", "delete ", "pause",
    "play ", "skip", "volume", "turn it", "screenshot", "kill switch",
)


def needs_search(said: str | None) -> bool:
    """True if this asks about the world rather than the machine."""
    if not said or not said.strip():
        return False
    text = said.strip().lower()

    if any(phrase in text for phrase in _PERSONAL):
        return False
    if any(text.startswith(p) or p in text for p in _LOCAL_ACTION):
        return False
    return any(phrase in text for phrase in _WORLD)


#: Questions whose answer depends on where you are.
_LOCATION_DEPENDENT = (
    "weather", "forecast", "temperature", "raining", "humidity",
    "sunset", "sunrise", "traffic", "near me", "nearby", "around here",
)


def local_place() -> str | None:
    """Where to assume the user is, for questions that need it.

    KAVACH_LOCATION wins; otherwise the timezone city is used as a default.
    The timezone is a hint rather than an address — plenty of machines run a
    timezone that is not where they are — so this is overridable and never
    silently authoritative. Without it, "what's the weather" returned Denver
    and the Bahamas: Tavily had nothing to go on.
    """
    override = os.environ.get("KAVACH_LOCATION")
    if override:
        return override.strip()

    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("KAVACH_LOCATION="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value

    try:
        zone = os.readlink("/etc/localtime").split("zoneinfo/")[-1]
        city = zone.split("/")[-1].replace("_", " ")
        return city or None
    except Exception:
        return None


def with_location(query: str) -> str:
    """Add a place to questions that are meaningless without one."""
    low = query.lower()
    if not any(word in low for word in _LOCATION_DEPENDENT):
        return query
    # Already named a place — "weather in Tokyo" must not become
    # "weather in Tokyo in Kolkata".
    if " in " in low or " at " in low:
        return query
    place = local_place()
    return f"{query} in {place}" if place else query


def _api_key() -> str | None:
    """The key from the environment, or from brain/.env.

    Read at call time rather than import: the key can be added without
    restarting, and nothing holds it in memory when search is unused.
    """
    key = os.environ.get("TAVILY_API_KEY")
    if key:
        return key.strip()

    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("TAVILY_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


_MARKDOWN = re.compile(r"\*\*|\*|`|_{2,}")
_LINKS = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_NO_ANSWER = "I couldn't find an answer to that."


def shorten_for_speech(answer: str | None, max_sentences: int = 2) -> str:
    """Trim a written answer to something worth hearing.

    Search answers are written to be read, with markdown and four sentences of
    hedging. Spoken, that is a wall — and silence after a question reads as a
    crash, so an empty answer becomes a short honest line rather than nothing.
    """
    if not answer or not answer.strip():
        return _NO_ANSWER

    text = _LINKS.sub(r"\1", answer)
    text = _MARKDOWN.sub("", text).strip()

    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = " ".join(sentences[:max_sentences]).strip()
    if out and not out.endswith((".", "!", "?")):
        out += "."
    return out or _NO_ANSWER


def search(query: str, log_to=None) -> str | None:
    """Ask Tavily, and speak the answer back.

    Returns None when search is unavailable, so the caller can fall back to
    the model rather than the turn failing.
    """
    key = _api_key()
    if not key:
        log.warning("no TAVILY_API_KEY — web search is off")
        return None

    query = with_location(query)

    payload = json.dumps({
        "api_key": key,
        "query": query,
        "max_results": 3,
        # Tavily composes a direct answer, which is what a voice reply needs —
        # a list of links is useless read aloud.
        "include_answer": True,
        "search_depth": "basic",
    }).encode()

    if log_to is not None:
        # §2 draws the local-first line here. What left the machine, and when.
        log_to.append("web.search", query=query, provider="tavily")

    request = urllib.request.Request(
        ENDPOINT, payload, {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        log.warning("search failed: HTTP %s", exc.code)
        return None
    except Exception as exc:
        log.warning("search failed: %s", exc)
        return None

    answer = data.get("answer")
    if answer:
        return shorten_for_speech(answer)

    results = data.get("results") or []
    if results:
        return shorten_for_speech(results[0].get("content"))
    return _NO_ANSWER
