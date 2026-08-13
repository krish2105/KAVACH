"""Rolling session recorder (Phase 16).

> A rolling local buffer (last ~15 minutes, configurable) of transcript and
> actions, with an export command. Local only, no upload path.

**In memory, and it genuinely forgets.** Entries past the window are discarded
from the deque, not filtered out on read — a buffer that still holds the data
and merely declines to show it has not forgotten anything, and would hand it
all over to the first person who called the wrong method. Nothing is written to
disk until you explicitly export.

**No upload path**, and a test asserts that against this module's own source
rather than trusting the sentence you just read. Adding an HTTP client here
would change what this feature is, and should have to delete a test to happen.

**Ghost mode is honoured**: while KAVACH is not sensing, this records nothing.

One thing worth knowing, because it is more retention than this file implies:
the action log **already** keeps every utterance permanently, through
`router.decision(utterance=...)`. This buffer is a narrower, self-expiring view
and does not change that.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("kavach.memory.session")

#: Fifteen minutes, as specified. Long enough to answer "what did I just ask
#: it?", short enough that it is not a record of your day.
DEFAULT_WINDOW_SECONDS = 900.0

DEFAULT_EXPORT_DIR = Path.home() / ".kavach" / "exports"


class SessionRecorder:
    """The last N seconds of what was said and done.

    `now` is injectable so the expiry rule can be tested without sleeping —
    a rolling-window test built on real time is slow, flaky, or both.
    """

    def __init__(self, window_seconds: float = DEFAULT_WINDOW_SECONDS,
                 now: Callable[[], float] | None = None, ghost=None):
        self.window_seconds = float(window_seconds)
        self._now = now or time.time
        self.ghost = ghost
        self._entries: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()

    # ——— recording ———

    def _add(self, entry: dict[str, Any]) -> None:
        if self.ghost is not None and self.ghost.is_active:
            # Ghost means every input is off. A recorder that kept writing
            # would make ghost mode a lie told to the orb.
            return
        with self._lock:
            entry["at"] = self._now()
            self._entries.append(entry)
            self._prune_locked()

    def record_turn(self, transcript: str, reply: str = "",
                    route: str | None = None) -> None:
        self._add({"kind": "turn", "transcript": transcript,
                   "reply": reply, "route": route})

    def record_action(self, action: str, arguments: Any = None) -> None:
        self._add({"kind": "action", "action": action,
                   "arguments": arguments})

    # ——— forgetting ———

    def _prune_locked(self) -> None:
        cutoff = self._now() - self.window_seconds
        while self._entries and self._entries[0]["at"] < cutoff:
            self._entries.popleft()

    def prune(self) -> None:
        with self._lock:
            self._prune_locked()

    def entries(self) -> list[dict[str, Any]]:
        """Everything still inside the window, oldest first."""
        with self._lock:
            self._prune_locked()
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    # ——— export ———

    def export(self, path: Path | str | None = None) -> Path:
        """Write the buffer to a JSONL file and return where it went.

        Mode 600: this is a transcript of everything you said in the last
        fifteen minutes, and it should not be world-readable on a shared
        machine because it happened to land in a temp directory.
        """
        if path is None:
            DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = DEFAULT_EXPORT_DIR / f"session-{stamp}.jsonl"

        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)

        payload = self.entries()
        # Opened with O_CREAT and an explicit mode rather than write_text, so
        # the file is never briefly world-readable between creation and chmod.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            for entry in payload:
                os.write(fd, (json.dumps(entry, default=str,
                                         ensure_ascii=False) + "\n").encode())
        finally:
            os.close(fd)
        target.chmod(0o600)

        log.info("exported %d entries to %s", len(payload), target)
        return target
