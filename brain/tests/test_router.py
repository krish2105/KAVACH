"""Router tests (spec §5, working agreement §B).

§5 calls the router "the interesting engineering problem": always calling
Claude is slow and burns credit on "what time is it", but routing a
multi-step, judgement-heavy request to a 4B local model produces confident
nonsense.

Written before the implementation and seen failing first. Do not weaken an
assertion to make one pass — if a case looks wrong, it is a routing decision
worth arguing about, not a test to edit.
"""

import pytest

from kavach.reasoning.router import (
    Route,
    Router,
    RoutingDecision,
    looks_destructive,
)


@pytest.fixture
def router():
    # No Ollama in unit tests: the heuristic pass must stand on its own, and
    # tying these to a running model would make them slow and flaky.
    return Router(local_client=None)


# ——— simple intents stay local (fast, free, offline) ———

@pytest.mark.parametrize("utterance", [
    "what time is it",
    "what's the date today",
    "open Safari",
    "open Notes",
    "launch Calendar",
    "volume up",
    "mute",
    "what's the battery level",
])
def test_simple_intents_route_local(router, utterance):
    decision = router.route(utterance)
    assert decision.route is Route.LOCAL, f"{utterance!r} → {decision.reason}"


# ——— anything needing judgement escalates ———

@pytest.mark.parametrize("utterance", [
    "read my last three emails and draft a reply to the one from my manager",
    "look at the spreadsheet on my desktop and tell me which row is wrong",
    "summarise this page and add the key points to my notes",
    "figure out why the build is failing and fix it",
    "compare these two documents and tell me what changed",
])
def test_complex_requests_route_to_claude(router, utterance):
    decision = router.route(utterance)
    assert decision.route is Route.CLAUDE, f"{utterance!r} → {decision.reason}"


def test_multi_step_requests_escalate(router):
    """'and then' is the cheapest reliable signal of a multi-step task."""
    assert router.route("open Safari and then search for flights").route is Route.CLAUDE


def test_ambiguous_input_escalates_rather_than_guessing(router):
    """Unknown routes to the more capable model. A 4B model answering
    confidently about something it cannot do is worse than the latency."""
    decision = router.route("do the thing with the stuff from earlier")
    assert decision.route is Route.CLAUDE


# ——— confidence drives the orb's outer shell (§4 #3) ———

def test_decisions_carry_confidence_in_range(router):
    for utterance in ["what time is it", "do something vague", "open Safari"]:
        decision = router.route(utterance)
        assert 0.0 <= decision.confidence <= 1.0


def test_local_routing_is_more_confident_than_escalation(router):
    """The ring should read 'sure' when it handled it itself and 'less sure'
    when it had to hand off — that is the whole signal §4 #3 describes."""
    confident = router.route("what time is it")
    unsure = router.route("do the thing with the stuff from earlier")
    assert confident.confidence > unsure.confidence


# ——— §7: destructive intent must be flagged regardless of route ———

@pytest.mark.parametrize("utterance", [
    "delete the draft in Notes",
    "send that email to Priya",
    "empty the trash",
    "buy the laptop stand",
    "reply to my manager and send it",
    "submit the form",
])
def test_destructive_intent_is_flagged(utterance):
    assert looks_destructive(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "what time is it",
    "open Safari",
    "read my calendar",
    "what did she send me",
])
def test_read_only_intent_is_not_flagged(utterance):
    assert not looks_destructive(utterance), utterance


def test_destructive_requests_require_confirmation(router):
    decision = router.route("delete the draft in Notes")
    assert decision.needs_confirmation, "a delete must never run unconfirmed (§7)"


def test_safe_requests_do_not_require_confirmation(router):
    assert not router.route("what time is it").needs_confirmation


# ——— empty / junk never becomes a command ———

@pytest.mark.parametrize("utterance", ["", "   ", "Thank you.", "you"])
def test_empty_or_hallucinated_input_is_rejected(router, utterance):
    decision = router.route(utterance)
    assert decision.route is Route.REJECT


# ——— every decision is auditable (§5: "log every routing decision") ———

def test_decisions_are_recorded_for_tuning(router):
    router.route("what time is it")
    router.route("read my emails and summarise them")

    assert len(router.decisions) == 2
    assert all(isinstance(d, RoutingDecision) for d in router.decisions)
    assert all(d.reason for d in router.decisions), "every decision needs a why"


def test_decision_serialises_for_the_action_log(router):
    payload = router.route("open Safari").as_dict()
    assert {"route", "confidence", "reason", "needs_confirmation"} <= set(payload)
    assert payload["route"] == "local"


# ——— the past-tense carve-out must not open a hole ———

@pytest.mark.parametrize("utterance", [
    "can you delete the draft",          # question-shaped, still an instruction
    "please send that email",
    "delete it",
    "what should I delete — no, delete the draft",
])
def test_question_shaped_instructions_are_still_flagged(utterance):
    """Excluding past-tense queries must not let imperatives phrased politely
    slip through unconfirmed."""
    assert looks_destructive(utterance), utterance


@pytest.mark.parametrize("utterance", [
    "what did she send me",
    "who sent that",
    "when did he delete it",
    "which file did I remove",
])
def test_questions_about_completed_actions_are_read_only(utterance):
    assert not looks_destructive(utterance), utterance


# ——— §7/§C: permission_mode is not a tunable ———

def test_agent_never_auto_approves():
    """§7 and §C both state this is not a tunable. Asserted rather than
    trusted to a default that could change under us."""
    from kavach.reasoning.agent import PERMISSION_MODE, ClaudeAgent

    forbidden = {
        "acceptedits", "acceptEdits", "bypassPermissions",
        "bypasspermissions", "auto", "yolo", "dangerously-skip-permissions",
    }
    assert PERMISSION_MODE.lower() not in {f.lower() for f in forbidden}
    assert ClaudeAgent().options().permission_mode == PERMISSION_MODE


def test_agent_has_no_tools_in_phase_3():
    """Tools arrive in Phase 4, behind the allowlist and confirmation flow."""
    from kavach.reasoning.agent import ClaudeAgent

    assert ClaudeAgent().options().allowed_tools == []


def test_local_model_self_reported_confidence_is_ignored():
    """qwen3:4b returns exactly 0.95 on every call, right or wrong. §4 #3
    renders confidence as the orb's shell opacity, so passing that constant
    through would show a number that never moves."""
    class ConstantConfidence:
        def classify_intent(self, text):
            return {"intent": "simple", "confidence": 0.95}

    router = Router(local_client=ConstantConfidence())
    decision = router.route("frobnicate the widget please")
    assert decision.confidence != 0.95


def test_draft_as_a_noun_is_not_a_generation_task(router):
    """'delete the draft' is a noun; 'draft a reply' is a verb. Matching the
    bare word sent every mention of a draft file to Claude."""
    decision = router.route("delete the draft in Notes")
    assert "generation" not in decision.reason
    assert decision.needs_confirmation, "still destructive, whatever the route"


def test_draft_as_a_verb_still_escalates(router):
    assert router.route("draft a reply to my manager").route is Route.CLAUDE


# ——— screen understanding (Peekaboo was installed in Phase 0, never reached) ———

@pytest.mark.parametrize("utterance", [
    "what's on my screen",
    "what am I looking at",
    "describe my screen",
    "read the screen for me",
    "what does this say on my screen",
])
def test_screen_questions_escalate_for_vision(router, utterance):
    """Peekaboo has been installed, permission-granted and gated since Phase 0,
    but the router had no intent that led to it, so it was never reachable."""
    decision = router.route(utterance)
    assert decision.route is Route.CLAUDE, f"{utterance!r} → {decision.reason}"


def test_screen_questions_are_not_treated_as_destructive(router):
    """Looking is not acting; asking to confirm a read trains reflexive yeses."""
    assert not router.route("what's on my screen").needs_confirmation


def test_screen_wording_does_not_hijack_unrelated_requests(router):
    """'open Safari' must stay a simple local intent even though a screen is
    involved in the abstract."""
    assert router.route("open Safari").route is Route.LOCAL
