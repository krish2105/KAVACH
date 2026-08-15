"""A confirmed request must not be confirmed again.

Measured live 2026-08-15, at the end of a long chain that finally worked:

    heard "Delete the note called draft."
    said  "That would delete the note called draft. Say confirm..."
    heard "Confirm."
    → confirmation approved, request re-ran with confirmed=True
    said  "I need your say-so first: this would delete the note titled
           'draft'. Say the word and I'll run it — nothing has been
           deleted yet."

It asked again. `confirmed=True` reaches `respond()` and correctly skips the
router's confirmation, but the **agent** carries its own instruction to read
destructive actions back, and nothing told it the user had already answered.

That is a loop with no exit: every confirmation produces another request for
confirmation. Worse than a gate that refuses, because it looks like progress.

The tool gate still confirms at the point of action — that is the real §7
enforcement and it is untouched. What this removes is the agent asking in
*prose*, a second time, for something already granted.
"""

import pytest

from kavach.reasoning.agent import CONFIRMED_PREFIX, ClaudeAgent


def test_a_confirmed_request_is_marked_for_the_agent():
    """The agent cannot know what happened before its turn unless told."""
    marked = ClaudeAgent.mark_confirmed("delete the note called draft",
                                        confirmed=True)

    assert CONFIRMED_PREFIX in marked
    assert "delete the note called draft" in marked


def test_an_unconfirmed_request_is_passed_through_unchanged():
    """Marking everything would tell the agent every request is pre-approved,
    which is the opposite failure and a far worse one."""
    text = "delete the note called draft"

    assert ClaudeAgent.mark_confirmed(text, confirmed=False) == text


def test_the_marker_says_it_was_the_user_who_confirmed():
    """"Already confirmed" is ambiguous about who did the confirming. The
    agent must not read it as permission it granted itself."""
    marked = ClaudeAgent.mark_confirmed("x", confirmed=True).lower()

    assert "user" in marked


def test_the_marker_is_scoped_to_this_action_not_a_standing_grant():
    """Corrected: the first version of this test forbade the phrase "do not
    ask", which is precisely what needs saying about *this* request. The
    intent was that it grants nothing beyond it, and that is what is asserted
    now.

    The tool gate still confirms at the point of action — that is the real §7
    enforcement — and an agent told it had blanket approval would argue with
    it, or worse, report success it had not been given.
    """
    marked = ClaudeAgent.mark_confirmed("x", confirmed=True).lower()

    # Scoped: it talks about the action in hand.
    assert "this action" in marked

    # Not a standing grant, and not an instruction to defeat the gate.
    for phrase in ("bypass", "always", "any action", "future", "all requests"):
        assert phrase not in marked, phrase
