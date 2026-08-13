"""The orb as a desktop presence, not a browser tab.

Spec §2 calls the Presence layer "what you see and gesture at". Living in a
Safari tab you have to go and find is not presence — you should be able to
speak and have it come to you.

So the orb is hosted in a borderless, transparent, always-on-top panel that
sits above every window and follows you across Spaces. It stays out of the
way while idle and surfaces itself the moment KAVACH starts listening.

Three properties make it a presence rather than a window:

* **Non-activating.** It never steals focus, so it cannot interrupt what you
  are typing into. Clicking it does not switch you out of your app.
* **Click-through while idle.** Mouse events pass straight to whatever is
  underneath, so a floating orb never blocks a button you were aiming for.
* **Follows you.** `canJoinAllSpaces` means it is on whichever desktop you
  are on, including over full-screen apps.

State comes from the same bridge the browser HUD uses, so this is another
`KavachSource` consumer rather than a second source of truth.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import AppKit
import Foundation

import WebKit

from .controls import Geometry

log = logging.getLogger("kavach.presence.overlay")

#: States where the orb should be visible. Idle is deliberately absent — a
#: presence that is always on screen is just clutter.
ACTIVE_STATES = {"listening", "thinking", "acting", "speaking", "halted"}

#: How long to linger after returning to idle, so the orb does not vanish
#: mid-sentence the instant a turn ends.
LINGER_SECONDS = 2.5

#: Move/resize returns to click-through after this long untouched.
INTERACTIVE_TIMEOUT = 120.0

PANEL_SIZE = 400.0
MARGIN = 28.0


class OverlayWindow:
    """A floating, transparent, non-activating panel hosting the orb."""

    def set_size(self, size: float) -> None:
        """Resize about the panel's centre, so it does not walk across the
        screen as you step through sizes."""
        self.geometry.size = size
        self.geometry.clamp()
        frame = self.panel.frame()
        centre_x = frame.origin.x + frame.size.width / 2
        centre_y = frame.origin.y + frame.size.height / 2
        new = self.geometry.size
        self.panel.setFrame_display_animate_(
            Foundation.NSMakeRect(centre_x - new / 2, centre_y - new / 2, new, new),
            True, True,
        )
        self.web.setFrame_(Foundation.NSMakeRect(0, 0, new, new))
        self._remember()

    def set_interactive(self, interactive: bool) -> None:
        """Trade click-through for direct manipulation, temporarily.

        Off (the default) the panel ignores the mouse entirely, so it can never
        block a click. On, it can be dragged and resized like a window — which
        necessarily means it also intercepts clicks that land on it.
        """
        self.interactive = interactive
        self._interactive_since = time.monotonic()
        self.geometry.interactive = interactive
        self.geometry.save()
        # Mouse events stay on regardless — the panel is clickable at all
        # times now. Interactive mode only governs dragging and resizing.
        self.panel.setMovableByWindowBackground_(interactive)

        # The web view eats every mouse event, so the window itself never sees
        # a drag. A transparent view above it forwards one.
        # The drag layer stays installed either way; it only claims the mouse
        # while ⌘ is held. Interactive mode now governs resizing alone.
        # Changing the style mask on a borderless window makes AppKit
        # recompute the frame, which silently moved and shrank the panel every
        # time move/resize was toggled — the size kept "reverting" to a value
        # nobody chose. Capture and restore it around the change.
        before = self.panel.frame()
        self.panel.setStyleMask_(
            (AppKit.NSWindowStyleMaskBorderless
             | AppKit.NSWindowStyleMaskNonactivatingPanel
             | AppKit.NSWindowStyleMaskResizable)
            if interactive else
            (AppKit.NSWindowStyleMaskBorderless
             | AppKit.NSWindowStyleMaskNonactivatingPanel)
        )
        self.panel.setFrame_display_(before, True)
        if interactive:
            # Visible while you are positioning it, regardless of agent state.
            self.show()
        log.info("interactive mode %s", "on" if interactive else "off")

    def set_always(self, always: bool) -> None:
        """Pin the panel on screen regardless of what the agent is doing."""
        self.geometry.always = always
        self.geometry.hidden = False if always else self.geometry.hidden
        self.geometry.save()
        if always:
            self.show()
        log.info("always-show %s", "on" if always else "off")

    def toggle_fullscreen(self) -> None:
        """Fill the display, or go back to the floating panel.

        Not NSWindow's native full-screen: that moves the window to its own
        Space, which would take the orb *away* from your work — the opposite of
        a presence. This simply resizes to the screen and remembers where it
        came from, so it still floats above everything you were doing.
        """
        if self._fullscreen_restore is not None:
            self.panel.setFrame_display_animate_(self._fullscreen_restore, True, True)
            self._fullscreen_restore = None
            self.web.setFrame_(
                Foundation.NSMakeRect(0, 0, self.geometry.size, self.geometry.size)
            )
            log.info("full screen off")
            return

        self._fullscreen_restore = self.panel.frame()
        frame = AppKit.NSScreen.mainScreen().frame()
        self.panel.setFrame_display_animate_(frame, True, True)
        self.web.setFrame_(
            Foundation.NSMakeRect(0, 0, frame.size.width, frame.size.height)
        )
        self.show()
        log.info("full screen on (%.0fx%.0f)", frame.size.width, frame.size.height)

    @property
    def is_fullscreen(self) -> bool:
        return self._fullscreen_restore is not None

    def set_pinned_hidden(self, hidden: bool) -> None:
        """Minimise: stay out of the way even when KAVACH is listening."""
        self.geometry.hidden = hidden
        self._remember()
        if hidden:
            self.hide()

    def reset_position(self) -> None:
        self.geometry.x = None
        self.geometry.y = None
        self.panel.setFrame_display_animate_(self._default_rect(), True, True)
        self._remember()

    def _default_rect(self):
        visible = AppKit.NSScreen.mainScreen().visibleFrame()
        size = self.geometry.size
        return Foundation.NSMakeRect(
            visible.origin.x + visible.size.width - size - MARGIN,
            visible.origin.y + MARGIN,
            size, size,
        )

    def _remember(self) -> None:
        frame = self.panel.frame()
        self.geometry.x = float(frame.origin.x)
        self.geometry.y = float(frame.origin.y)
        self.geometry.size = float(frame.size.width)
        self.geometry.save()

    def __init__(self, url: str, size: float | None = None):
        # `?overlay=1` puts the app in its compact, transparent mode.
        # Appending it here without a path separator produced
        # `http://host:3100?overlay=1`, whose query did not survive to
        # `window.location.search` — so the app rendered its full-window
        # HUD inside a 400pt panel and the orb stayed hidden behind it.
        # The caller passes a complete URL instead.
        self.url = url
        self.geometry = Geometry.load()
        if size is not None:
            self.geometry.size = size
        self.geometry.clamp()
        self.size = self.geometry.size
        #: Click-through by default; see set_interactive().
        self.interactive = False
        self._visible = False
        self._hide_at: float | None = None
        #: Written by the bridge thread, read by the main-thread timer.
        self.pending_state: str | None = None
        #: (gesture, progress) from the tracker thread.
        self.pending_gesture = None
        self._drag_view = None
        self._interactive_since = 0.0
        #: The frame to return to; also the full-screen flag.
        self._fullscreen_restore = None
        #: Set by the CLI so the menu tick can follow an auto-exit.
        self._on_interactive_change = None

        size = self.geometry.size
        visible = AppKit.NSScreen.mainScreen().visibleFrame()
        if self.geometry.x is not None and self.geometry.y is not None:
            rect = Foundation.NSMakeRect(self.geometry.x, self.geometry.y, size, size)
        else:
            rect = Foundation.NSMakeRect(
                visible.origin.x + visible.size.width - size - MARGIN,
                visible.origin.y + MARGIN,
                size, size,
            )

        # NonactivatingPanel is the load-bearing flag: without it, showing the
        # orb pulls focus out of whatever you were typing into.
        style = (
            AppKit.NSWindowStyleMaskBorderless
            | AppKit.NSWindowStyleMaskNonactivatingPanel
        )
        self.panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, AppKit.NSBackingStoreBuffered, False
        )
        self.panel.setOpaque_(False)
        self.panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.panel.setHasShadow_(False)
        self.panel.setLevel_(AppKit.NSScreenSaverWindowLevel)
        self.panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        # Never take focus, even when clicked.
        self.panel.setBecomesKeyOnlyIfNeeded_(True)
        self.panel.setFloatingPanel_(True)
        # Clickable always, by request: the HUD buttons in the panel need the
        # mouse. The cost is real and worth stating — a 760pt square sitting
        # above every window now intercepts any click that lands on it, so it
        # is no longer a ghost you can click straight through. It still never
        # takes focus, so it cannot interrupt what you are typing into.
        self.panel.setIgnoresMouseEvents_(False)
        self.panel.setAlphaValue_(0.0)

        config = WebKit.WKWebViewConfiguration.alloc().init()
        # A fresh, non-persistent store each launch. WKWebView otherwise caches
        # the app's JS hard enough that restarting the overlay kept running the
        # previous build — new code on disk, old code on screen, and no way to
        # tell from the outside which you were looking at.
        config.setWebsiteDataStore_(
            WebKit.WKWebsiteDataStore.nonPersistentDataStore()
        )

        self.web = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            Foundation.NSMakeRect(0, 0, size, size), config
        )
        # A transparent web view is what makes this an orb on your desktop
        # rather than a black square with an orb in it.
        self.web.setValue_forKey_(False, "drawsBackground")
        self.web.setUnderPageBackgroundColor_(AppKit.NSColor.clearColor())

        # Bypass every cache layer. A non-persistent data store still let
        # NSURLCache serve the previous JS bundle, so the panel kept running
        # code that no longer existed on disk.
        # 4 = NSURLRequestReloadIgnoringLocalAndRemoteCacheData
        request = Foundation.NSURLRequest.requestWithURL_cachePolicy_timeoutInterval_(
            Foundation.NSURL.URLWithString_(self.url), 4, 30.0
        )
        self.web.loadRequest_(request)

        # Always present, always ⌘-gated. See DragView.hitTest_.
        from .controls import DragView

        self._drag_view = DragView.alloc().initWithFrame_(
            Foundation.NSMakeRect(0, 0, self.geometry.size, self.geometry.size)
        )
        self._drag_view.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        # The panel starts invisible, so the page should start paused. Give the
        # bridge a moment to exist before calling it.
        Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            5.0, False, lambda _t: (None if self._visible else self._set_page_rendering(False))
        )
        self.panel.setContentView_(self.web)
        self.web.addSubview_(self._drag_view)
        self.panel.orderFrontRegardless()

    # ——— visibility ———

    def _fade(self, to: float, duration: float = 0.35) -> None:

        AppKit.NSAnimationContext.beginGrouping()
        AppKit.NSAnimationContext.currentContext().setDuration_(duration)
        self.panel.animator().setAlphaValue_(to)
        AppKit.NSAnimationContext.endGrouping()

    def _set_page_rendering(self, enabled: bool) -> None:
        """Ask the page to stop or resume its WebGL loop.

        A canvas at zero opacity still renders every frame. The panel is hidden
        for most of its life, so this is the difference between a presence that
        costs nothing while idle and one that quietly drains the battery.
        """
        js = (
            "(function(){"
            "  if (!window.__kavachSetRendering) return 'no-bridge';"
            f"  window.__kavachSetRendering({str(enabled).lower()});"
            "  return 'ok';"
            "})()"
        )

        def handler(result, error) -> None:
            # A None completion handler is silently dropped by pyobjc, so the
            # pause never ran and the hidden panel kept rendering at ~78% CPU.
            # Logging the result is also the only way to see that the page-side
            # bridge exists at all.
            if error is not None:
                log.warning("render pause failed: %s", error)
            elif result == "no-bridge":
                log.warning("page has no __kavachSetRendering bridge")
            else:
                log.info("page rendering %s", "on" if enabled else "PAUSED")

        self.web.evaluateJavaScript_completionHandler_(js, handler)

    def show(self) -> None:
        self._hide_at = None
        if self._visible:
            return
        self._visible = True
        self._set_page_rendering(True)
        self.panel.orderFrontRegardless()
        self._fade(1.0)
        log.debug("overlay shown")

    def hide(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._fade(0.0, 0.5)

        def finish(_timer) -> None:
            self._set_page_rendering(False)
            # A window at alpha 0 is still on screen and still composited —
            # measured at ~78% of a core in WebKit with rendering already
            # paused. Ordering it out is what actually stops the work.
            if not self._visible:
                self.panel.orderOut_(None)

        Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.6, False, finish)
        log.debug("overlay hidden")

    def apply_state(self, state: str) -> None:
        """Show for active states; linger briefly before hiding on idle."""
        # Minimised means minimised — a turn should not override an explicit
        # request to stay out of the way.
        if self.geometry.hidden or self.is_fullscreen:
            return
        # Move/resize keeps the panel on screen so there is something to grab,
        # but it must not pin it there forever: left on, it held the panel
        # visible and rendering indefinitely (~83% of a core in WebKit) long
        # after the user had finished positioning it. Auto-exit after a spell
        # of no interaction hands it back to the state machine.
        if self.interactive:
            if time.monotonic() - self._interactive_since > INTERACTIVE_TIMEOUT:
                log.info("move/resize idle for %.0fs — returning to click-through",
                         INTERACTIVE_TIMEOUT)
                self.set_interactive(False)
                if self._on_interactive_change:
                    self._on_interactive_change()
            else:
                return
        if self.geometry.always:
            self.show()
            return
        if state in ACTIVE_STATES:
            self.show()
        elif self._visible and self._hide_at is None:
            self._hide_at = time.monotonic() + LINGER_SECONDS

    def probe(self) -> None:
        """Log what the page actually loaded.

        The panel is opaque from the outside: there is no console and no
        inspector, so a stale bundle or a dropped query string looks identical
        to a styling bug. Asking the page directly is the only honest check.
        """

        def handler(result, error) -> None:
            if error is not None:
                log.warning("probe failed: %s", error)
            else:
                log.info("page reports: %s", result)

        self.web.evaluateJavaScript_completionHandler_(
            "(function(){var c=document.querySelector('.orb-root canvas');"
            "return JSON.stringify({"
            "overlay:document.documentElement.classList.contains('kv-overlay'),"
            "canvas:!!c,"
            "cssPx:c?c.clientWidth:0,"
            "devicePx:c?c.width:0,"
            "ratio:c&&c.clientWidth?+(c.width/c.clientWidth).toFixed(2):0,"
            "dpr:window.devicePixelRatio,"
            "caption:!!document.querySelector('.overlay-caption')})})()",
            handler,
        )

    def tick(self) -> None:
        """Called on the AppKit main thread. The only place the panel moves."""
        state, self.pending_state = self.pending_state, None
        if state is not None:
            self.apply_state(state)

        if self._hide_at is not None and time.monotonic() >= self._hide_at:
            self._hide_at = None
            self.hide()


class BridgeListener(threading.Thread):
    """Follows agent state from the bridge and drives the panel.

    Reconnects on its own: the voice loop is restarted often during
    development, and an overlay that dies with it would be useless.
    """

    daemon = True

    def __init__(self, overlay: OverlayWindow, url: str):
        super().__init__(name="kavach-overlay-bridge")
        self.overlay = overlay
        self.url = url
        self._stop = threading.Event()

    def run(self) -> None:
        # The *synchronous* websocket client: one fewer event loop in a
        # process that already has AppKit's run loop, and a plain blocking
        # thread is easier to reason about than asyncio-inside-a-thread.
        #
        # It was originally swapped in to fix a crash that turned out not to
        # exist — the liveness check was grepping for the wrong process name,
        # so a healthy overlay looked dead. Keeping the sync client on its
        # merits, not on that story.
        from websockets.sync.client import connect

        def pump() -> None:
            while not self._stop.is_set():
                try:
                    with connect(self.url, open_timeout=5) as ws:
                        log.info("overlay connected to %s", self.url)
                        for message in ws:
                            if self._stop.is_set():
                                return
                            try:
                                state = (json.loads(message) or {}).get("state")
                            except Exception:
                                continue
                            if state:
                                # Hand the state over by assignment only.
                                #
                                # AppKit is not thread-safe, and dispatching a
                                # Python callable onto the main queue from here
                                # crashes the process outright — no exception,
                                # no traceback, just gone. A plain attribute
                                # write is atomic, and the main-thread timer
                                # that already runs picks it up on the next
                                # tick, so no AppKit object is ever touched
                                # from this thread.
                                self.overlay.pending_state = state
                except Exception as exc:
                    log.debug("bridge unavailable (%s); retrying", exc)
                    self._stop.wait(2.0)

        pump()

    def send(self, payload: dict) -> bool:
        """Fire a one-shot command at the bridge.

        Its own short-lived connection: the listener's socket is parked in a
        blocking read on the receive side, and sharing it would mean
        synchronising two threads around one socket for a message sent once
        every few minutes.
        """
        try:
            from websockets.sync.client import connect

            with connect(self.url, open_timeout=2) as ws:
                ws.send(json.dumps(payload))
            return True
        except Exception as exc:
            log.warning("could not reach the brain: %s", exc)
            return False

    def stop(self) -> None:
        self._stop.set()
