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

log = logging.getLogger("kavach.presence.overlay")

#: States where the orb should be visible. Idle is deliberately absent — a
#: presence that is always on screen is just clutter.
ACTIVE_STATES = {"listening", "thinking", "acting", "speaking", "halted"}

#: How long to linger after returning to idle, so the orb does not vanish
#: mid-sentence the instant a turn ends.
LINGER_SECONDS = 2.5

PANEL_SIZE = 340.0
MARGIN = 28.0


class OverlayWindow:
    """A floating, transparent, non-activating panel hosting the orb."""

    def __init__(self, url: str, size: float = PANEL_SIZE):
        self.url = url
        self.size = size
        self._visible = False
        self._hide_at: float | None = None
        #: Written by the bridge thread, read by the main-thread timer.
        self.pending_state: str | None = None

        screen = AppKit.NSScreen.mainScreen()
        frame = screen.visibleFrame()
        rect = Foundation.NSMakeRect(
            frame.origin.x + frame.size.width - size - MARGIN,
            frame.origin.y + MARGIN,
            size,
            size,
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
        self.panel.setIgnoresMouseEvents_(True)
        self.panel.setAlphaValue_(0.0)

        config = WebKit.WKWebViewConfiguration.alloc().init()

        # The orb page paints an opaque black body — correct for a full-screen
        # browser tab, wrong for a floating panel, where it reads as a black
        # rectangle stuck to the desktop. Strip the background at document
        # start so it is transparent from the first frame rather than flashing
        # black and then clearing.
        css = (
            "html,body{background:transparent !important;"
            "background-color:transparent !important;overflow:hidden !important}"
            # The vignette and scanline overlays assume a dark page behind
            # them; floating over the desktop they just muddy it.
            ".overlay-vignette,.overlay-grain,.overlay-scanlines{display:none !important}"
        )
        script_source = (
            "(function(){var s=document.createElement('style');"
            f"s.textContent={json.dumps(css)};"
            "document.documentElement.appendChild(s);})();"
        )
        user_script = WebKit.WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            script_source,
            0,  # WKUserScriptInjectionTimeAtDocumentStart
            True,
        )
        config.userContentController().addUserScript_(user_script)

        self.web = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            Foundation.NSMakeRect(0, 0, size, size), config
        )
        # A transparent web view is what makes this an orb on your desktop
        # rather than a black square with an orb in it.
        self.web.setValue_forKey_(False, "drawsBackground")
        self.web.setUnderPageBackgroundColor_(AppKit.NSColor.clearColor())

        request = Foundation.NSURLRequest.requestWithURL_(
            Foundation.NSURL.URLWithString_(self.url)
        )
        self.web.loadRequest_(request)
        self.panel.setContentView_(self.web)
        self.panel.orderFrontRegardless()

    # ——— visibility ———

    def _fade(self, to: float, duration: float = 0.35) -> None:

        AppKit.NSAnimationContext.beginGrouping()
        AppKit.NSAnimationContext.currentContext().setDuration_(duration)
        self.panel.animator().setAlphaValue_(to)
        AppKit.NSAnimationContext.endGrouping()

    def show(self) -> None:
        self._hide_at = None
        if self._visible:
            return
        self._visible = True
        self.panel.orderFrontRegardless()
        self._fade(1.0)
        log.debug("overlay shown")

    def hide(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._fade(0.0, 0.5)
        log.debug("overlay hidden")

    def apply_state(self, state: str) -> None:
        """Show for active states; linger briefly before hiding on idle."""
        if state in ACTIVE_STATES:
            self.show()
        elif self._visible and self._hide_at is None:
            self._hide_at = time.monotonic() + LINGER_SECONDS

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
        # The *synchronous* websocket client, deliberately.
        #
        # Running `asyncio.run()` on this thread alongside AppKit's run loop
        # made NSApplication.run() return the moment a connection was
        # established — the process exited cleanly, no traceback, no crash
        # report, and the overlay simply vanished as soon as the voice loop
        # came up. The sync client needs no event loop, so the two never meet.
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

    def stop(self) -> None:
        self._stop.set()
