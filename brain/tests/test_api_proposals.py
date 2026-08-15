"""Phase 33's review surface — the queue, reachable from the phone.

A queue with no way to review it is a queue that only ever fills up. Phase 6's
API (`reach-6`) is the surface the spec names, and Phase 7 already reaches it
from the phone over Tailscale Serve, so this is three endpoints rather than a
new transport.

**Everything here requires the bearer token**, like every other route. An
unauthenticated approve endpoint would be a way to authorise a destructive
action from outside the machine, which is the one thing the whole gate exists
to prevent.

**Approving through the API does not execute anything.** It marks a proposal
approved; the tool gate still runs when it is attempted — kill switch,
confirmation, log. The queue is not a second permission system.
"""

import pytest
from fastapi.testclient import TestClient

from kavach.api.app import create_app
from kavach.autonomy.proposals import ProposalQueue, Status
from kavach.autonomy.tiers import TierPolicy
from kavach.autonomy.trust import TrustLedger
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog

TOKEN = "test-token"


class FakeLoop:
    def __init__(self):
        self.state = type("S", (), {"as_dict": lambda self: {}})()
        self.pending = None


@pytest.fixture
def stack(tmp_path):
    ks = KillSwitch(log=ActionLog(tmp_path / "actions.jsonl"))
    tiers = TierPolicy(path=tmp_path / "tiers.json")
    trust = TrustLedger(path=tmp_path / "trust.json", tiers=tiers)
    queue = ProposalQueue(path=tmp_path / "proposals.json", trust=trust)
    app = create_app(FakeLoop(), ks, TOKEN, queue=queue)
    return TestClient(app), queue


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


# ═══ the token gates all of it ═══

@pytest.mark.parametrize("method,path", [
    ("get", "/proposals"),
    ("post", "/proposals/approve"),
    ("post", "/proposals/reject"),
])
def test_every_proposal_route_needs_the_token(stack, method, path):
    """An unauthenticated approve endpoint is a way to authorise a
    destructive action from outside the machine."""
    client, _ = stack

    # GET takes no body; only the POSTs do.
    response = (client.get(path) if method == "get"
                else client.post(path, json={"ids": ["x"]}))

    assert response.status_code == 401


# ═══ reading ═══

def test_the_queue_is_listable(stack):
    client, queue = stack
    queue.add("file.delete", "delete ~/Downloads/old.zip")

    body = client.get("/proposals", headers=auth()).json()

    assert len(body["proposals"]) == 1
    assert "old.zip" in body["proposals"][0]["description"]


def test_only_pending_items_are_offered_for_review(stack):
    client, queue = stack
    queue.reject(queue.add("file.delete", "already decided").id)
    queue.add("file.delete", "still waiting")

    body = client.get("/proposals", headers=auth()).json()

    assert [p["description"] for p in body["proposals"]] == ["still waiting"]


# ═══ deciding ═══

def test_approving_marks_it_approved(stack):
    client, queue = stack
    item = queue.add("file.delete", "delete something")

    response = client.post("/proposals/approve", headers=auth(),
                           json={"ids": [item.id]})

    assert response.status_code == 200
    assert queue.get(item.id).status is Status.APPROVED


def test_a_batch_is_approved_together(stack):
    """The point of a queue rather than a prompt."""
    client, queue = stack
    ids = [queue.add("file.delete", f"delete {n}").id for n in "abc"]

    body = client.post("/proposals/approve", headers=auth(),
                       json={"ids": ids}).json()

    assert body["decided"] == 3


def test_an_unknown_id_is_reported_not_silently_ignored(stack):
    """Silently succeeding would let the phone believe it approved
    something."""
    client, queue = stack
    item = queue.add("file.delete", "real one")

    body = client.post("/proposals/approve", headers=auth(),
                       json={"ids": [item.id, "not-a-real-id"]}).json()

    assert body["decided"] == 1
    assert body["failed"]


def test_rejecting_works_the_same_way(stack):
    client, queue = stack
    item = queue.add("file.delete", "delete something")

    client.post("/proposals/reject", headers=auth(), json={"ids": [item.id]})

    assert queue.get(item.id).status is Status.REJECTED


def test_an_expired_proposal_cannot_be_approved_from_the_phone(stack):
    """The 120s-confirmation rule, one level up: something nobody reviewed in
    time must not be resurrected."""
    client, queue = stack
    item = queue.add("file.delete", "x", ttl_seconds=0)
    queue.sweep(now=item.created_at + 1)

    body = client.post("/proposals/approve", headers=auth(),
                       json={"ids": [item.id]}).json()

    assert body["decided"] == 0
    assert body["failed"]


# ═══ it stays a queue, not an executor ═══

def test_approving_executes_nothing(stack):
    """`ready_to_run` means "may be attempted". The gate still runs."""
    client, queue = stack
    item = queue.add("file.delete", "delete something")

    client.post("/proposals/approve", headers=auth(), json={"ids": [item.id]})

    assert [p.id for p in queue.ready_to_run()] == [item.id]


def test_no_queue_configured_is_an_empty_list_not_a_crash(tmp_path):
    """The API must start on a system where nobody has touched autonomy."""
    ks = KillSwitch(log=ActionLog(tmp_path / "a.jsonl"))
    client = TestClient(create_app(FakeLoop(), ks, TOKEN))

    body = client.get("/proposals", headers=auth()).json()

    assert body["proposals"] == []
