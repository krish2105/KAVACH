"""Indexing a source is asked for, never assumed — and it goes through the gate.

The plan for this task assumed `index` and `forget` did not exist. They did.
Reading the file first found three defects instead, all with one root cause:
`cli.py` and `store.index_folder` were written before `memory/sources.py`, and
nobody reconciled them.

1. **`index_folder` reads the disk with `Path.read_text()`.** `FileTools.read`
   checks the kill switch and appends `file.read` to the §7 log; the indexer
   does neither. So `kavach-memory index ~/Documents` could read two hundred
   files during a latched kill switch and leave no record of a single one.
   `sources.py` says in its own docstring that file reads go through
   `FileTools` and never `open()` — this was the second path it warned about.

2. **`forget` accepted only `turns` and `files`.** `SOURCES` holds four
   collections. `forget actions` died in argparse, so the one collection that
   records what KAVACH *did* was the one that could not be purged —
   while `test_memory_sources.py` asserted every source is purgeable and
   passed, because it checks the dict rather than the command.

3. **`status` reported two of the four**, same cause.

Ninth instance of one-fact-in-two-places in this project. Every test below
reads `SOURCES` rather than repeating its contents, so a fifth collection
cannot arrive without the CLI learning about it.
"""

import inspect

import pytest

from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog
from kavach.memory import cli
from kavach.memory.sources import SOURCES
from kavach.memory.store import EmbeddingUnavailable, MemoryStore, embed


def _ollama_ready() -> bool:
    try:
        embed("probe")
        return True
    except EmbeddingUnavailable:
        return False


needs_ollama = pytest.mark.skipif(
    not _ollama_ready(), reason="ollama + nomic-embed-text not available"
)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """The CLI, pointed at a throwaway database and action log.

    `_open_store` returns a *fresh* store per call, because that is what
    production does — `main()` owns its store and closes it in `finally`.
    Handing back one shared object instead made `main()` close the fixture's
    store out from under the assertions, which failed as
    `Cannot operate on a closed database` and looked like a bug in the code
    under test.
    """
    db = tmp_path / "memory.db"
    log = ActionLog(tmp_path / "actions.jsonl")
    switch = KillSwitch(log=log)

    monkeypatch.setattr(cli, "_open_store", lambda: MemoryStore(path=db))
    monkeypatch.setattr(cli, "_kill_switch", lambda: switch)

    def opened() -> MemoryStore:
        return MemoryStore(path=db)

    yield opened, switch, log


# ═══ 1. indexing goes through the gate ═══

@needs_ollama
def test_indexing_is_recorded_in_the_action_log(wired, tmp_path, capsys):
    """§7: every read, logged. A silent index is a copy of your documents that
    nothing can audit."""
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "note.txt").write_text("the quarterly report is due on Friday")

    assert cli.main(["index", str(folder)]) == 0

    _, _, log = wired
    events = [entry["event"] for entry in log.read_all()]
    assert "file.read" in events, f"indexed with no §7 record: {events}"


@needs_ollama
def test_a_latched_kill_switch_stops_indexing(wired, tmp_path, capsys):
    """The kill switch halts in-flight actions. Reading two hundred files is
    an action, and `read_text()` never asked."""
    opened, switch, _ = wired
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "note.txt").write_text("something worth not reading right now")

    switch.trigger(source="test", reason="indexing must stop")

    code = cli.main(["index", str(folder)])

    assert code != 0, "indexed while disarmed"
    assert "kill switch" in capsys.readouterr().err.lower()
    store = opened()
    assert store.count("files") == 0, "read the disk while latched"
    store.close()


def test_the_indexer_does_not_open_the_disk_itself():
    """Asserted on the code, because the property is "it cannot", not "it
    currently happens not to".

    Written first as `"read_text(" not in source` — which passed against the
    bug it was aimed at. `code_text` emits bare identifiers, so no token ever
    carries a paren and that assertion could not fail for any input. A test
    that cannot go red is worse than no test: it reports a guarantee nobody
    is providing. Checked against the real module before trusting the colour.
    """
    from kavach.memory import store as store_module
    from tests._sourcecheck import code_text

    source = code_text(inspect.getmodule(store_module))
    assert "read_text" not in source, (
        "store.py reads the disk directly, bypassing the kill switch and the "
        "§7 log — file reads go through FileTools"
    )


# ═══ 2. every collection can be purged ═══

@pytest.mark.parametrize("collection", sorted(SOURCES))
def test_every_source_can_be_forgotten(wired, collection, capsys):
    """"Memory you cannot audit or delete is surveillance" — cli.py's own
    docstring. A collection argparse refuses is a collection you cannot
    delete."""
    assert cli.main(["forget", collection]) == 0, collection
    assert collection in capsys.readouterr().out


def test_forgetting_an_unknown_collection_is_still_refused(wired):
    """The fix for the above must not be to drop validation — then a typo
    reports success and removes nothing."""
    with pytest.raises(SystemExit):
        cli.main(["forget", "everything-i-ever-said"])


@pytest.mark.parametrize("collection", sorted(SOURCES))
def test_every_source_can_be_searched(wired, collection):
    assert cli.main(["search", "anything", "--collection", collection]) == 0


# ═══ 3. status tells the truth about all of it ═══

@needs_ollama
def test_status_reports_every_collection(wired, capsys):
    """It reported turns and files. Rows in `actions` and `messages` existed
    and were invisible — and what you cannot see, you do not think to purge."""
    opened, _, _ = wired
    store = opened()
    for collection in SOURCES.values():
        store.remember(f"a {collection} row", collection=collection)
    store.close()

    assert cli.main(["status"]) == 0

    out = capsys.readouterr().out
    for collection in SOURCES:
        assert collection in out, f"{collection} missing from status"


# ═══ what was cut stays cut ═══

def test_there_is_no_index_everything_flag():
    """Sweeping the disk without naming a source is the passive capture that
    was cut. It must not be one flag away."""
    source = inspect.getsource(cli)
    for flag in ("--all-files", "--everything", "index_all"):
        assert flag not in source, f"{flag} is a sweep, which was cut"


def test_indexing_a_missing_folder_fails_loudly(wired, tmp_path, capsys):
    code = cli.main(["index", str(tmp_path / "nope")])

    assert code != 0
    assert "nope" in capsys.readouterr().err
