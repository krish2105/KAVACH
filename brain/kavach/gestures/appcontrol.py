"""Hand control of whatever application is in front.

The first thing in KAVACH that acts **outside** KAVACH. Until now a misread
gesture wiggled a 3D model; here it can scroll a form you are filling in or
zoom a document you are reading. That difference is the whole design.

## What it can and cannot do

Scroll and zoom, and nothing else. Zoom is sent as **⌘+scroll**, the idiom
Safari, Preview, Maps and every browser already understand, so it needs no
keystroke synthesis and no private API.

**Rotation is deliberately absent.** macOS has no public API for a rotate
gesture, and building on private `NSEvent` internals would break on any macOS
update — §A says do not. Rotation keeps working on the orb, where it is ours to
define, and does nothing here.

## Six gates, and denial is the default at each

1. Explicitly armed — off at every startup, never persisted
2. Post-event access granted
3. The frontmost app is on the allowlist
4. No confirmation pending
5. The kill switch is armed
6. Not in ghost mode

Any of them failing raises `ControlRefused` with a reason worth reading. None
of them silently no-ops, because a gesture that does nothing and says nothing
is indistinguishable from one the camera never saw.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger("kavach.gestures.appcontrol")

#: Hand movement (fraction of frame) to scroll pixels.
#:
#: A comfortable hand movement spans perhaps a third of the camera's view, and
#: should move a page by a useful amount rather than a nudge.
SCROLL_GAIN = 900.0

#: Below this, a zoom is fingertip jitter rather than intent, and streaming
#: ⌘-scroll events at an application over noise is how you zoom something by
#: accident.
ZOOM_DEADZONE = 0.02

#: Zoom factor to scroll clicks. Small: ⌘+scroll is coarse in most apps, and
#: overshooting a zoom is far more annoying than undershooting it.
ZOOM_GAIN = 12.0

#: A session ends after this long without a gesture, so the log records
#: something shaped like the interaction rather than one entry per frame.
SESSION_IDLE_SECONDS = 2.0


class ControlRefused(RuntimeError):
    """Raised, never swallowed.

    A refusal has to be visible: the HUD shows why, so "not allowed here" and
    "the camera did not see you" are never confused for one another.
    """


class QuartzPoster:
    """Real event synthesis. Replaced by a fake in tests, so the suite never
    moves your actual windows."""

    def has_access(self) -> bool:
        try:
            import Quartz

            return bool(Quartz.CGPreflightPostEventAccess())
        except Exception:
            log.debug("could not check post-event access", exc_info=True)
            return False

    def request_access(self) -> bool:
        try:
            import Quartz

            return bool(Quartz.CGRequestPostEventAccess())
        except Exception:
            return False

    def post_scroll(self, dx: float, dy: float, command: bool = False) -> None:
        import Quartz

        event = Quartz.CGEventCreateScrollWheelEvent(
            None,
            Quartz.kCGScrollEventUnitPixel,
            2,                    # two axes: vertical, then horizontal
            int(dy),
            int(dx),
        )
        if event is None:
            raise ControlRefused("macOS refused to create the scroll event.")
        if command:
            Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def frontmost_app() -> dict | None:
    """The application in front, or None if it cannot be identified.

    Needs no permission — `NSWorkspace` reports this to anyone. Returning None
    rather than guessing matters: an app that cannot be named cannot be checked
    against the allowlist, and what cannot be checked is refused.
    """
    try:
        import AppKit

        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return {"name": str(app.localizedName() or ""),
                "bundle_id": str(app.bundleIdentifier() or "")}
    except Exception:
        log.debug("could not read the frontmost application", exc_info=True)
        return None


class AppController:
    """Scrolls and zooms the frontmost app, if every gate agrees."""

    def __init__(self, allowlist, kill_switch, poster=None, frontmost=None,
                 ghost=None):
        self.allowlist = allowlist
        self.kill_switch = kill_switch
        self.poster = poster or QuartzPoster()
        self.frontmost = frontmost or frontmost_app
        self.ghost = ghost
        #: Off. Every startup. See the module docstring.
        self.enabled = False
        self.confirmation_pending = False
        self._session_app: str | None = None
        self._session_started = 0.0
        self._last_event = 0.0

    # ——— arming ———

    def enable(self) -> None:
        log.warning("hand control of other applications ARMED")
        self.enabled = True

    def disable(self) -> None:
        self.end_session()
        self.enabled = False
        log.info("hand control of other applications disarmed")

    # ——— the gate ———

    def target(self) -> dict | None:
        """The app a gesture would drive right now, or None if none would."""
        try:
            self._check()
        except ControlRefused:
            return None
        return self.frontmost()

    def why_refused(self) -> str | None:
        """The reason a gesture would be refused, for the HUD to show."""
        try:
            self._check()
        except ControlRefused as exc:
            return str(exc)
        return None

    def _check(self) -> dict:
        if not self.enabled:
            raise ControlRefused("Hand control of other apps is off.")

        if self.ghost is not None and self.ghost.is_active:
            raise ControlRefused("Ghost mode — nothing is sensing.")

        if self.confirmation_pending:
            raise ControlRefused(
                "A confirmation is waiting — hand control is suspended so a "
                "moving hand cannot answer it."
            )

        if self.kill_switch is not None and not self.kill_switch.is_armed:
            raise ControlRefused("Kill switch is latched.")

        if not self.poster.has_access():
            raise ControlRefused(
                "macOS has not granted permission to post events. "
                "System Settings → Privacy & Security → Accessibility, "
                "then enable KAVACH."
            )

        app = self.frontmost()
        if not app or not app.get("bundle_id"):
            # Same rule ToolGate follows: what cannot be identified cannot be
            # checked against the allowlist, and what cannot be checked is
            # refused rather than assumed harmless.
            raise ControlRefused("Cannot identify the frontmost application.")

        if not self.allowlist.is_allowed(app["bundle_id"]):
            raise ControlRefused(
                f"{app['name']} is not on the allowlist. "
                f"Ask before expanding it (§7)."
            )
        return app

    # ——— acting ———

    def _begin(self, app: dict) -> None:
        now = time.time()
        stale = (now - self._last_event) > SESSION_IDLE_SECONDS
        if self._session_app != app["name"] or stale:
            self.end_session()
            self._session_app = app["name"]
            self._session_started = now
            if self.kill_switch is not None:
                # Logged like a tool call, because that is what it is: KAVACH
                # acting on something that is not KAVACH.
                self.kill_switch.log.append(
                    "appcontrol.start", app=app["name"],
                    bundle_id=app["bundle_id"],
                )
        self._last_event = now

    def end_session(self) -> None:
        if self._session_app is None:
            return
        if self.kill_switch is not None:
            self.kill_switch.log.append(
                "appcontrol.end", app=self._session_app,
                seconds=round(time.time() - self._session_started, 2),
            )
        self._session_app = None

    def scroll(self, dx: float, dy: float) -> None:
        """Scroll the frontmost app by a hand movement."""
        app = self._check()
        self._begin(app)
        self.poster.post_scroll(dx * SCROLL_GAIN, dy * SCROLL_GAIN)

    def zoom(self, factor: float) -> None:
        """Zoom the frontmost app, as ⌘+scroll."""
        if abs(factor - 1.0) < ZOOM_DEADZONE:
            return
        app = self._check()
        self._begin(app)
        clicks = (factor - 1.0) * ZOOM_GAIN
        self.poster.post_scroll(0.0, clicks, command=True)
