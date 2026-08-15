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
from pathlib import Path

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


def index_messages(store, tools, db_path=None, limit: int = 500) -> int:
    """Index recent iMessages. Returns how many rows were written.

    **`messages` was declared in `SOURCES` with no indexer at all**, so the
    collection existed, `forget messages` worked, and nothing could ever put
    anything in it to forget.

    Read through `FileTools`, so the kill switch and the §7 log apply to
    reading your conversations exactly as they apply to any other file, and
    a missing Full Disk Access grant raises rather than reporting an empty
    history.

    Direction is recorded because "tell him yes" *from* you and *to* you are
    different facts, and a later question about who agreed to what cannot be
    answered from the text alone.
    """
    written = 0
    for message in tools.read_messages(db_path=db_path, limit=limit):
        who = message["who"]
        speaker = "You said" if message["from_me"] else f"{who} said"
        text = f"{speaker}: {message['text']}"
        if store.remember(text, collection=SOURCES["messages"],
                          source=f"message with {who}, "
                                 f"{message['when'] or 'an unknown time'}"
                          ) is not None:
            written += 1
    return written


def index_folder(store, tools, folder, recursive: bool = True) -> dict:
    """Index the text files under a folder the user named.

    Moved here from `MemoryStore` because it read the disk with
    `Path.read_text()` — no kill-switch check and no `file.read` in the §7
    log. Two hundred files could be read while the switch was latched and
    leave no record that any of them had been opened. `tools.read` is the one
    gated path, so it is the one used.

    **Never called implicitly**; the folder is always something the user
    typed. A missing folder raises rather than reporting zero, for the same
    reason `index_file` does.
    """
    from .store import MAX_FILE_BYTES, TEXT_SUFFIXES, _chunk

    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise NotADirectoryError(f"{folder} is not a directory")

    indexed, skipped = 0, 0

    for candidate in sorted(folder.glob("**/*" if recursive else "*")):
        if not candidate.is_file() or candidate.suffix.lower() not in TEXT_SUFFIXES:
            continue
        # Skip anything hidden or inside a dot-directory — .git, .venv and
        # friends are noise at best and secrets at worst.
        if any(part.startswith(".") for part in candidate.parts):
            skipped += 1
            continue
        if candidate.stat().st_size > MAX_FILE_BYTES:
            skipped += 1
            continue

        # A per-file failure skips that file; the kill switch stops the run.
        # Catching `Exception` here would swallow `KillSwitchDisarmed` and
        # index the remaining files after a latch — which is the whole reason
        # this loop moved behind the gate.
        try:
            text = tools.read(str(candidate)).strip()
        except (OSError, UnicodeDecodeError, ValueError):
            skipped += 1
            continue

        if not text:
            skipped += 1
            continue

        for chunk in _chunk(text):
            store.remember(chunk, collection=SOURCES["files"],
                           source=str(candidate))
        indexed += 1

    log.info("indexed %d file(s) from %s (%d skipped)", indexed, folder, skipped)
    return {"folder": str(folder), "indexed": indexed, "skipped": skipped}


__all__ = ["Source", "SOURCES", "WORTH_REMEMBERING", "index_actions",
           "index_file", "index_folder", "index_messages"]
