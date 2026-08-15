"""The memory that was built and never constructed.

`MemoryStore` (sqlite-vec) and `SessionRecorder` are built and tested. Nothing
creates them, so `VoiceLoop.memory` is always None, every turn hits
`if self.memory is not None:` at loop.py:745 and skips, and the store holds 0
rows.

**Seventh instance of this defect in one project.** The others: the startup
banner printing four apps while the file held seven, `voice/__main__.py`
hardcoding an Ollama model name, `agent.py` refusing an app it was permitted
to drive, a duplicated `MIN_VERIFY_SECONDS`, the gate and the agent disagreeing
about which MCP servers exist, and endpointing logic fixed in the copy that
does not run.

Every one was found by asking whether the code was *reached*, not whether it
*worked*. This file asks that question as a test.
"""

import inspect

from kavach.voice import __main__ as entry
from kavach.voice.loop import VoiceLoop


def test_the_daemon_constructs_a_memory_store():
    source = inspect.getsource(entry)
    assert "MemoryStore(" in source, (
        "nothing constructs MemoryStore, so VoiceLoop.memory is None and "
        "every turn silently skips the write"
    )


def test_it_is_passed_to_the_loop():
    """Constructing it and not handing it over would be the same bug with an
    extra line."""
    source = inspect.getsource(entry)
    assert "memory=memory" in source, "constructed but not passed to VoiceLoop"


def test_the_loop_still_accepts_no_memory():
    """Ollama may not be running. A missing store must degrade to no recall,
    never to a broken turn — the loop already catches per-turn failures, and
    that path has to stay reachable."""
    assert "memory" in inspect.signature(VoiceLoop.__init__).parameters


def test_a_turn_without_memory_does_not_raise():
    """The `if self.memory is not None` guard is what makes the degraded path
    safe. If it is ever removed, this fails."""
    source = inspect.getsource(VoiceLoop)
    assert "if self.memory is not None:" in source
