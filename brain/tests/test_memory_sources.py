"""What may be indexed, and what may never be.

**The line is passive versus asked.** Turns and actions are things KAVACH did
and already recorded — indexing them adds no new collection of anything.
Files, Messages and Mail require you to name them.

Screen content and ambient audio have no indexer, deliberately. The user cut
them as a privacy and storage liability, and §7 says wake-word audio that was
not acted on leaves no trace. The tests below assert those functions **do not
exist**, so adding one has to be an argument rather than a discovery.
"""

import pytest

from kavach.memory import sources


# ═══ what was cut stays cut ═══

def test_screen_content_has_no_indexer():
    assert not hasattr(sources, "index_screen")
    assert "screen" not in sources.SOURCES


def test_ambient_audio_has_no_indexer():
    assert not hasattr(sources, "index_audio")
    for forbidden in ("audio", "microphone", "ambient"):
        assert forbidden not in sources.SOURCES


def test_no_indexer_reads_the_microphone_or_the_screen():
    """Asserted on the code, because the property is "this module cannot",
    not "this module currently does not"."""
    import inspect

    from tests._sourcecheck import code_text

    source = code_text(inspect.getmodule(sources))
    for forbidden in ("sounddevice", "InputStream", "screencapture",
                      "CGWindowListCreateImage", "AVCaptureDevice"):
        assert forbidden not in source, f"{forbidden} in sources.py"


# ═══ actions ═══

class FakeStore:
    def __init__(self):
        self.stored = []

    def remember(self, text, collection="turns", source=""):
        self.stored.append((text, collection, source))
        return len(self.stored)


def test_actions_are_indexed_with_their_own_timestamp(tmp_path):
    """Provenance needs the time the thing happened, not the time it was
    indexed — otherwise every memory claims to be from today."""
    from kavach.killswitch.log import ActionLog

    log = ActionLog(tmp_path / "actions.jsonl")
    log.append("action.app_open", app="Notes", ok=True)
    store = FakeStore()

    count = sources.index_actions(store, log)

    assert count == 1
    text, collection, source = store.stored[0]
    assert "Notes" in text
    assert collection == "actions"
    assert "action log" in source


def test_only_events_worth_remembering_are_indexed(tmp_path):
    """The log carries router decisions and voice scores by the hundred.
    Indexing those buries the handful of things that actually happened."""
    from kavach.killswitch.log import ActionLog

    log = ActionLog(tmp_path / "actions.jsonl")
    log.append("action.app_open", app="Notes")
    log.append("router.decision", route="local")
    log.append("voice.score", similarity=0.4)
    store = FakeStore()

    count = sources.index_actions(store, log)

    assert count == 1, [t for t, _, _ in store.stored]


def test_an_empty_log_indexes_nothing(tmp_path):
    from kavach.killswitch.log import ActionLog

    assert sources.index_actions(FakeStore(),
                                 ActionLog(tmp_path / "a.jsonl")) == 0


# ═══ files ═══

def test_a_file_is_read_through_the_gated_tools(tmp_path):
    """Not `open()`. A second path to the disk would be a second gate to keep
    in sync, and this project has got that wrong seven times."""
    target = tmp_path / "notes.txt"
    target.write_text("the quarterly report is due on Friday")
    store = FakeStore()

    class FakeTools:
        def read(self, path):
            return target.read_text()

    count = sources.index_file(store, FakeTools(), str(target))

    assert count == 1
    text, collection, source = store.stored[0]
    assert "quarterly" in text
    assert collection == "files"
    assert str(target) in source


def test_an_unreadable_file_raises_rather_than_returning_zero():
    """Zero-indexed and could-not-read look identical to a caller, and only
    one of them means the file was empty."""

    class Broken:
        def read(self, path):
            raise PermissionError("needs Full Disk Access")

    with pytest.raises(PermissionError):
        sources.index_file(FakeStore(), Broken(), "/protected/thing.txt")


# ═══ every source is purgeable ═══

def test_every_source_declares_a_collection():
    """`MemoryStore.forget()` takes a collection. A source without one cannot
    be purged, which makes the privacy promise unkeepable."""
    assert sources.SOURCES
    for name, collection in sources.SOURCES.items():
        assert isinstance(collection, str) and collection.strip(), name
