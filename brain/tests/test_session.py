"""Session recorder (Phase 16).

> A rolling local buffer (last ~15 minutes, configurable) of transcript and
> actions, with an export command. Local only, no upload path.

Two things make this a privacy feature rather than a surveillance one, and both
are tested here rather than asserted in a comment:

* **It forgets.** Anything past the window is gone from memory, not merely
  hidden. A buffer that quietly keeps everything is a recording device.
* **It cannot leave the machine.** No network client, no upload path, and
  export writes where you tell it and nowhere else.

Worth knowing while reading this: the action log *already* keeps every
utterance permanently, via `router.decision(utterance=...)`. This buffer is
deliberately narrower.
"""

import json

import pytest

from kavach.memory.session import SessionRecorder


@pytest.fixture
def clock():
    """Time under test control — a rolling window tested with sleep() is a
    test that is either slow or flaky, and usually both."""
    class Clock:
        now = 1_000.0

        def __call__(self):
            return self.now

        def advance(self, seconds):
            self.now += seconds

    return Clock()


@pytest.fixture
def recorder(clock):
    return SessionRecorder(window_seconds=900, now=clock)


# ═══ 1. it records ═══

def test_it_records_a_turn(recorder):
    recorder.record_turn("what time is it", "It's 5 p.m.")

    entries = recorder.entries()
    assert len(entries) == 1
    assert entries[0]["transcript"] == "what time is it"
    assert entries[0]["reply"] == "It's 5 p.m."


def test_it_records_an_action(recorder):
    recorder.record_action("Notes.create", {"title": "Draft"})

    assert recorder.entries()[0]["action"] == "Notes.create"


def test_entries_come_back_oldest_first(recorder, clock):
    recorder.record_turn("first", "a")
    clock.advance(1)
    recorder.record_turn("second", "b")

    assert [e.get("transcript") for e in recorder.entries()] == ["first", "second"]


# ═══ 2. it forgets — the part that makes it a privacy feature ═══

def test_entries_older_than_the_window_are_dropped(recorder, clock):
    recorder.record_turn("ancient history", "a")
    clock.advance(901)
    recorder.record_turn("recent", "b")

    transcripts = [e.get("transcript") for e in recorder.entries()]
    assert "ancient history" not in transcripts
    assert "recent" in transcripts


def test_forgetting_is_real_not_cosmetic(recorder, clock):
    """Dropped from memory, not filtered on read. A buffer that still holds
    the data and merely declines to show it has not forgotten anything."""
    recorder.record_turn("secret", "a")
    clock.advance(901)
    recorder.prune()

    assert "secret" not in json.dumps(recorder._entries, default=str)


def test_the_window_is_configurable(clock):
    short = SessionRecorder(window_seconds=60, now=clock)
    short.record_turn("old", "a")
    clock.advance(61)

    assert short.entries() == []


# ═══ 3. ghost mode ═══

def test_nothing_is_recorded_in_ghost_mode(clock):
    from kavach.privacy.ghost import GhostMode

    ghost = GhostMode()
    recorder = SessionRecorder(window_seconds=900, now=clock, ghost=ghost)
    ghost.enter(source="test")

    recorder.record_turn("said while invisible", "reply")
    recorder.record_action("Notes.create", {})

    assert recorder.entries() == [], "ghost mode was recorded anyway"


def test_recording_resumes_after_ghost(clock):
    from kavach.privacy.ghost import GhostMode

    ghost = GhostMode()
    recorder = SessionRecorder(window_seconds=900, now=clock, ghost=ghost)
    ghost.enter(source="test")
    recorder.record_turn("hidden", "x")
    ghost.leave(source="test")

    recorder.record_turn("visible", "y")

    assert [e.get("transcript") for e in recorder.entries()] == ["visible"]


# ═══ 4. it cannot leave the machine ═══

def test_export_writes_where_it_is_told(recorder, tmp_path):
    recorder.record_turn("hello", "hi")
    target = tmp_path / "session.jsonl"

    written = recorder.export(target)

    assert written == target and target.exists()
    lines = [json.loads(l) for l in target.read_text().splitlines() if l.strip()]
    assert lines[0]["transcript"] == "hello"


def test_export_is_readable_only_by_you(recorder, tmp_path):
    """It holds transcripts of everything you said in the last 15 minutes."""
    recorder.record_turn("hello", "hi")
    target = tmp_path / "session.jsonl"
    recorder.export(target)

    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_the_module_has_no_network_path():
    """"Local only, no upload path" — asserted against the source, not trusted.

    A future edit that adds `requests` here would be a genuine change in what
    this feature *is*, and should have to delete this test to happen.
    """
    import ast
    import inspect

    from kavach.memory import session

    # Parsed, not grepped. The first version of this searched the raw source
    # for "upload" and matched its own docstring — a test of my prose rather
    # than of the code. Walking the import table is both stricter and immune
    # to what the comments happen to say.
    tree = ast.parse(inspect.getsource(session))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    network = {"requests", "httpx", "urllib", "socket", "http", "aiohttp",
               "ftplib", "smtplib", "websockets", "boto3", "paramiko"}
    leaked = imported & network
    assert not leaked, f"session.py can reach the network via {leaked}"


def test_exporting_an_empty_buffer_is_not_an_error(recorder, tmp_path):
    target = tmp_path / "empty.jsonl"
    recorder.export(target)
    assert target.exists() and target.read_text() == ""
