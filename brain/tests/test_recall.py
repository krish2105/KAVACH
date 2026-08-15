"""Recall answers with its provenance, or it does not answer.

CHI 2023 on voice-assistant failures: abandonment is **task-specific**. One
truncated text message and the user never sends texts by voice again. A
confident wrong answer costs that task permanently; an honest miss does not.
So a weak match returns None — never a hedged answer.

**The threshold is relative, and that was a correction made during
implementation.** The plan specified `MIN_SCORE = 0.35`. Measured against real
embeddings, `store.py` computes `1.0 / (1.0 + distance)` and the entire usable
range is::

    what did I ask about Chrome     best 0.054   worst 0.038
    did I delete a note             best 0.052   worst 0.037
    what is quantum physics         best 0.039   worst 0.037   <- unrelated
    tell me about the weather       best 0.050   worst 0.038

0.35 would have rejected every result that exists. The separation is real —
0.054 against 0.039 — but compressed into a band 0.015 wide, where any
absolute number is one embedding-model change away from silently wrong.

That is the same error that produced three unusable speaker thresholds in one
evening: a number set from an assumed scale rather than a measured one. So
this asks a question that does not depend on the scale at all — **does the
best match stand out from the rest?** Unrelated results cluster tightly;
a real one separates.
"""

import pytest

from kavach.memory.recall import Answer, MIN_LEAD, recall
from kavach.memory.store import Memory


class FakeStore:
    def __init__(self, results):
        self.results = results

    def search(self, query, limit=5, collection=None):
        return self.results


def memory(text, source, score):
    return Memory(id=1, collection="actions", text=text, source=source,
                  created_at=0.0, score=score)


# ═══ a match that stands out ═══

def test_a_standout_match_is_answered_with_its_source():
    """Real numbers from the measurement above: 0.054 against a 0.038 field."""
    store = FakeStore([
        memory("KAVACH opened Notes", "action log, Fri 8pm", 0.054),
        memory("unrelated one", "action log", 0.038),
        memory("unrelated two", "action log", 0.037),
    ])

    answer = recall(store, "what did I open on Friday")

    assert answer is not None
    assert "Notes" in answer.text
    assert "action log" in answer.sources[0]


def test_a_flat_field_returns_nothing():
    """Everything scoring the same means nothing matched — the shape of the
    "what is quantum physics" row, where best and worst were 0.039 and 0.037."""
    store = FakeStore([
        memory("a", "action log", 0.039),
        memory("b", "action log", 0.038),
        memory("c", "action log", 0.037),
    ])

    assert recall(store, "what is quantum physics") is None


def test_it_does_not_depend_on_the_absolute_scale():
    """The same shape at a completely different magnitude must behave the
    same. This is what an absolute threshold could not do, and why the plan's
    0.35 was wrong."""
    tiny = FakeStore([memory("hit", "s", 0.054), memory("x", "s", 0.038),
                      memory("y", "s", 0.037)])
    large = FakeStore([memory("hit", "s", 0.94), memory("x", "s", 0.66),
                       memory("y", "s", 0.64)])

    assert recall(tiny, "q") is not None
    assert recall(large, "q") is not None


# ═══ nothing at all ═══

def test_an_empty_index_returns_nothing():
    assert recall(FakeStore([]), "anything") is None


def test_a_single_result_with_nothing_to_compare_is_refused():
    """One hit and no field to stand out from. Answering would be trusting a
    number whose scale we just established is not meaningful alone."""
    assert recall(FakeStore([memory("only", "s", 0.05)]), "q") is None


def test_a_store_that_raises_returns_nothing():
    """Ollama down, or the index missing. Recall degrades to "I don't have
    that", never to a broken turn."""

    class Broken:
        def search(self, *a, **k):
            raise RuntimeError("ollama is not running")

    assert recall(Broken(), "q") is None


@pytest.mark.parametrize("question", ["", "   ", None])
def test_an_empty_question_returns_nothing(question):
    assert recall(FakeStore([memory("x", "s", 0.9)]), question) is None


# ═══ provenance ═══

def test_every_returned_source_is_non_empty():
    """A memory with no provenance cannot be checked, which is the same as
    not being trustworthy."""
    store = FakeStore([memory("hit", "", 0.054), memory("x", "s", 0.038),
                       memory("y", "s", 0.037)])

    answer = recall(store, "q")

    assert all(s.strip() for s in answer.sources)


def test_the_lead_required_is_not_zero():
    """A lead of zero means every field has a winner, which is the same as no
    threshold at all."""
    assert MIN_LEAD > 0
