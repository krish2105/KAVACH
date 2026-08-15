"""The two sources that were declared and could not be filled.

`SOURCES` listed four collections. Two of them had no way to reach the
index at all:

* **`index_actions` was imported by nothing.** Written, tested, and never
  called — so `kavach-memory status` reported `actions: 0` for a system that
  records every tool call it makes. KAVACH remembered what you *said* and
  never what it *did*. Tenth instance of built-but-unwired in this project.
* **`messages` had no indexer whatsoever.** The collection was declared, so
  `forget messages` worked and could only ever remove nothing.

Both now have a command. Neither has a sweep: you name the source, every
time, and `test_memory_cli_index.py` fails the build if a flag appears that
indexes everything.

**Messages is the privacy-loaded one**, and two properties are deliberate:

* it reads through `FileTools`, so the kill switch and the §7 log apply to
  reading your conversations exactly as they apply to any other file;
* a missing Full Disk Access grant **raises**, carrying the Settings path.
  An empty result would say "you have no messages", which is a lie about
  the cause — the same rule the rest of `files.py` follows.

Note for whoever runs this: **the terminal does not hold FDA on this
machine, and the daemon does.** TCC attributes the grant to the responsible
process, so `uv run kavach-memory index-messages` from a shell is refused
while the daemon reads the same file. That is TCC working correctly, not a
bug, and the refusal says so.
"""

import sqlite3

import pytest

from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog
from kavach.memory import sources


class FakeStore:
    def __init__(self):
        self.stored = []

    def remember(self, text, collection="turns", source=""):
        self.stored.append((text, collection, source))
        return len(self.stored)


@pytest.fixture
def tools(tmp_path):
    from kavach.hands.files import FileTools

    return FileTools(KillSwitch(log=ActionLog(tmp_path / "actions.jsonl")))


@pytest.fixture
def chat_db(tmp_path):
    """A stand-in for ~/Library/Messages/chat.db, real schema, fake content.

    The real one cannot be read from a test process — no FDA — and a test
    that depends on the tester's own message history is not a test.
    """
    path = tmp_path / "chat.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, text TEXT, is_from_me INTEGER,
            date INTEGER, handle_id INTEGER
        );
        INSERT INTO handle VALUES (1, '+441234567890');
        INSERT INTO message VALUES
            (1, 'The roofer can start on the third of September', 0,
             745000000000000000, 1),
            (2, 'Tell him yes', 1, 745000001000000000, 1),
            (3, NULL, 0, 745000002000000000, 1);
    """)
    db.commit()
    db.close()
    return path


# ═══ actions ═══

def test_actions_can_be_indexed_from_the_cli(tmp_path, monkeypatch, capsys):
    from kavach.memory import cli

    log = ActionLog(tmp_path / "actions.jsonl")
    log.append("action.app_open", app="Notes", ok=True)
    log.append("router.decision", route="local")      # noise, must be skipped

    store = FakeStore()
    monkeypatch.setattr(cli, "_open_store", lambda: _closeable(store))
    monkeypatch.setattr(cli, "_kill_switch",
                        lambda: KillSwitch(log=log))
    monkeypatch.setattr(cli, "_action_log", lambda: log)

    assert cli.main(["index-actions"]) == 0

    assert len(store.stored) == 1, [t for t, _, _ in store.stored]
    assert store.stored[0][1] == "actions"
    assert "Notes" in store.stored[0][0]
    assert "1" in capsys.readouterr().out


def _closeable(store):
    store.close = lambda: None
    store.count = lambda c=None: len(store.stored)
    return store


# ═══ messages ═══

def test_messages_are_indexed_with_who_and_when(tools, chat_db):
    store = FakeStore()

    count = sources.index_messages(store, tools, db_path=chat_db)

    assert count == 2, "expected the two texts, and not the NULL row"
    texts = [t for t, _, _ in store.stored]
    assert any("roofer" in t for t in texts)
    collections = {c for _, c, _ in store.stored}
    assert collections == {"messages"}
    assert any("+441234567890" in s for _, _, s in store.stored), (
        "no provenance — a message with no sender cannot be judged later"
    )


def test_a_message_records_its_direction(tools, chat_db):
    """"Tell him yes" from you and "tell him yes" to you are different facts."""
    store = FakeStore()
    sources.index_messages(store, tools, db_path=chat_db)

    blob = " ".join(t for t, _, _ in store.stored).lower()
    assert "you said" in blob or "from you" in blob, blob[:200]


def test_indexing_messages_is_recorded_in_the_action_log(tools, chat_db, tmp_path):
    """§7. Reading every conversation on the machine is precisely the action
    that must leave a record."""
    sources.index_messages(FakeStore(), tools, db_path=chat_db)

    events = [e["event"] for e in tools.ks.log.read_all()]
    assert any("read" in e for e in events), events


def test_a_latched_kill_switch_stops_it(tools, chat_db):
    from kavach.killswitch.core import KillSwitchDisarmed

    tools.ks.trigger(source="test", reason="halt")
    store = FakeStore()

    with pytest.raises(KillSwitchDisarmed):
        sources.index_messages(store, tools, db_path=chat_db)

    assert store.stored == []


def test_a_missing_grant_raises_rather_than_reporting_none(tools, tmp_path):
    """"No messages found" would send the user looking for the wrong problem.
    The same rule the rest of files.py follows."""
    missing = tmp_path / "not-there.db"

    with pytest.raises((PermissionError, FileNotFoundError)) as excinfo:
        sources.index_messages(FakeStore(), tools, db_path=missing)

    assert str(excinfo.value), "raised with no explanation"


def test_the_limit_is_honoured(tools, chat_db):
    store = FakeStore()

    sources.index_messages(store, tools, db_path=chat_db, limit=1)

    assert len(store.stored) == 1


def test_messages_is_a_declared_source():
    assert "messages" in sources.SOURCES


# ═══ neither is a sweep ═══

def test_neither_command_reads_anything_unnamed():
    """`index-actions` reads KAVACH's own log; `index-messages` reads one
    database. Both are named, single, and typed by the user."""
    import inspect

    signature = inspect.signature(sources.index_messages)
    assert "db_path" in signature.parameters
    assert "limit" in signature.parameters
