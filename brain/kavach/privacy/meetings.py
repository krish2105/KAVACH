"""Meeting-aware muting (Phase 15).

> Detect an active call (Zoom/Meet/FaceTime) and auto-suspend the wake word for
> the duration, resuming after.

**Why this reads window titles.** The obvious approach is CoreAudio's
`kAudioDevicePropertyDeviceIsRunningSomewhere` — "is anything using the mic?".
Verified against current sources before writing this, it does not work here for
two independent reasons: KAVACH holds the microphone itself, so the property
reads true permanently; and Bluetooth microphones report inactive regardless of
use. There is no public macOS API for "am I in a call", so detection is
necessarily app-specific and necessarily heuristic.

Window titles need Screen Recording permission, which this machine has granted
(checked: 7 of 8 windows returned readable names).

**The rule that matters** is not detection, it is resumption. This module
suspends the wake word, so it may only ever un-suspend what *it* suspended.
Ghost mode and the kill switch outrank it in both directions: neither a call
ending nor anything else here may turn a microphone back on that a human, or an
emergency stop, turned off.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

log = logging.getLogger("kavach.privacy.meetings")

#: How often the desktop is inspected. Slow on purpose — a call is minutes
#: long, and this runs forever in the background.
POLL_SECONDS = 5.0


@dataclass(frozen=True)
class Detection:
    app: str
    #: "high" for a window an app opens *only* while in a call; "low" for a
    #: guess made from a browser tab title. Surfaced rather than hidden,
    #: because a low-confidence match muting KAVACH mid-sentence is the kind
    #: of thing you need to be able to explain.
    confidence: str
    title: str = ""


#: Signals that an app is *in a call*, not merely running.
#:
#: The distinction is the whole feature: Zoom sits open all day, and muting on
#: app-launch would make this useless. `Zoom Meeting` is the window Zoom opens
#: only once a meeting starts.
_HIGH_CONFIDENCE = (
    ("zoom.us", ("zoom meeting", "zoom webinar"), "Zoom"),
    ("facetime", ("facetime",), "FaceTime"),
    ("microsoft teams", ("meeting in progress", "| microsoft teams call",
                         "meeting with"), "Teams"),
    ("webex", ("webex meeting",), "Webex"),
)

_BROWSERS = ("google chrome", "safari", "arc", "firefox", "microsoft edge",
             "brave browser")

#: A Meet call's tab title is the meeting code, e.g. `Meet — abc-defg-hij`.
#: Matching that shape rather than the bare word avoids muting on a page that
#: merely says "meet".
_MEET_MARKERS = ("meet.google.com", "meet — ", "meet - ", "| google meet")


def detect_call(windows: list[dict]) -> Detection | None:
    """The first call-like window, or None.

    Pure: takes the window list rather than fetching it, so the heuristics can
    be tested against fixtures instead of against whatever happens to be on
    screen when the suite runs.
    """
    for entry in windows:
        owner = str(entry.get("kCGWindowOwnerName", "")).strip().lower()
        title = str(entry.get("kCGWindowName", "")).strip()
        low = title.lower()
        if not owner:
            continue

        for app_key, markers, label in _HIGH_CONFIDENCE:
            if app_key in owner and any(m in low for m in markers):
                return Detection(app=label, confidence="high", title=title)

        if any(b in owner for b in _BROWSERS):
            if any(m in low for m in _MEET_MARKERS):
                # Low, and honestly so: this is a tab title. A page called
                # "Meet — the team" would land here too.
                return Detection(app="Google Meet", confidence="low",
                                 title=title)
    return None


def visible_windows() -> list[dict]:
    """Every on-screen window. Empty list if Quartz is unavailable."""
    try:
        import Quartz

        return list(Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
        ) or [])
    except Exception:
        log.debug("could not read the window list", exc_info=True)
        return []


class MeetingWatcher:
    """Suspends the wake word while you are on a call.

    Tracks whether *it* was the one that suspended, which is the only thing
    standing between this and a background poller that silently re-enables a
    microphone somebody deliberately turned off.
    """

    def __init__(self, loop, ghost=None, kill_switch=None, log_=None):
        self.loop = loop
        self.ghost = ghost
        self.kill_switch = kill_switch
        self.action_log = log_
        self._suspended_by_us = False
        self._current: Detection | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def in_call(self) -> Detection | None:
        return self._current

    def evaluate(self, windows: list[dict]) -> bool:
        """Apply one observation. Returns True if anything changed.

        Returning "did this change something" keeps a 5-second poll from
        re-suspending, re-logging and thrashing the wake word for the whole
        length of a call.
        """
        found = detect_call(windows)

        if found is not None and self._current is None:
            self._current = found
            if not self.loop.wake_suspended:
                self.loop.wake_suspended = True
                self._suspended_by_us = True
                self._record("meeting.start", found)
                log.info("call detected (%s, %s confidence) — wake word off",
                         found.app, found.confidence)
                return True
            # Already off for some other reason. Note the call, take no credit
            # for the suspension, and therefore never undo it.
            self._record("meeting.start", found)
            return True

        if found is None and self._current is not None:
            ended, self._current = self._current, None
            if self._suspended_by_us and self._may_resume():
                self.loop.wake_suspended = False
                self._suspended_by_us = False
                self._record("meeting.end", ended)
                log.info("call ended (%s) — wake word back on", ended.app)
            else:
                self._record("meeting.end", ended)
                log.info("call ended (%s) — wake word stays off", ended.app)
            return True

        return False

    def _may_resume(self) -> bool:
        """Whether it is safe to turn listening back on.

        Ghost mode and a latched kill switch both mean "a human decided nothing
        should be listening". A background timer noticing your Zoom window
        closed is not grounds to overrule either.
        """
        if self.ghost is not None and self.ghost.is_active:
            return False
        if self.kill_switch is not None and not self.kill_switch.is_armed:
            return False
        return True

    def _record(self, event: str, detection: Detection) -> None:
        if self.action_log is None:
            return
        try:
            # No window title: it can contain a meeting name, participant names
            # or a customer's company. Knowing a call happened is the point;
            # recording what it was called is not.
            self.action_log.append(event, app=detection.app,
                                   confidence=detection.confidence)
        except Exception:
            log.debug("could not record %s", event, exc_info=True)

    # ——— background polling ———

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="kavach-meetings",
                                        daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.evaluate(visible_windows())
            except Exception:
                log.debug("meeting poll failed; continuing", exc_info=True)
            self._stop.wait(POLL_SECONDS)

    def stop(self) -> None:
        self._stop.set()
