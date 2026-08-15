"""`/say` — how the observer speaks without owning the audio stack.

`kavach-observe` narrates test results. It must not open Kokoro itself: a
second process holding the TTS engine means two audio devices, and two Whisper
instances competing already cost this project an evening.

So it asks the voice daemon, which owns the microphone and the speaker, to say
something. Token-gated like every other route.

**This route speaks; it does not act.** There is no path from here to a tool
call, and a test asserts the text never reaches the router — an endpoint that
turned text into commands would be a way to drive the machine from outside it,
which is precisely what the gate exists to stop.
"""

import pytest
from fastapi.testclient import TestClient

from kavach.api.app import create_app
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog

TOKEN = "test-token"


class FakeLoop:
    def __init__(self):
        self.spoken = []
        self.routed = []
        self.state = type("S", (), {"as_dict": lambda self: {}})()
        self.pending = None

    def speak(self, text):
        self.spoken.append(text)

    def respond(self, text, **kwargs):
        self.routed.append(text)
        return "should never happen"


@pytest.fixture
def stack(tmp_path):
    loop = FakeLoop()
    ks = KillSwitch(log=ActionLog(tmp_path / "a.jsonl"))
    return TestClient(create_app(loop, ks, TOKEN)), loop


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_it_needs_the_token(stack):
    client, _ = stack

    assert client.post("/say", json={"text": "hello"}).status_code == 401


def test_it_speaks(stack):
    client, loop = stack

    response = client.post("/say", headers=auth(), json={"text": "tests passed"})

    assert response.status_code == 200
    assert loop.spoken == ["tests passed"]


def test_the_text_never_reaches_the_router(stack):
    """An endpoint that turned text into commands would be a way to drive the
    machine from outside it. This speaks and nothing else."""
    client, loop = stack

    client.post("/say", headers=auth(), json={"text": "delete everything"})

    assert loop.routed == []


def test_empty_text_says_nothing(stack):
    client, loop = stack

    client.post("/say", headers=auth(), json={"text": "   "})

    assert loop.spoken == []


def test_it_is_logged(stack, tmp_path):
    """§7 — KAVACH making a noise in the room is something that happened."""
    client, loop = stack

    client.post("/say", headers=auth(), json={"text": "hello"})

    events = [e["event"] for e in ActionLog(tmp_path / "a.jsonl").read_all()]
    assert "api.say" in events
