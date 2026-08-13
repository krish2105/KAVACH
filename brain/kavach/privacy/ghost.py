"""Ghost mode (Phase 14) — stop sensing, visibly.

> A command/shortcut, separate from the existing kill switch, that fully
> suspends mic input, camera/hand-tracking input, and action logging. The orb
> must visibly show ghost mode is active — never ambiguous whether KAVACH is
> listening.

Ghost is deliberately **not** a second kill switch, and the difference is worth
stating because the two are easy to conflate:

|            | Kill switch          | Ghost mode        |
|------------|----------------------|-------------------|
| Stops      | *acting*             | *sensing*         |
| Recovery   | latched, deliberate  | resumable         |

Turning your own microphone back on is routine, so ghost resumes. Resuming
action after an emergency stop is not routine, so the kill switch latches.

Three properties this module is built around:

* **Ghost cannot hide its own edges.** The action log is suspended, but
  `ghost.enter` and `ghost.leave` are on `ActionLog.ALWAYS_LOGGED`, so the gap
  always has a visible beginning and end. A gap you cannot see the edges of is
  indistinguishable from a gap somebody made on purpose.
* **Ghost is not a bypass.** It has no opinion on whether KAVACH may *act*, and
  leaving ghost must never quietly undo a kill switch that latched meanwhile.
* **Both senses, or it is a lie.** Mic and camera stop together. A ghost mode
  that leaves the camera running is worse than none, because you would trust it.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("kavach.privacy.ghost")


class GhostMode:
    """Suspends every input KAVACH has, reversibly.

    Takes its dependencies via `attach()` rather than importing them, because
    the loop wires the mic and the tracker at different points in startup and
    ghost has to work whether or not the camera was ever started.
    """

    def __init__(self, log=None, kill_switch=None, publish=None):
        #: The ActionLog. Named `log` to match every other call site in the
        #: codebase; the module logger is `logger` here to avoid shadowing it.
        self.action_log = log
        self.kill_switch = kill_switch
        self.publish = publish
        self._active = False
        self._entered_at = 0.0
        self._lock = threading.Lock()
        self._mic = None
        self._tracker = None

    def attach(self, mic=None, tracker=None) -> None:
        """Register what to silence. Safe to call repeatedly as things start."""
        if mic is not None:
            self._mic = mic
        if tracker is not None:
            self._tracker = tracker

    @property
    def is_active(self) -> bool:
        return self._active

    # ——— entering ———

    def enter(self, source: str = "unknown") -> bool:
        """Stop sensing. Returns True if this call changed anything.

        Order matters: inputs are stopped **before** the log is suspended, so
        anything the stop itself emits is still recorded. Suspending first
        would hide the shutdown of the very sensors this is about.
        """
        with self._lock:
            if self._active:
                return False
            self._active = True
            self._entered_at = time.time()

        stopped = []
        for name, device in (("mic", self._mic), ("camera", self._tracker)):
            if device is None:
                continue
            try:
                device.stop()
                stopped.append(name)
            except Exception:
                # Reported, never swallowed: if the camera would not stop, you
                # need to know that ghost mode is not what it claims to be.
                logger.exception("ghost: could not stop %s", name)

        if self.action_log is not None:
            self.action_log.append("ghost.enter", source=source,
                                   stopped=stopped)
            self.action_log.suspend()

        logger.warning("ghost mode ON (%s) — stopped: %s",
                    source, ", ".join(stopped) or "nothing attached")
        self._publish()
        return True

    # ——— leaving ———

    def leave(self, source: str = "unknown") -> bool:
        """Resume sensing. Returns True if this call changed anything."""
        with self._lock:
            if not self._active:
                return False
            self._active = False
            seconds = round(time.time() - self._entered_at, 2)

        if self.action_log is not None:
            # Resume first, then log: `ghost.leave` is on ALWAYS_LOGGED so it
            # would survive either way, but resuming first means a later change
            # to that list cannot silently cost us the exit record.
            self.action_log.resume()
            self.action_log.append("ghost.leave", source=source,
                                   seconds=seconds, mic_resumed=self._may_listen())

        if self._may_listen():
            if self._mic is not None:
                try:
                    self._mic.start()
                except Exception:
                    logger.exception("ghost: could not restart the mic")
        else:
            # The switch latched while we were blind. Leaving ghost restores
            # what ghost took away and nothing else — it is not a back door
            # into re-arming, and an ambiguous state stays stopped (§7).
            logger.warning("ghost mode OFF (%s) — mic stays down, "
                        "kill switch is latched", source)

        logger.warning("ghost mode OFF (%s) after %ss", source, seconds)
        self._publish()
        return True

    def toggle(self, source: str = "unknown") -> bool:
        """Flip. Returns the new state."""
        if self._active:
            self.leave(source=source)
        else:
            self.enter(source=source)
        return self._active

    # ——— helpers ———

    def _may_listen(self) -> bool:
        """Whether anything is allowed to listen right now.

        A latched kill switch outranks ghost mode in both directions: it can be
        triggered while blind, and it keeps the mic down after ghost ends.
        """
        if self.kill_switch is None:
            return True
        return bool(self.kill_switch.is_armed)

    def _publish(self) -> None:
        if self.publish is not None:
            try:
                self.publish()
            except Exception:
                logger.debug("ghost: could not publish state", exc_info=True)
