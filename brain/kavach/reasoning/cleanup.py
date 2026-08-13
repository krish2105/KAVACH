"""Strip narrated reasoning from a spoken reply.

Qwen3 emits deliberation as ordinary prose rather than inside <think> tags, so
`think: false` removes nothing and there is no structure to key on. Observed
live, read aloud and shown in the HUD:

    "We are in a scenario where I am KAVACH... The user asks: "What is the
     weather today?" As an AI, I don't have real-time weather data access...
     So, my reply is: I can't check the weather."

The prompt is the first defence. This is the second, because an assistant that
reads its own deliberation aloud is unusable however the fault is apportioned,
and a stronger prompt is a hope rather than a guarantee.
"""

from __future__ import annotations

import re

#: Phrases that only appear when the model is talking about the task rather
#: than doing it. Deliberately specific — a loose match would truncate honest
#: answers, which is worse than the leak.
_REASONING_MARKERS = (
    "we are in a scenario",
    "the user asks",
    "i must reply",
    "as an ai",
    "in the context of this exercise",
    "the problem says",
    "my reply is",
    "i should say",
    "note:",
    "step 1",
    "first, i",
    "let me think",
)

#: Lead-ins the model puts before the real answer.
_PREFIXES = re.compile(
    r"^\s*(so[,:]?\s*)?(my\s+)?(reply|answer|response|output)\s*(is|would be)?\s*[:\-—]\s*"
    r"|^\s*i would say[:,]?\s*"
    r"|^\s*so[,:]\s*",
    re.IGNORECASE,
)

_THINK_TAGS = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)

_FALLBACK = "Sorry — I got tangled up. Ask me again?"


def looks_like_reasoning(text: str | None) -> bool:
    """True if the text narrates the task instead of answering it."""
    if not text:
        return False
    low = text.lower()
    return any(marker in low for marker in _REASONING_MARKERS)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def clean_reply(text: str | None) -> str:
    """Reduce a model reply to one speakable sentence.

    Left alone when it already looks like an answer: over-cleaning a good reply
    is a worse failure than passing a slightly long one through.
    """
    if not text or not text.strip():
        return ""

    text = _THINK_TAGS.sub("", text).strip()
    if not text:
        return ""

    if not looks_like_reasoning(text):
        return _PREFIXES.sub("", text).strip()

    # Work backwards: the answer, when there is one, comes after the
    # deliberation rather than before it.
    for sentence in reversed(_sentences(text)):
        candidate = _PREFIXES.sub("", sentence).strip()
        if candidate and not looks_like_reasoning(candidate) and len(candidate) > 8:
            return candidate

    # It deliberated and never answered. Silence would be more confusing than
    # admitting it, so say something short and honest instead.
    return _FALLBACK
