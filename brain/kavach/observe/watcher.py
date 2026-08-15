"""Tail the newest Claude Code session and narrate what changes.

`claudecode.py` can read a transcript. This is what makes that reachable —
without it Phase 31 would be another module that works and is called by
nothing, which is the defect this project found three times in one day
(`browser.py`, the file tools, the duplicated endpointer).

**Starts at the end of the file, never the beginning.** A watcher that
replayed history would announce every test run since the session began the
moment it started, which is both wrong and alarming.

**Speaks only what changed.** The same suite passing twice is one piece of
news, not two — repeating it trains the user to tune KAVACH out, which is the
same failure as confirming everything.

Tier AUTO (Phase 30): this observes and never acts, so there is nothing to
approve. It is also the only tier a `watch`-type action could honestly have —
asking permission to notice something is theatre.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .claudecode import Observation, describe, observe_line, session_files

log = logging.getLogger("kavach.observe.watcher")

#: How often to look for new lines. Slow enough to cost nothing, fast enough
#: that "your tests are failing" arrives while the user still cares.
POLL_SECONDS = 2.0


@dataclass
class Narration:
    text: str
    observation: Observation


class SessionWatcher:
    """Follows the newest session transcript and yields narrations.

    Read-only throughout: it opens transcripts in text-read mode and holds an
    offset. Nothing here writes to, renames or deletes a Claude Code file.
    """

    def __init__(self, root: Path | None = None):
        self.root = root
        self._path: Path | None = None
        self._offset = 0
        self._last: str | None = None

    def _newest(self) -> Path | None:
        files = session_files(self.root) if self.root else session_files()
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_mtime)

    def poll(self) -> list[Narration]:
        """Read whatever has been appended since the last call."""
        newest = self._newest()
        if newest is None:
            return []

        if newest != self._path:
            # A new session started. Begin at its end — replaying a
            # transcript would announce hours of history at once.
            self._path = newest
            try:
                self._offset = newest.stat().st_size
            except OSError:
                self._offset = 0
            return []

        try:
            size = newest.stat().st_size
        except OSError:
            return []
        if size < self._offset:
            # Truncated or rotated. Start from the end again rather than
            # re-reading a file that is no longer the one we had.
            self._offset = size
            return []
        if size == self._offset:
            return []

        out: list[Narration] = []
        try:
            with newest.open("r", errors="replace") as handle:
                handle.seek(self._offset)
                for raw in handle:
                    observed = observe_line(raw)
                    if observed is None:
                        continue
                    spoken = describe(observed)
                    if spoken is None or spoken == self._last:
                        # Same news twice is one piece of news. Repeating it
                        # is how a notification becomes background noise.
                        continue
                    self._last = spoken
                    out.append(Narration(spoken, observed))
                self._offset = handle.tell()
        except OSError:
            log.debug("could not read %s", newest, exc_info=True)
        return out

    def follow(self, on_narration, stop=None, poll: float = POLL_SECONDS):
        """Block, polling, calling `on_narration(text, observation)`.

        Every callback is wrapped: a failure in the speaking path must not
        end the watch, the same rule the wake-word scorer follows.
        """
        while stop is None or not stop.is_set():
            for item in self.poll():
                try:
                    on_narration(item.text, item.observation)
                except Exception:
                    log.debug("narration failed", exc_info=True)
            time.sleep(poll)


__all__ = ["SessionWatcher", "Narration", "POLL_SECONDS"]
