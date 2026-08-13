"""Local API tests (Phase 6).

This API can act on the machine, so these are guardrail tests rather than
smoke tests. Every one encodes a rule that the voice path already follows, and
the point of the phase is that reaching KAVACH over HTTP does not quietly
weaken any of them.

The load-bearing difference: a spoken destructive action is gated by the
enrolled voiceprint. An HTTP request has no voice, so the bearer token is the
only thing proving who is asking — and a token alone must not be enough to
delete something. Hence the pending-confirmation flow, and hence most of the
tests below.
"""

import time

import pytest
from fastapi.testclient import TestClient

from kavach.api.app import create_app
from kavach.api.confirm import ApiConfirmer, PendingRegistry
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog

TOKEN = "test-token-do-not-use"


class FakeLoop:
    """Stands in for VoiceLoop. Records what it was asked to do."""

    def __init__(self):
        from kavach.privacy.ghost import GhostMode

        self.commands: list[str] = []
        self.ghost = GhostMode()
        self.state = type("S", (), {
            "as_dict": lambda self: {
                "state": "idle", "transcript": "", "partial": "",
                "amplitude": 0.0, "confidence": 1.0, "route": None,
                "toolCalls": [], "killSwitch": "armed",
            }
        })()

    def respond(self, text: str) -> str:
        self.commands.append(text)
        return f"handled: {text}"


@pytest.fixture
def kill_switch(tmp_path):
    return KillSwitch(log=ActionLog(tmp_path / "actions.jsonl"))


@pytest.fixture
def loop():
    return FakeLoop()


@pytest.fixture
def registry():
    return PendingRegistry()


@pytest.fixture
def client(loop, kill_switch, registry):
    app = create_app(loop=loop, kill_switch=kill_switch, token=TOKEN,
                     registry=registry)
    return TestClient(app)


def auth(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ═══ 1. nothing is reachable without the token ═══

@pytest.mark.parametrize("method,path", [
    ("get", "/status"),
    ("get", "/log"),
    ("get", "/pending"),
    ("post", "/command"),
    ("post", "/confirm"),
    ("post", "/kill"),
])
def test_every_endpoint_refuses_without_a_token(client, method, path):
    call = getattr(client, method)
    response = call(path) if method == "get" else call(path, json={})
    assert response.status_code == 401, path


def test_a_wrong_token_is_refused(client):
    assert client.get("/status", headers=auth("wrong")).status_code == 401


def test_the_websocket_also_requires_the_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises((WebSocketDisconnect, Exception)):
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()


def test_a_valid_token_is_accepted(client):
    assert client.get("/status", headers=auth()).status_code == 200


# ═══ 2. the kill switch outranks the API ═══

def test_a_latched_kill_switch_refuses_commands(client, kill_switch, loop):
    kill_switch.trigger(source="test", reason="latched")
    response = client.post("/command", headers=auth(), json={"text": "what time is it"})
    assert response.status_code == 409
    assert "kill switch" in response.json()["detail"].lower()
    assert loop.commands == [], "the command must not have run"


def test_status_still_readable_while_latched(client, kill_switch):
    """Reading state is how you find out *why* it is stopped."""
    kill_switch.trigger(source="test")
    assert client.get("/status", headers=auth()).status_code == 200


# ═══ 3. safe commands run ═══

def test_a_safe_command_runs_and_answers(client, loop):
    response = client.post("/command", headers=auth(), json={"text": "what time is it"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert "what time is it" in loop.commands


def test_an_empty_command_is_rejected(client, loop):
    assert client.post("/command", headers=auth(), json={"text": "  "}).status_code == 422
    assert loop.commands == []


# ═══ 4. the pending-confirmation flow ═══

async def test_a_destructive_action_blocks_until_answered(registry):
    """The confirmer must not return until someone answers."""
    confirmer = ApiConfirmer(registry, timeout=2.0)

    import asyncio

    task = asyncio.create_task(confirmer.confirm("Delete note 'Draft'"))
    await asyncio.sleep(0.05)

    pending = registry.list()
    assert len(pending) == 1
    assert not task.done(), "it answered before anyone approved"

    registry.answer(pending[0].id, approved=True)
    assert await task is True


async def test_a_denied_action_returns_false(registry):
    import asyncio

    confirmer = ApiConfirmer(registry, timeout=2.0)
    task = asyncio.create_task(confirmer.confirm("Delete everything"))
    await asyncio.sleep(0.05)
    registry.answer(registry.list()[0].id, approved=False)
    assert await task is False


async def test_an_unanswered_confirmation_times_out_denied(registry):
    """Silence is not consent — the same rule the voice path follows."""
    confirmer = ApiConfirmer(registry, timeout=0.3)
    assert await confirmer.confirm("Delete note 'Draft'") is False


async def test_a_timed_out_confirmation_is_cleared(registry):
    confirmer = ApiConfirmer(registry, timeout=0.3)
    await confirmer.confirm("Delete note 'Draft'")
    assert registry.list() == [], "a dead confirmation must not linger"


def test_answering_an_unknown_id_is_refused(client):
    response = client.post("/confirm", headers=auth(),
                           json={"id": "nope", "approved": True})
    assert response.status_code == 404


def test_a_confirmation_cannot_be_answered_twice(registry):
    """Otherwise a replayed approval could authorise a later action."""
    item = registry.register("Delete note 'Draft'")
    assert registry.answer(item.id, approved=True) is True
    assert registry.answer(item.id, approved=True) is False


def test_pending_lists_what_is_waiting(client, registry):
    registry.register("Delete note 'Draft'")
    body = client.get("/pending", headers=auth()).json()
    assert len(body["pending"]) == 1
    assert "Draft" in body["pending"][0]["prompt"]


# ═══ 5. the action log ═══

def test_log_returns_recent_entries(client, kill_switch):
    for i in range(5):
        kill_switch.log.append("test.event", index=i)
    body = client.get("/log?limit=3", headers=auth()).json()
    assert len(body["entries"]) == 3
    assert body["entries"][-1]["index"] == 4, "newest last"


def test_log_limit_is_bounded(client, kill_switch):
    """An unbounded limit would read an arbitrarily large file into memory."""
    assert client.get("/log?limit=100000", headers=auth()).status_code == 422


# ═══ 6. nothing leaks ═══

def test_status_never_returns_the_token(client):
    assert TOKEN not in client.get("/status", headers=auth()).text


def test_status_never_returns_the_voiceprint(client):
    """The biometric itself must not leave, only whether one exists.

    An earlier version of this asserted the string "voiceprint" was absent,
    which failed on the legitimate `voiceprint: "enrolled"` status field — it
    was testing a field name rather than leaked data. What actually matters is
    that no embedding, centroid or threshold crosses the wire.
    """
    body = client.get("/status", headers=auth()).json()

    assert body["voiceprint"] in ("enrolled", "not enrolled", "off", "unknown")
    for leaked in ("embedding", "centroid", "threshold", "similarity"):
        assert leaked not in str(body).lower()
    # A float array is what an embedding looks like once serialised.
    assert not any(isinstance(v, list) and v and isinstance(v[0], float)
                   for v in body.values())


# ═══ 7. a destructive command must become something to approve ═══
#
# The gap these cover: the router short-circuits on `needs_confirmation`
# before any tool reaches the gate, so the gate's confirmer never runs. The
# API used to answer `{"status": "done"}` with nothing pending — the action
# correctly did not happen, but a phone or Watch had nothing to approve and a
# spoken "confirm" arrived as an unrelated new command.

from kavach.killswitch.core import KillSwitch as _KillSwitch  # noqa: E402
from kavach.voice.loop import VoiceLoop  # noqa: E402


class StubRouter:
    """Flags one phrase as destructive. Everything else is ordinary."""

    def __init__(self, destructive: str = "delete the draft in Notes"):
        self.destructive = destructive
        self.seen: list[str] = []

    def route(self, text: str):
        from kavach.reasoning.router import Route, RoutingDecision

        self.seen.append(text)
        return RoutingDecision(
            route=Route.LOCAL,
            confidence=0.9,
            reason="stub",
            intent="stub",
            needs_confirmation=(text == self.destructive),
        )


class StubLocal:
    def __init__(self):
        self.ran: list[str] = []

    def respond(self, text: str) -> str:
        self.ran.append(text)
        return f"ran: {text}"


def make_loop(registry, kill_switch, router=None, local=None):
    """A real VoiceLoop without loading Whisper, Kokoro and the wake model.

    `respond()` is the code under test and it touches none of them, so this
    exercises the real method rather than a reimplementation of it.
    """
    from kavach.voice.loop import VoiceState

    loop = object.__new__(VoiceLoop)
    loop.ks = kill_switch
    loop.state = VoiceState()
    loop.router = router or StubRouter()
    loop.local = local or StubLocal()
    loop.agent = None
    loop.memory = None
    loop.voiceprint = None
    loop.pending = registry
    from kavach.privacy.ghost import GhostMode
    loop.ghost = GhostMode(log=kill_switch.log)
    loop.publish_fn = lambda _: None
    return loop


@pytest.fixture
def real_loop(registry, kill_switch):
    return make_loop(registry, kill_switch)


def test_a_destructive_command_registers_something_to_approve(real_loop, registry):
    reply = real_loop.respond("delete the draft in Notes")

    assert "confirm" in reply.lower()
    waiting = registry.list()
    assert len(waiting) == 1, "nothing was left to approve"
    assert waiting[0].payload == "delete the draft in Notes"
    assert real_loop.local.ran == [], "it acted before anyone approved"


def test_the_api_reports_pending_not_done(registry, kill_switch):
    """`done` for an action that did not happen is the bug this phase found."""
    loop = make_loop(registry, kill_switch)
    app = create_app(loop=loop, kill_switch=kill_switch, token=TOKEN,
                     registry=registry)
    client = TestClient(app)

    body = client.post("/command", headers=auth(),
                       json={"text": "delete the draft in Notes"}).json()

    assert body["status"] == "pending"
    assert body["id"], "a client has nothing to answer without an id"
    assert loop.local.ran == []


def test_denying_over_the_api_never_runs_it(registry, kill_switch):
    loop = make_loop(registry, kill_switch)
    client = TestClient(create_app(loop=loop, kill_switch=kill_switch,
                                   token=TOKEN, registry=registry))

    item_id = client.post("/command", headers=auth(),
                          json={"text": "delete the draft in Notes"}).json()["id"]
    response = client.post("/confirm", headers=auth(),
                           json={"id": item_id, "approved": False})

    assert response.status_code == 200
    assert loop.local.ran == [], "a denial must not act"
    assert registry.list() == []


def test_approving_over_the_api_actually_runs_it(registry, kill_switch):
    """The other half: an approval that does not act is just as wrong."""
    loop = make_loop(registry, kill_switch)
    client = TestClient(create_app(loop=loop, kill_switch=kill_switch,
                                   token=TOKEN, registry=registry))

    item_id = client.post("/command", headers=auth(),
                          json={"text": "delete the draft in Notes"}).json()["id"]
    response = client.post("/confirm", headers=auth(),
                           json={"id": item_id, "approved": True})

    assert response.status_code == 200
    assert loop.local.ran == ["delete the draft in Notes"]
    assert "delete the draft" in (response.json()["reply"] or "")


def test_the_same_approval_cannot_be_replayed(registry, kill_switch):
    loop = make_loop(registry, kill_switch)
    client = TestClient(create_app(loop=loop, kill_switch=kill_switch,
                                   token=TOKEN, registry=registry))

    item_id = client.post("/command", headers=auth(),
                          json={"text": "delete the draft in Notes"}).json()["id"]
    client.post("/confirm", headers=auth(), json={"id": item_id, "approved": True})
    again = client.post("/confirm", headers=auth(),
                        json={"id": item_id, "approved": True})

    assert again.status_code == 404
    assert loop.local.ran == ["delete the draft in Notes"], "it ran twice"


def test_latching_the_kill_switch_refuses_a_held_approval(registry, kill_switch):
    """A latch stops what is already in train, not only what starts after it."""
    loop = make_loop(registry, kill_switch)
    client = TestClient(create_app(loop=loop, kill_switch=kill_switch,
                                   token=TOKEN, registry=registry))

    item_id = client.post("/command", headers=auth(),
                          json={"text": "delete the draft in Notes"}).json()["id"]
    kill_switch.trigger(source="test", reason="latched mid-confirmation")
    response = client.post("/confirm", headers=auth(),
                           json={"id": item_id, "approved": True})

    assert response.status_code == 409
    assert loop.local.ran == []


def test_an_expired_confirmation_cannot_be_approved(registry):
    """Expiry is a denial. A stale yes must not authorise anything."""
    from kavach.api import confirm as confirm_mod

    item = registry.register("Delete note 'Draft'", payload="delete it")
    item.created_at -= confirm_mod.PENDING_TTL + 1

    assert registry.answer(item.id, approved=True) is False
    assert registry.get(item.id) is None
    assert registry.list() == []


# ═══ 8. the spoken half of the same flow ═══

def test_a_spoken_confirm_resolves_the_pending_action(real_loop, registry):
    real_loop.respond("delete the draft in Notes")
    reply = real_loop.respond("confirm")

    assert real_loop.local.ran == ["delete the draft in Notes"]
    assert "delete the draft" in reply
    assert registry.list() == []


def test_a_spoken_no_cancels_it(real_loop, registry):
    real_loop.respond("delete the draft in Notes")
    reply = real_loop.respond("cancel")

    assert real_loop.local.ran == []
    assert reply == "Cancelled."
    assert registry.list() == []


def test_an_unclear_reply_is_not_consent(real_loop, registry):
    """Anything that is not an unambiguous yes leaves the action unperformed."""
    real_loop.respond("delete the draft in Notes")
    real_loop.respond("what time is it")

    assert "delete the draft in Notes" not in real_loop.local.ran
    assert len(registry.list()) == 1, "the question was silently dropped"


# ═══ 9. the kill switch, from a pocket (Phase 7) ═══
#
# The phone can stop KAVACH from anywhere. It deliberately cannot start it
# again — §C's latch means an ambiguous state stays stopped, and "ambiguous"
# very much includes a request arriving from a device that is not in the room.

def test_kill_latches_the_switch(client, kill_switch):
    assert kill_switch.is_armed
    response = client.post("/kill", headers=auth(), json={})

    assert response.status_code == 200
    assert response.json()["kill_switch"] == "disarmed"
    assert not kill_switch.is_armed


def test_after_a_kill_commands_are_refused(client, kill_switch, loop):
    client.post("/kill", headers=auth(), json={})
    response = client.post("/command", headers=auth(),
                           json={"text": "what time is it"})

    assert response.status_code == 409
    assert loop.commands == []


def test_kill_is_idempotent(client, kill_switch):
    """From a pocket, "did that go through?" must be answerable by pressing
    it again. A second kill is confirmation, not an error."""
    first = client.post("/kill", headers=auth(), json={})
    second = client.post("/kill", headers=auth(), json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["kill_switch"] == "disarmed"
    assert not kill_switch.is_armed


def test_kill_is_not_blocked_by_the_kill_switch(client, kill_switch):
    """The one route that must work when everything else is refusing."""
    kill_switch.trigger(source="test", reason="already latched")
    assert client.post("/kill", headers=auth(), json={}).status_code == 200


def test_the_kill_is_logged_with_its_source(client, kill_switch):
    """The action log has to say where the halt came from."""
    client.post("/kill", headers=auth(),
                json={"reason": "phone: stop what you are doing"})

    # Matched on the real record shape: ActionLog.append() writes the name
    # under "event", not "action". The first version of this passed via a
    # loose fallback, which would have kept passing if the event name changed.
    entries = kill_switch.log.read_all()
    kills = [e for e in entries if e.get("event") == "killswitch.trigger"]

    assert len(kills) == 1, f"expected one kill record, got {kills}"
    assert kills[0]["source"] == "api"
    assert kills[0]["reason"] == "phone: stop what you are doing"

    # And the command refused afterwards is recorded too, so the log explains
    # not just the halt but everything the halt then prevented.
    client.post("/command", headers=auth(), json={"text": "what time is it"})
    blocked = [e for e in kill_switch.log.read_all()
               if e.get("event") == "killswitch.blocked"]
    assert blocked, "a refused command left no trace"


def test_no_route_re_arms_the_switch(client):
    """Asserted over the real route table, so nobody adds one later.

    Stopping KAVACH from another device is safe; starting it again from one is
    not. Re-arming stays a deliberate act at the Mac, and the way to keep that
    true is to make adding a route fail this test.
    """
    paths = {r.path for r in client.app.routes}
    for forbidden in ("/rearm", "/arm", "/resume", "/start", "/reset"):
        assert forbidden not in paths, f"{forbidden} re-arms remotely"

    # Nor by another name: no route may CALL rearm(). Checked as a call
    # rather than as a substring — the first version of this matched its own
    # docstring, which made it a test of my prose instead of the code.
    import inspect

    from kavach.api import app as app_mod

    source = inspect.getsource(app_mod.create_app)
    assert ".rearm(" not in source, "a route re-arms the kill switch"


# ═══ 10. where a request came from (Phase 9) ═══
#
# Tailscale Serve injects Tailscale-User-Login on tailnet traffic and strips
# any client-supplied copy before forwarding, so the header is a usable audit
# signal. It is NOT an authorisation one, and the difference matters enough
# that the third test here is the reason this section exists: everything else
# is bookkeeping, that one is a door.

TAILNET = {"Tailscale-User-Login": "krishna@example.com"}


def _origins(kill_switch) -> list[str]:
    return [e.get("origin") for e in kill_switch.log.read_all() if "origin" in e]


def test_a_tailnet_request_records_where_it_came_from(client, kill_switch):
    client.post("/command", headers={**auth(), **TAILNET},
                json={"text": "what time is it"})

    assert _origins(kill_switch) == ["tailnet:krishna@example.com"]


def test_a_local_request_records_that_it_was_local(client, kill_switch):
    client.post("/command", headers=auth(), json={"text": "what time is it"})

    assert _origins(kill_switch) == ["local"]


def test_an_identity_header_does_not_authenticate(client, loop):
    """The load-bearing test of this phase.

    Serve strips client-supplied identity headers, so a forged one can only
    come from something already running on this Mac — which has better options
    than forging a header. That makes it fine for an audit trail and unfit for
    a decision. If this ever passes with a 200, the token has been quietly
    demoted to optional.
    """
    response = client.post("/command", headers=TAILNET,
                           json={"text": "delete everything"})

    assert response.status_code == 401
    assert loop.commands == []


def test_an_identity_header_does_not_outrank_the_kill_switch(client, kill_switch, loop):
    kill_switch.trigger(source="test", reason="latched")
    response = client.post("/command", headers={**auth(), **TAILNET},
                           json={"text": "what time is it"})

    assert response.status_code == 409
    assert loop.commands == []


def test_a_remote_kill_records_its_origin(client, kill_switch):
    """"Who stopped it, and from where" is the question you ask afterwards."""
    client.post("/kill", headers={**auth(), **TAILNET},
                json={"reason": "from the car"})

    halts = [e for e in kill_switch.log.read_all()
             if e.get("event") == "killswitch.trigger"]
    assert len(halts) == 1
    assert halts[0]["origin"] == "tailnet:krishna@example.com"


def test_a_forged_origin_is_still_recorded_as_claimed_not_trusted(client, kill_switch):
    """A header we cannot verify must not be written as though we had.

    Recorded with the tailnet: prefix that says where it claims to be from,
    never as a bare verified identity — the log should not assert more than it
    knows.
    """
    client.post("/command", headers={**auth(), **TAILNET},
                json={"text": "what time is it"})

    origin = _origins(kill_switch)[0]
    assert origin.startswith("tailnet:"), "origin must say how it was learned"


# ═══ 11. ghost mode over the API (Phase 14) ═══
#
# Enter only. Turning your own microphone back ON from somewhere else is the
# one direction that should require being at the machine — the same asymmetry
# as /kill having no /rearm.

def test_ghost_can_be_entered_over_the_api(client, loop):
    response = client.post("/ghost", headers=auth(), json={})

    assert response.status_code == 200
    assert response.json()["ghost"] is True
    assert loop.ghost.is_active


def test_ghost_cannot_be_left_over_the_api(client, loop):
    """No route turns the microphone back on remotely."""
    client.post("/ghost", headers=auth(), json={})

    paths = {r.path for r in client.app.routes}
    for forbidden in ("/unghost", "/ghost/off", "/listen", "/resume"):
        assert forbidden not in paths

    # Nor by passing a flag to the same endpoint.
    client.post("/ghost", headers=auth(), json={"active": False})
    assert loop.ghost.is_active, "the API turned the mic back on"


def test_ghost_over_the_api_needs_the_token(client, loop):
    assert client.post("/ghost", json={}).status_code == 401
    assert not loop.ghost.is_active


def test_status_reports_ghost(client, loop):
    loop.ghost.enter(source="test")
    assert client.get("/status", headers=auth()).json()["ghost"] is True
