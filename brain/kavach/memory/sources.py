"""What may be indexed, and what may never be.

**The line is passive versus asked.** Turns and actions are things KAVACH did
and already recorded — indexing them creates no new collection of anything.
Files, Messages and Mail require you to name them.

**Screen content and ambient audio have no indexer here, deliberately.** The
user cut both as a privacy and storage liability, and §7 says wake-word audio
that was not acted on leaves no trace. `test_memory_sources.py` asserts those
functions *do not exist* and that this module imports nothing that could reach
a microphone or a display — so adding one has to be an argument rather than a
discovery.

File reads go through `FileTools`, never `open()`. A second path to the disk
would be a second gate to keep in sync with the kill switch, the confirmation
and the §7 log — and this project has now got one-fact-in-two-places wrong
seven times.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger("kavach.memory.sources")

#: Every source, and the collection it writes to.
#:
#: The collection is what `MemoryStore.forget()` takes, so a source without
#: one cannot be purged — which would make the privacy promise in the spec
#: unkeepable. A test requires every entry to declare one.
SOURCES = {
    "turns": "turns",
    "actions": "actions",
    "files": "files",
    "messages": "messages",
}

#: Log events worth remembering.
#:
#: The action log carries router decisions and voice scores by the hundred.
#: Indexing those would bury the handful of things that actually happened
#: under the noise of deciding to do them.
WORTH_REMEMBERING = ("action.", "file.write", "file.delete", "proposal.",
                     "allowlist.add", "killswitch.")


@dataclass
class Source:
    name: str
    collection: str


def _when(entry: dict) -> str:
    """A human timestamp for provenance.

    The event's own time, never `now()` — otherwise every memory claims to be
    from today and provenance says nothing.
    """
    stamp = entry.get("ts")
    if not stamp:
        return "an unknown time"
    try:
        return datetime.fromisoformat(str(stamp)).strftime("%a %-d %b, %-I%p")
    except (ValueError, TypeError):
        return str(stamp)


def index_actions(store, action_log) -> int:
    """Index what KAVACH did. Returns how many rows were written."""
    written = 0
    for entry in action_log.read_all():
        event = str(entry.get("event", ""))
        if not event.startswith(WORTH_REMEMBERING):
            continue

        detail = {
            key: value for key, value in entry.items()
            if key not in ("event", "ts")
            and isinstance(value, (str, int, float, bool))
        }
        text = f"KAVACH did {event}: {json.dumps(detail, sort_keys=True)}"
        if store.remember(text, collection=SOURCES["actions"],
                          source=f"action log, {_when(entry)}") is not None:
            written += 1
    return written


def index_file(store, tools, path: str) -> int:
    """Index one file's contents, read through the gated tools.

    **Raises rather than returning 0** when the file cannot be read. Zero
    indexed and could-not-read look identical to a caller, and only one of
    them means the file was empty — the same rule that makes a missing Full
    Disk Access grant an explicit refusal rather than an empty listing.
    """
    text = tools.read(path)
    written = store.remember(text, collection=SOURCES["files"],
                             source=f"file {path}")
    return 1 if written is not None else 0


__all__ = ["Source", "SOURCES", "WORTH_REMEMBERING", "index_actions",
           "index_file"]
