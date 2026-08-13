"""One wake-word listener at a time (Phase 18).

> A simple heartbeat/lock-file guard so only one instance runs the wake-word
> listener if KAVACH ever runs on a second Mac. Don't overbuild this into a
> distributed system.

Taken literally. This is a lock file with a heartbeat: no election, no
consensus, no network, no second machine talking to the first. Two processes
both listening for "KAVACH" means two of them fighting over the microphone and
both answering — the guard exists to stop that and nothing more.

It also fixes something that already happened here: **25 overlay instances ran
simultaneously** earlier in this project, burning CPU, and because a `pgrep`
pattern didn't match the process name the symptom was misread as a crash for
about an hour.

Two failure modes it has to survive, and they need different mechanisms:

* **A crash** leaves a lock file whose PID no longer exists. Checked with
  `os.kill(pid, 0)`.
* **A hang** leaves a PID that exists and does nothing. Only a heartbeat
  catches that, which is why there is one.

Everything else — including a torn or empty file from a power cut — resolves to
"take the lock", because a KAVACH that refuses to listen until a human finds
and deletes a file they don't know exists is worse than one that occasionally
takes over a lock too eagerly.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import threading
import time
from pathlib import Path

log = logging.getLogger("kavach.single")

DEFAULT_LOCK_PATH = Path.home() / ".kavach" / "wake.lock"

#: How old a heartbeat may get before the holder is presumed dead.
#:
#: Comfortably longer than the beat interval below: a listener briefly busy
#: with a turn must not have its lock stolen mid-sentence.
DEFAULT_STALE_AFTER = 60.0

#: How often the holder refreshes it.
BEAT_SECONDS = 15.0


def _process_alive(pid: int) -> bool:
    """Whether a PID exists. Signal 0 checks without sending anything."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, owned by someone else. Alive as far as we are concerned.
        return True
    except Exception:
        return False
    return True


class WakeWordLock:
    """Advisory lock over the wake-word listener.

    Advisory on purpose: it gates whether KAVACH *starts* the listener, and
    does not try to prevent a determined process from opening the microphone.
    The goal is to stop accidental duplicates, which is the thing that actually
    happens.
    """

    def __init__(self, path: Path | str | None = None,
                 stale_after: float = DEFAULT_STALE_AFTER):
        self.path = Path(path) if path is not None else DEFAULT_LOCK_PATH
        self.stale_after = float(stale_after)
        self.held = False
        #: Identity is per lock OBJECT, not per process.
        #:
        #: Keying on the PID alone looked right and was wrong: a second
        #: WakeWordLock inside one process would see its own PID in the file
        #: and conclude the lock was already its own — silently permitting the
        #: exact double-listener this class exists to prevent. The PID is still
        #: recorded, because "which process holds it" is what you want when
        #: reading the file by hand.
        self._token = secrets.token_hex(8)
        self._lock = threading.Lock()
        self._beat_thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ——— reading ———

    def _read(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text())
        except Exception:
            # Missing, empty, or torn. All mean "no usable claim".
            return None
        return data if isinstance(data, dict) else None

    def _claim_is_live(self, data: dict) -> bool:
        pid = int(data.get("pid", 0) or 0)
        host = str(data.get("host", ""))
        heartbeat = float(data.get("heartbeat", 0) or 0)

        if host and host != socket.gethostname():
            # Another machine. We cannot check its PID, so the heartbeat is the
            # only evidence available — which is exactly why it exists.
            return (time.time() - heartbeat) < self.stale_after

        if not _process_alive(pid):
            log.info("wake lock held by dead pid %s — taking over", pid)
            return False
        if (time.time() - heartbeat) >= self.stale_after:
            log.info("wake lock heartbeat is %.0fs stale — taking over",
                     time.time() - heartbeat)
            return False
        return True

    def describe_holder(self) -> str:
        """A message worth printing when an instance is turned away."""
        data = self._read()
        if data is None:
            return "nobody"
        age = time.time() - float(data.get("heartbeat", 0) or 0)
        return (f"pid {data.get('pid')} on {data.get('host')} "
                f"(heartbeat {age:.0f}s ago)")

    # ——— taking ———

    def acquire(self) -> bool:
        """Try to become the listener. True if this process may listen."""
        with self._lock:
            if self.held:
                # Re-acquiring our own lock is fine: restarting the listener
                # inside one process must not deadlock it.
                self._write()
                return True

            data = self._read()
            if data is not None and self._claim_is_live(data):
                if data.get("owner") == self._token:
                    # Our own claim, from a previous acquire() on this object.
                    self.held = True
                    self._write()
                    return True
                return False

            self._write()
            self.held = True

        log.info("wake-word listener lock acquired (%s)", self.path)
        self._start_beating()
        return True

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "owner": self._token,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "heartbeat": time.time(),
            "started": time.time(),
        }
        # Written whole to a temp file then moved: a reader must never catch a
        # half-written claim, which would look like a corrupt lock and get
        # taken over while the holder was perfectly healthy.
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(self.path)

    # ——— keeping ———

    def beat(self) -> None:
        """Refresh the heartbeat. Does nothing unless this process holds it."""
        if not self.held:
            return
        data = self._read()
        if data is not None and data.get("owner") != self._token:
            # Someone else took over while we were busy. Stop claiming it.
            log.warning("wake lock was taken over by pid %s", data.get("pid"))
            self.held = False
            return
        try:
            existing = data or {}
            payload = {
                "owner": self._token,
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "heartbeat": time.time(),
                "started": existing.get("started", time.time()),
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(self.path)
        except Exception:
            log.debug("could not refresh the wake lock", exc_info=True)

    def _start_beating(self) -> None:
        if self._beat_thread is not None:
            return

        def run() -> None:
            while not self._stop.wait(BEAT_SECONDS):
                self.beat()

        self._beat_thread = threading.Thread(target=run, name="kavach-wakelock",
                                             daemon=True)
        self._beat_thread.start()

    # ——— giving up ———

    def release(self) -> None:
        """Release the lock, if this process holds it."""
        self._stop.set()
        with self._lock:
            if not self.held:
                return
            data = self._read()
            if data is None or data.get("owner") == self._token:
                try:
                    self.path.unlink(missing_ok=True)
                except Exception:
                    log.debug("could not remove the wake lock", exc_info=True)
            self.held = False
        log.info("wake-word listener lock released")
