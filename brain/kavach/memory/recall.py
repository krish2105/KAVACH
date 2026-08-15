"""A question becomes an answer with provenance, or it becomes nothing.

CHI 2023 on voice-assistant failures: abandonment is **task-specific**. One
truncated text message and the user never sends texts by voice again. A
confident wrong answer costs the task permanently; an honest miss does not.

So there is no hedged answer here. Either the best match stands out and it
answers with sources, or it returns None and the caller says "I don't have
that".

**The threshold is relative, and that is a correction, not a preference.**

The plan for this module specified an absolute `MIN_SCORE = 0.35`. Measured
against real embeddings before writing a line, `store.py` computes
``1.0 / (1.0 + distance)`` and the whole usable range turned out to be::

    what did I ask about Chrome     best 0.054   worst 0.038
    did I delete a note             best 0.052   worst 0.037
    what is quantum physics         best 0.039   worst 0.037   <- unrelated
    tell me about the weather       best 0.050   worst 0.038

0.35 would have rejected every result that exists. The separation is genuine —
0.054 against 0.039 — but compressed into a band 0.015 wide, and any absolute
number there is one embedding-model change away from being silently wrong with
no test that would catch it.

That is precisely the error that produced three unusable speaker thresholds in
a single evening: **a number set from an assumed scale rather than a measured
one.** So this asks a question the scale cannot invalidate — *does the best
match stand out from the field?* Unrelated results cluster tightly; a real one
separates. Change the embedding model tomorrow and the question still holds.

**The model never sees a retrieved document without its provenance attached**,
so it cannot merge three sources into one unattributed claim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from statistics import median

log = logging.getLogger("kavach.memory.recall")

#: How far ahead of the field the best match must be, as a fraction of the
#: field's own level. Relative, so it survives an embedding-model change.
#:
#: **Placed in the middle of a measured gap, not chosen.** The first value
#: here was 0.25, picked by eye, and it missed two of four true matches on
#: real embeddings. Measured properly against nomic-embed-text::
#:
#:     true matches    +0.184  +0.207  +0.263  +0.610
#:     nonsense        +0.007  +0.022  +0.032  +0.046
#:     gap             +0.138
#:
#: 0.115 is the midpoint. That is the same method `choose_threshold` uses for
#: the speaker gate, and for the same reason: a number placed in a measured
#: gap can be defended, and a round one cannot.
MIN_LEAD = 0.115

#: How many memories to consider. Small on purpose — an answer assembled from
#: ten weak sources is exactly the synthesis this module exists to prevent.
#: It also has to be enough to *have* a field to stand out from, which is why
#: a single result is refused rather than trusted.
LIMIT = 5


@dataclass
class Answer:
    text: str
    sources: list[str] = field(default_factory=list)


def recall(store, question: str | None, min_lead: float = MIN_LEAD
           ) -> Answer | None:
    """Answer from memory, or None.

    None is the common case and the safe one. **The caller must not turn it
    into a guess** — that is the whole point of returning it.
    """
    if not question or not str(question).strip():
        return None

    try:
        found = list(store.search(question, limit=LIMIT))
    except Exception:
        # Ollama down, or the index missing. Recall degrades to "I don't have
        # that", never to a broken turn — the same rule the wake-word scorer
        # follows, where an exception must not end listening.
        log.debug("recall search failed", exc_info=True)
        return None

    if len(found) < 2:
        # One hit and no field to stand out from. Answering would mean
        # trusting a number whose scale is not meaningful on its own, which
        # is the mistake this module was written to avoid.
        return None

    ranked = sorted(found, key=lambda m: getattr(m, "score", 0.0), reverse=True)
    best = getattr(ranked[0], "score", 0.0)
    rest = [getattr(m, "score", 0.0) for m in ranked[1:]]
    field_level = median(rest) if rest else 0.0

    if field_level <= 0 or (best - field_level) / field_level < min_lead:
        log.debug("no standout: best %.4f against field %.4f",
                  best, field_level)
        return None

    winner = ranked[0]
    return Answer(
        text=winner.text,
        sources=[s for s in [getattr(winner, "source", "")] if (s or "").strip()],
    )


__all__ = ["Answer", "recall", "MIN_LEAD", "LIMIT"]
