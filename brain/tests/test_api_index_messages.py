"""Indexing Messages has to happen where the Full Disk Access grant lives.

`uv run kavach-memory index-messages` is refused from a terminal:

    ✗ ~/Library/Messages/chat.db: macOS refused this path … needs Full Disk
      Access

That is correct and it is not a bug. **TCC attributes a grant to the
responsible process**, and for a launchd job that is the daemon, while for a
shell command it is the terminal's parent. The daemon reads `chat.db` fine;
the terminal cannot. This project already measured exactly that asymmetry
when Full Disk Access was granted.

So a feature that only exists in the CLI is a feature the user cannot run.
The endpoint puts the work in the process that holds the grant — and makes
it reachable from the phone, which is where "index my messages" is most
likely to be asked in the first place.

**Still not a sweep.** It is a POST the user makes, with a limit, and it
writes to a collection `forget messages` empties. Nothing schedules it and
nothing calls it on a timer.
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from kavach.api.app import create_app
from kavach.api.confirm import PendingRegistry
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog

TOKEN = "test-token-for-index-messages"


class FakeStore:
    def __init__(self):
        self.stored = []

    def remember(self, text, collection="turns", source=""):
        self.stored.append((text, collection, source))
        return len(self.stored)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from tests.test_api import make_loop

    switch = KillSwitch(log=ActionLog(tmp_path / "actions.jsonl"))
    loop = make_loop(PendingRegistry(), switch)
    loop.memory = FakeStore()
    app = create_app(kill_switch=switch, loop=loop, token=TOKEN)
    return TestClient(app), loop, switch


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_it_needs_the_token(client):
    api, _, _ = client
    assert api.post("/index-messages", json={}).status_code == 401


def test_it_indexes_through_the_gate(client, tmp_path, monkeypatch):
    api, loop, _ = client

    seen = {}

    def fake_index(store, tools, db_path=None, limit=500):
        seen["limit"] = limit
        store.remember("Someone said: hello", collection="messages",
                       source="message with +44, today")
        return 1

    monkeypatch.setattr("kavach.memory.sources.index_messages", fake_index)

    response = api.post("/index-messages", json={"limit": 25}, headers=_auth())

    assert response.status_code == 200, response.text
    assert response.json()["indexed"] == 1
    assert seen["limit"] == 25
    assert loop.memory.stored[0][1] == "messages"


def test_a_missing_grant_is_reported_not_swallowed(client, monkeypatch):
    """The whole reason this endpoint exists is a permission boundary. If it
    ever hits one itself, "0 indexed" would be the least useful answer
    available."""
    api, _, _ = client

    def refuse(store, tools, db_path=None, limit=500):
        raise PermissionError("needs Full Disk Access: System Settings → …")

    monkeypatch.setattr("kavach.memory.sources.index_messages", refuse)

    response = api.post("/index-messages", json={}, headers=_auth())

    assert response.status_code == 403, response.text
    assert "Full Disk Access" in response.json()["detail"]


def test_a_latched_kill_switch_refuses(client, monkeypatch):
    api, _, switch = client
    monkeypatch.setattr(
        "kavach.memory.sources.index_messages",
        lambda *a, **k: pytest.fail("indexed while latched"),
    )
    switch.trigger(source="test", reason="halt")

    response = api.post("/index-messages", json={}, headers=_auth())

    # 409, which is what /command already returns for a latched switch. The
    # first draft of this test asserted 423 — a code invented here rather
    # than read off the codebase, which would have made clients handle
    # "latched" two different ways depending on the route.
    assert response.status_code == 409, response.text


def test_no_memory_store_is_an_error_not_a_silent_zero(client, monkeypatch):
    api, loop, _ = client
    loop.memory = None

    response = api.post("/index-messages", json={}, headers=_auth())

    assert response.status_code == 503, response.text
