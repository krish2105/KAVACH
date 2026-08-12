"""Append-only local action log (spec §7).

Every tool call, every argument, timestamped, written to a local JSONL file.
The kill switch is this log's first writer; Phase 4's MCP tool-call logging
reuses it rather than inventing a second format.

Two properties matter more than convenience here:

* **Append-only.** Records are never rewritten, so a log line is evidence of
  what the agent actually did, not of what it currently thinks it did.
* **Never committed.** ``logs/`` and ``*.jsonl`` are gitignored — this file
  records everything the agent touched on this machine.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LOG_PATH = Path.home() / ".kavach" / "logs" / "actions.jsonl"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActionLog:
    """A thread-safe append-only JSONL log.

    Writes are locked because the kill switch can be triggered from the AppKit
    main thread (hotkey, menubar) while the asyncio loop is writing from
    another — two interleaved partial lines would corrupt the audit trail
    exactly when it matters most.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_LOG_PATH
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **fields: Any) -> dict[str, Any]:
        """Append one record and return it. Returns the record for callers
        that want to echo it (the CLI prints the kill record it caused)."""
        record: dict[str, Any] = {"ts": _utc_now_iso(), "event": event}
        record.update(fields)
        line = json.dumps(record, default=str, ensure_ascii=False)

        with self._lock:
            # Open per-write with O_APPEND rather than holding a handle: an
            # append is then atomic against other processes writing the same
            # log, and a crash mid-session cannot lose buffered records.
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (line + "\n").encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)

        return record

    def read_all(self) -> list[dict[str, Any]]:
        """Read every well-formed record. Malformed trailing lines are skipped
        rather than raising — a torn last line must not make the whole audit
        trail unreadable."""
        if not self.path.exists():
            return []

        records: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records
