"""The kill switch (spec §7, working agreement §C).

One keyboard shortcut that immediately halts any in-flight action, no matter
what layer it's in. This is built and proven before any device-control code
exists, because an agent holding Accessibility + Automation + Screen Recording
can click anything on the machine.

Semantics, decided deliberately:

* **Halt, then latch disarmed.** Triggering cancels in-flight work and kills
  registered subprocesses, then latches into ``DISARMED``. Nothing re-arms it
  automatically. A runaway loop cannot re-trigger itself past the latch, and an
  ambiguous state stays stopped.
* **Guard is the gate.** Every action path calls :meth:`KillSwitch.guard`
  before doing anything. Phase 4 wires it into MCP dispatch.
* **Trigger is safe from any thread.** The hotkey and menubar surfaces fire on
  the AppKit main thread while the agent's work runs on an asyncio loop, so
  cancellation is marshalled back onto the owning loop.
* **Fail toward stopped.** Every failure mode in ``trigger`` is caught and
  recorded rather than raised. A kill switch that throws halfway through is a
  kill switch that didn't kill.
* **The latch outlives the process.** ``trigger`` writes the state to disk and
  ``__init__`` reads it back, so a CLI launched after a kill starts disarmed
  too. Until this existed the latch lived only in the daemon's memory: a
  freshly constructed switch reported ``ARMED`` seconds after a kill, and
  ``kavach-memory index`` read the disk while the system was supposedly
  halted. A gate that answers yes in every process but one is the shape of a
  gate, not a gate.

  The state file sits **beside the action log** rather than at a fixed path,
  so a test pointed at a ``tmp_path`` log is isolated by construction.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import threading
from enum import Enum
from pathlib import Path
from typing import Any

from .log import ActionLog

log = logging.getLogger("kavach.killswitch")

#: Name of the latch file, kept next to `actions.jsonl`.
STATE_FILENAME = "killswitch.state"


class State(str, Enum):
    ARMED = "armed"
    DISARMED = "disarmed"


class KillSwitchDisarmed(RuntimeError):
    """Raised by :meth:`KillSwitch.guard` when an action is attempted while
    the switch is latched. Callers must not catch-and-continue."""


class KillSwitch:
    def __init__(self, log: ActionLog | None = None) -> None:
        self.log = log if log is not None else ActionLog()
        self._state_path = Path(self.log.path).parent / STATE_FILENAME
        self._lock = threading.RLock()
        self._tasks: set[asyncio.Task] = set()
        self._processes: list[subprocess.Popen] = []
        self._last_trigger: dict[str, Any] | None = None
        self._state = self._read_state()

    # --- persistence ------------------------------------------------------
    #
    # Deliberately a plain file rather than the socket the CLI already uses:
    # `kavach status` asks a *running daemon*, and the case that matters is a
    # process starting up when the daemon may not be there at all.

    def _read_state(self) -> State:
        """The state a newly started process should begin in.

        Three cases, and the difference between the last two is the whole
        point:

        * **no file** — nothing has ever fired. ARMED. A fresh install must
          not ship dead.
        * **a file saying disarmed** — someone hit the switch. DISARMED.
        * **a file we cannot parse** — truncated, hand-edited, half-written.
          DISARMED, because §C says an ambiguous state stays stopped, and
          guessing "armed" is the expensive guess.
        """
        try:
            raw = self._state_path.read_text()
        except FileNotFoundError:
            return State.ARMED
        except OSError as exc:
            log.warning("kill switch state unreadable (%s) — staying stopped", exc)
            return State.DISARMED

        try:
            return State(json.loads(raw)["state"])
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("kill switch state unparseable (%s) — staying stopped", exc)
            return State.DISARMED

    def _write_state(self, state: State, **detail: Any) -> None:
        """Record the state for other processes.

        **Never raises.** If the disk is full or the directory is gone, the
        in-memory latch still holds — losing the ability to *record* a stop
        must not become a failure to *stop*. It is logged loudly, because the
        consequence is that a CLI started afterwards would not know.
        """
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"state": state.value, **detail}
            tmp = self._state_path.with_suffix(".state.tmp")
            tmp.write_text(json.dumps(payload))
            # Atomic, so a reader never sees a half-written file — which
            # would land in the unparseable branch above and stop a machine
            # that was only ever being re-armed.
            tmp.replace(self._state_path)
        except OSError as exc:
            log.warning("could not persist kill switch state %s: %s",
                        state.value, exc)

    # --- state ------------------------------------------------------------

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    @property
    def is_armed(self) -> bool:
        return self.state is State.ARMED

    def status(self) -> dict[str, Any]:
        with self._lock:
            live_tasks = [t for t in self._tasks if not t.done()]
            live_procs = [p for p in self._processes if p.poll() is None]
            return {
                "state": self._state.value,
                "armed": self._state is State.ARMED,
                "in_flight_tasks": len(live_tasks),
                "live_processes": [p.pid for p in live_procs],
                "last_trigger": self._last_trigger,
                "log_path": str(self.log.path),
            }

    # --- registration -----------------------------------------------------

    def register_task(self, task: asyncio.Task) -> asyncio.Task:
        """Register in-flight async work to be cancelled on trigger."""
        with self._lock:
            self._tasks.add(task)
        # Drop the reference once it finishes so long sessions don't leak.
        task.add_done_callback(self._forget_task)
        return task

    def _forget_task(self, task: asyncio.Task) -> None:
        with self._lock:
            self._tasks.discard(task)

    def register_process(self, proc: subprocess.Popen) -> subprocess.Popen:
        """Register a subprocess (an MCP server, a helper) to be killed on
        trigger."""
        with self._lock:
            self._processes.append(proc)
        return proc

    # --- the gate ---------------------------------------------------------

    def guard(self, action: str = "<unnamed action>") -> None:
        """Raise :class:`KillSwitchDisarmed` unless the switch is armed.

        Every action path calls this before touching anything. Blocked attempts
        are logged: after a kill, what the agent *tried* to do next is exactly
        what you want to be able to read back.
        """
        with self._lock:
            if self._state is State.ARMED:
                return

        self.log.append("killswitch.blocked", action=action, state=self._state.value)
        raise KillSwitchDisarmed(
            f"Kill switch is latched DISARMED; refusing to run {action!r}. "
            f"Re-arm explicitly with `kavach rearm` once you know why it fired."
        )

    # --- trigger ----------------------------------------------------------

    def trigger(self, source: str, reason: str = "",
                **extra: Any) -> dict[str, Any]:
        """Halt everything in flight and latch disarmed. Safe to call from any
        thread, and safe to call repeatedly.

        Returns the audit record that was written.
        """
        with self._lock:
            # Latch FIRST. If cancellation below is slow or partially fails,
            # the gate is already shut and no new action can start.
            self._state = State.DISARMED
            tasks = list(self._tasks)
            processes = list(self._processes)

        # And persist it before doing the slow work, for the same reason: a
        # process launched during cancellation must find the gate already
        # shut. `_write_state` never raises.
        self._write_state(State.DISARMED, source=source, reason=reason)

        cancelled = self._cancel_tasks(tasks)
        killed, kill_errors = self._kill_processes(processes)

        record = self.log.append(
            "killswitch.trigger",
            source=source,
            reason=reason,
            cancelled_tasks=cancelled,
            killed_processes=killed,
            errors=kill_errors,
            # Passed through to the audit record, never interpreted. The switch
            # does not care who asked and must not start caring — everything
            # that reaches here has already been authorised, and a latch that
            # weighed up its caller would be a latch with a bypass.
            **extra,
        )
        with self._lock:
            self._last_trigger = record
        return record

    def _cancel_tasks(self, tasks: list[asyncio.Task]) -> int:
        cancelled = 0
        for task in tasks:
            if task.done():
                continue
            try:
                loop = task.get_loop()
            except Exception:
                continue

            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None

            try:
                if running is loop:
                    task.cancel()
                else:
                    # Triggered off-loop (hotkey/menubar on the AppKit main
                    # thread). Task.cancel is not thread-safe; marshal it.
                    loop.call_soon_threadsafe(task.cancel)
                cancelled += 1
            except RuntimeError:
                # Loop already closed — the task cannot still be running.
                continue
        return cancelled

    def _kill_processes(
        self, processes: list[subprocess.Popen]
    ) -> tuple[list[int], list[str]]:
        killed: list[int] = []
        errors: list[str] = []

        for proc in processes:
            if proc.poll() is not None:
                continue  # already dead; nothing to do and not an error
            try:
                self._kill_one(proc)
                killed.append(proc.pid)
            except Exception as exc:  # never let one bad child abort the kill
                errors.append(f"pid {proc.pid}: {exc!r}")

        return killed, errors

    def _kill_one(self, proc: subprocess.Popen) -> None:
        """SIGKILL a child, preferring its whole process group.

        MCP servers launched via ``npx``/``uvx`` spawn grandchildren, and
        killing only the direct child leaves those running with the very
        permissions we are trying to revoke.

        The pgid check is load-bearing: if the child was NOT started with
        ``start_new_session=True`` its group is our own, and killpg would
        SIGKILL this process too.
        """
        try:
            child_pgid = os.getpgid(proc.pid)
            own_pgid = os.getpgid(0)
            if child_pgid != own_pgid:
                os.killpg(child_pgid, signal.SIGKILL)
                return
        except (ProcessLookupError, PermissionError, OSError):
            pass  # fall through to the direct kill

        proc.kill()

    # --- rearm ------------------------------------------------------------

    def rearm(self, source: str, reason: str = "") -> dict[str, Any]:
        """Explicitly re-arm. The only way out of the latch, by design."""
        with self._lock:
            # Clear stale registrations: whatever was in flight is dead now,
            # and carrying dead handles forward would make status() lie.
            self._tasks = {t for t in self._tasks if not t.done()}
            self._processes = [p for p in self._processes if p.poll() is None]
            self._state = State.ARMED

        self._write_state(State.ARMED, source=source, reason=reason)
        return self.log.append("killswitch.rearm", source=source, reason=reason)
