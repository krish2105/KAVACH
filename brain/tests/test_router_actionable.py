"""Requests that need hands must not be answered by a model that has none.

Measured, through the running system:

    POST /command  "open Notes"
    → route=local · "simple intent (app control)"
    → reply: "Notes are now open."
    → Notes was not open.

That is the worst failure this project can have: a confident claim to have done
something it never did. No tool call, no gate, no error.

The cause is a category error in the router. `_SIMPLE_PATTERNS` treats "simple
to understand" as "simple to answer", and sends `open X`, volume, mute, media
control and battery to the local 3B model — which has no tools and narrates a
plausible success instead.

"what is the time" belongs there: code answers it from the system clock in 7ms.
"open Notes" does not: nothing can answer it without touching the machine.
"""

import pytest

from kavach.reasoning.router import Route, Router


@pytest.fixture
def router():
    return Router()


# ═══ things that require acting on the machine ═══

@pytest.mark.parametrize("utterance", [
    "open Notes",
    "open Safari",
    "launch Calendar",
    "quit Music",
    "close Finder",
    "volume up",
    "turn the volume down",
    "mute",
    "unmute",
    "play",
    "pause",
    "next track",
])
def test_an_actionable_request_reaches_the_tool_route(router, utterance):
    """Otherwise the local model invents the outcome."""
    decision = router.route(utterance)

    assert decision.route is Route.CLAUDE, (
        f"{utterance!r} went to {decision.route} — a model with no tools, "
        f"which will describe having done it"
    )


# ═══ things code can genuinely answer ═══

@pytest.mark.parametrize("utterance", [
    "what time is it",
    "what is the time right now",
    "what's the date",
    "tell me the time",
])
def test_the_clock_still_answers_locally(router, utterance):
    """The one simple intent with a real local implementation: the system
    clock, in 7ms, never reaching a language model. Routing it out would make
    every "what time is it" a network round trip."""
    decision = router.route(utterance)

    assert decision.route is Route.LOCAL
    assert decision.intent == "clock"


@pytest.mark.parametrize("utterance", [
    "hello",
    "how are you",
    "thanks",
])
def test_small_talk_stays_local(router, utterance):
    """The local model is still the right answer for conversation — this is
    about acting, not about chat."""
    assert router.route(utterance).route is Route.LOCAL


def test_the_reason_says_why_it_escalated(router):
    """The HUD shows this string (§13), so it has to be true."""
    decision = router.route("open Notes")

    assert "tool" in decision.reason.lower() or "act" in decision.reason.lower(), \
        f"reason was {decision.reason!r}, which does not explain the escalation"


# ═══ a destructive request is an action by definition ═══

@pytest.mark.parametrize("utterance", [
    "delete the note called KAVACH scratch",
    "delete the draft in notes",
    "send that email to my manager",
    "trash the file on my desktop",
    "empty the trash",
])
def test_anything_needing_confirmation_reaches_the_tool_route(router, utterance):
    """The dangerous version of the same category error.

    Measured: "delete the note called KAVACH scratch" fell past every regex to
    the local model's own classifier, which called it simple. The §7
    confirmation fired correctly and blocked it — and once approved, the
    resumed turn routed local *again* and replied "There is no note called
    KAVACH." with no tool call. Nothing was deleted.

    That reply happened to be harmless. The same path could as easily have said
    "Deleted." — you approve a destructive action, KAVACH says something, and
    there is no way to tell whether it happened.

    If a request needs confirmation it is an action, and an action cannot be
    carried out by a model with no hands.
    """
    decision = router.route(utterance)

    assert decision.needs_confirmation, \
        f"{utterance!r} was not flagged destructive — a separate bug"
    assert decision.route is Route.CLAUDE, (
        f"{utterance!r} → {decision.route}: approved destructive actions would "
        f"be answered by a model that cannot perform them"
    )


def test_the_local_classifier_cannot_send_a_destructive_request_local():
    """Reproduces the live path, which the test above does not.

    Without Ollama the router escalates by default, so a plain `Router()` makes
    these look fine. Live, the classifier runs, says "simple", and its verdict
    was returned unchanged — which is how an approved deletion came back as a
    sentence with no tool call behind it.
    """
    class SaysSimple:
        def classify_intent(self, text):
            return {"complexity": "simple", "intent": "", "confidence": 0.75}

    router = Router(local_client=SaysSimple())
    decision = router.route("delete the note called KAVACH scratch")

    assert decision.needs_confirmation is True
    assert decision.route is Route.CLAUDE, \
        "the classifier's 'simple' overrode the fact that this is an action"
