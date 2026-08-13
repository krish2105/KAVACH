"""Ghost mode reaching the camera, which lives in another process (§14).

The camera is owned by the **presence** process, not the voice loop: the macOS
camera prompt is UI, and only an NSApplication can raise it. So `GhostMode` in
the brain stops the microphone it owns and knows nothing about the webcam.

Found in live testing rather than in the tests — `ghost.enter` logged
`stopped: ["mic"]` with no camera in the list, because there was no tracker in
that process to stop. The unit tests passed because they attached a fake
tracker in-process, which is exactly the shape of assurance that isn't one.

This closes it from the other side: the presence process already receives every
snapshot (that is how the menu bar follows state), so it reads `ghost` from
there and stops its own camera. Extracted into a class rather than left as a
closure in `__main__` so the rules below can be tested without a webcam.
"""

from __future__ import annotations

import logging

log = logging.getLogger("kavach.privacy.camera_gate")


class CameraGate:
    """Starts and stops hand tracking to follow ghost mode.

    `make_tracker` is a factory, not an instance, because `HandTracker` is a
    `threading.Thread` — a stopped thread cannot be restarted, so leaving ghost
    mode has to build a new one.
    """

    def __init__(self, make_tracker=None):
        self.make_tracker = make_tracker
        self.tracker = None
        #: Whether *we* stopped it. The gate may only restart a camera it
        #: turned off — if gestures were disabled for any other reason
        #: (no permission, --no-gestures, a crash), leaving ghost mode must
        #: not quietly switch the webcam on.
        self._stopped_by_ghost = False

    def start(self) -> None:
        if self.make_tracker is None:
            return
        self.tracker = self.make_tracker()

    @property
    def running(self) -> bool:
        return self.tracker is not None

    def apply(self, ghost: bool) -> bool:
        """Follow a snapshot's ghost flag. True if anything changed."""
        if ghost and self.tracker is not None:
            log.warning("ghost mode — stopping the camera")
            try:
                self.tracker.stop()
            except Exception:
                log.exception("could not stop the tracker")
            self.tracker = None
            self._stopped_by_ghost = True
            return True

        if not ghost and self.tracker is None and self._stopped_by_ghost:
            if self.make_tracker is None:
                return False
            log.warning("ghost mode over — restarting the camera")
            try:
                self.tracker = self.make_tracker()
                self._stopped_by_ghost = False
                return True
            except Exception:
                log.exception("could not restart the tracker")
                return False

        return False
