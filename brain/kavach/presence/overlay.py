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

import objc

import AppKit
import Foundation

import WebKit

from .controls import Geometry, should_hide_when_idle

log = logging.getLogger("kavach.presence.overlay")

#: States where the orb should be visible. Idle is deliberately absent — a
#: presence that is always on screen is just clutter.
ACTIVE_STATES = {"listening", "thinking", "acting", "speaking", "halted"}

#: How long to linger after returning to idle, so the orb does not vanish
#: mid-sentence the instant a turn ends.
MAX_RELOAD_ATTEMPTS = 3

LINGER_SECONDS = 2.5

#: Move/resize returns to click-through after this long untouched.
INTERACTIVE_TIMEOUT = 120.0

PANEL_SIZE = 400.0
MARGIN = 28.0


class OverlayWindow:
    """A floating, transparent, non-activating panel hosting the orb."""

    def set_size(self, size: float) -> None:
        """Resize about the panel's centre, so it does not walk across the
        screen as you step through sizes.

        Also un-minimises. Resizing used to leave `hidden` alone, so while the
        panel was minimised every entry in the size menu resized a window
        nobody could see — the click landed, the geometry changed, and nothing
        appeared. Asking for Large can only mean you want to look at it.
        """
        self.geometry.apply_size(size)
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
        self.show()

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
        # Filling the display is the least ambiguous "show me the orb" there
        # is, so it cannot leave the panel minimised either.
        self.geometry.hidden = False

        if self._fullscreen_restore is not None:
            self.panel.setFrame_display_animate_(self._fullscreen_restore, True, True)
            self._fullscreen_restore = None
            self.web.setFrame_(
                Foundation.NSMakeRect(0, 0, self.geometry.size, self.geometry.size)
            )
            self._set_fullscreen_chrome(False)
            log.info("full screen off")
            return

        self._fullscreen_restore = self.panel.frame()
        frame = AppKit.NSScreen.mainScreen().frame()
        self.panel.setFrame_display_animate_(frame, True, True)
        self.web.setFrame_(
            Foundation.NSMakeRect(0, 0, frame.size.width, frame.size.height)
        )
        self._set_fullscreen_chrome(True)
        # Cancel any dismissal already in flight, or a linger scheduled just
        # before this call will hide the panel moments after it fills the screen.
        self._hide_at = None
        self.show()
        log.info("full screen on (%.0fx%.0f)", frame.size.width, frame.size.height)

    def _set_fullscreen_chrome(self, on: bool) -> None:
        """Opaque black in full screen, transparent as a floating panel.

        Both halves are needed and neither is sufficient. The CSS alone still
        composites against a transparent NSWindow, so the desktop reads through
        anything not fully opaque — which is most of a glowing orb. The window
        alone leaves the page painting `background: transparent !important`
        over it. Set them together or the result is the washed-out overlay this
        exists to fix.
        """
        colour = (AppKit.NSColor.blackColor() if on
                  else AppKit.NSColor.clearColor())
        try:
            self.panel.setOpaque_(bool(on))
            self.panel.setBackgroundColor_(colour)
            self.web.setUnderPageBackgroundColor_(colour)
            # The WKWebView draws its own background only when told to; left
            # off, an opaque window shows through as grey behind the page.
            self.web.setValue_forKey_(bool(on), "drawsBackground")
        except Exception:
            log.debug("could not set full-screen chrome", exc_info=True)

        js = ("document.documentElement.classList."
              + ("add" if on else "remove")
              + "('kv-fullscreen')")
        self.web.evaluateJavaScript_completionHandler_(js, lambda *_: None)

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
        # Asking for it back in the corner means asking to see it.
        self.geometry.hidden = False
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
        #: Set by BridgeListener. False means no voice loop has
        #: ever answered, so nothing can make this orb active.
        self.bridge_connected = False
        #: Written by the bridge thread, read by the main-thread timer.
        self.pending_state: str | None = None
        #: §17. The full snapshot, for the menubar. Same hand-off rule as
        #: pending_state: written by the bridge thread, read on the main
        #: thread, never touched by AppKit from anywhere else.
        self.pending_snapshot: dict | None = None
        #: Latest pinch move, applied on the next main-thread tick. Same
        #: hand-off rule as the snapshot: written by the tracker thread, read
        #: on the main thread, so no AppKit or WebKit object is touched from
        #: anywhere else.
        self.pending_control = None
        #: Latest two-finger scroll, same hand-off rule as the pinch.
        self.pending_scroll = None
        #: What a gesture is currently driving — "orb", an app name, or
        #: "blocked". Shown in the HUD so which one is never a guess.
        self.pending_target = None
        self.pending_refusal = None
        #: Set by the presence process so the panel can arm app control.
        self.app_control = None
        #: Set by the presence process so the panel's Quit button works.
        self.on_quit = None
        #: Bounded, so a genuinely-down server becomes a loud error rather
        #: than an infinite reload loop against nothing.
        self._reload_attempts = 0
        #: Set by the presence process; called on the main thread with each
        #: snapshot so the status item can follow along.
        self.on_snapshot = None
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

        # Let the panel talk back.
        #
        # The menu bar item never attaches when the overlay runs from its app
        # bundle — created, reports healthy, absent from the bar — so the
        # controls it carried had nowhere to live. The page can reach the
        # brain over the websocket, but sizing, full screen and quitting
        # belong to THIS process, and a bridge round trip cannot reach it.
        # A script message handler is the direct line.
        self._controller = WebKit.WKUserContentController.alloc().init()
        self._handler = _PanelBridge.alloc().initWithOverlay_(self)
        self._controller.addScriptMessageHandler_name_(self._handler, "kavach")
        config.setUserContentController_(self._controller)

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

        # Minimised stays minimised across a restart, and that has to be
        # honoured *here*.
        #
        # It used to be enforced only inside apply_state(), which nothing calls
        # when no voice loop is running — so a minimised orb reappeared on
        # every launch without a brain, and vanished on the first snapshot once
        # one arrived. Same flag, two different behaviours, depending on
        # something the user cannot see.
        if self.geometry.hidden:
            log.info("panel starts minimised — any size, full screen or "
                     "reset position from the 🛡 menu brings it back")
        else:
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
        #
        # Enforced rather than merely returned early. Returning left the flag
        # unenforced whenever no voice loop was running (nothing calls this at
        # all then), so Minimise worked with a brain and did nothing without
        # one. Every route back out — any size, full screen, reset position —
        # clears it, so this can no longer be a state you cannot leave.
        if self.geometry.hidden:
            if self._visible:
                self.hide()
            return
        if self.is_fullscreen:
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
        if self.is_fullscreen or not should_hide_when_idle(
                self.bridge_connected, self.geometry.always):
            # Full screen is a mode you entered deliberately. Letting the idle
            # linger timer dismiss it means the orb fills the display and then
            # silently disappears a few seconds later, which reads as a crash.
            #
            # The same applies with no voice loop running: nothing will ever
            # set an active state, so "hide when idle" would mean "hide", and
            # the orb would be invisible from login onwards with no way to
            # tell a broken one from a quiet one.
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
                return

            log.info("page reports: %s", result)

            # Recover rather than sit there broken.
            #
            # A page whose CSS never arrived still renders — as a column of
            # unstyled text reading "KAVACHकवच · local-first presence
            # CONNECTING…" — and WKWebView keeps showing that indefinitely.
            # It happened when the overlay loaded while `next start` was
            # mid-restart. Reloading costs a second; not reloading costs the
            # whole panel until somebody notices and restarts it by hand.
            try:
                import json as _json

                report = _json.loads(result) if isinstance(result, str) else {}
            except Exception:
                return

            if report.get("styled") is False or report.get("canvas") is False:
                if self._reload_attempts >= MAX_RELOAD_ATTEMPTS:
                    log.error("page still broken after %d reloads — is "
                              "`next start` running on 3100?",
                              self._reload_attempts)
                    return
                self._reload_attempts += 1
                log.warning("page loaded unstyled (styled=%s canvas=%s) — "
                            "reloading, attempt %d",
                            report.get("styled"), report.get("canvas"),
                            self._reload_attempts)
                self.reload()
            else:
                self._reload_attempts = 0

        self.web.evaluateJavaScript_completionHandler_(
            "(function(){var c=document.querySelector('.orb-root canvas');"
            "return JSON.stringify({"
            "overlay:document.documentElement.classList.contains('kv-overlay'),"
            "canvas:!!c,"
            "cssPx:c?c.clientWidth:0,"
            "devicePx:c?c.width:0,"
            "ratio:c&&c.clientWidth?+(c.width/c.clientWidth).toFixed(2):0,"
            "dpr:window.devicePixelRatio,"
            "caption:!!document.querySelector('.overlay-caption'),"
            # Did the stylesheet actually load? A page whose CSS 404'd still
            # renders — as a column of unstyled text — and WKWebView keeps
            # showing it forever. That happened when the overlay loaded while
            # `next start` was mid-restart, and nothing noticed.
            "styled:(document.styleSheets.length>0)&&"
            "(getComputedStyle(document.body).fontFamily||'').length>0&&"
            "!!document.querySelector('.hud,.orb-root')})})()",
            handler,
        )

    def handle_panel_command(self, command: str, value=None) -> None:
        """Act on a button in the panel. Main thread only.

        Deliberately a small, closed set. This is a channel from a web page
        into the process that owns the window, so it does what the menu did
        and nothing more — no arbitrary sizing, no eval, no file access.
        """
        from .controls import SIZES

        if command == "size" and str(value) in SIZES:
            self.set_size(SIZES[str(value)])
        elif command == "fullscreen":
            self.toggle_fullscreen()
        elif command == "minimise":
            self.set_pinned_hidden(not self.geometry.hidden)
        elif command == "interactive":
            self.set_interactive(not self.interactive)
        elif command == "reset":
            self.reset_position()
        elif command == "appcontrol":
            # Arming something that drives your other applications. Logged, and
            # never persisted — see appcontrol.py.
            controller = self.app_control
            if controller is None:
                log.warning("hand control of other apps is unavailable")
            elif value:
                controller.enable()
            else:
                controller.disable()
        elif command == "quit":
            if self.on_quit is not None:
                self.on_quit()
        else:
            log.warning("unknown panel command %r", command)

    def reload(self) -> None:
        """Re-fetch the page, bypassing every cache.

        Same cache policy as the initial load: a plain reload let NSURLCache
        hand back the broken response it had just cached, which is precisely
        the thing being recovered from.
        """
        request = Foundation.NSURLRequest.requestWithURL_cachePolicy_timeoutInterval_(
            Foundation.NSURL.URLWithString_(self.url), 4, 30.0
        )
        self.web.loadRequest_(request)
        # Re-probe after it has had a chance to settle, so a reload that also
        # fails is noticed rather than assumed to have worked.
        Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            6.0, False, lambda _t: self.probe()
        )

    def tick(self) -> None:
        """Called on the AppKit main thread. The only place the panel moves."""
        state, self.pending_state = self.pending_state, None
        if state is not None:
            self.apply_state(state)

        move, self.pending_control = self.pending_control, None
        if move is not None and getattr(move, "engaged", False):
            # Wake the panel first, or the whole thing is invisible.
            #
            # The overlay hides when idle and stops the page rendering to save
            # CPU, so a pinch was moving a camera in a frozen scene behind a
            # hidden window: the events fired, the JS ran, and nothing was ever
            # painted. Reaching for the orb with your hand is as much a reason
            # to be on screen as speaking to it.
            if not self._visible and not self.geometry.hidden:
                # Not while minimised. Hand tracking publishes a
                # control target every tick, so a hand anywhere
                # near the camera pulled the panel back on screen
                # and Minimise looked broken.
                self.show()
            self._hide_at = None

            if move.dx or move.dy or abs(move.scale - 1.0) > 0.005:
                self.web.evaluateJavaScript_completionHandler_(
                    "window.__kavachControl && window.__kavachControl("
                    f"{move.dx:.5f},{move.dy:.5f},{move.scale:.5f})",
                    lambda *_: None,
                )

        target, self.pending_target = self.pending_target, None
        if target is not None:
            refusal, self.pending_refusal = self.pending_refusal, None
            payload = json.dumps({"target": target, "refusal": refusal})
            self.web.evaluateJavaScript_completionHandler_(
                f"window.__kavachTarget && window.__kavachTarget({payload})",
                lambda *_: None,
            )
            if not self._visible and not self.geometry.hidden:
                # Not while minimised. Hand tracking publishes a
                # control target every tick, so a hand anywhere
                # near the camera pulled the panel back on screen
                # and Minimise looked broken.
                self.show()
            self._hide_at = None

        scroll, self.pending_scroll = self.pending_scroll, None
        if scroll is not None and getattr(scroll, "engaged", False):
            if not self._visible and not self.geometry.hidden:
                # Not while minimised. Hand tracking publishes a
                # control target every tick, so a hand anywhere
                # near the camera pulled the panel back on screen
                # and Minimise looked broken.
                self.show()
            self._hide_at = None
            if scroll.dy or scroll.dx:
                self.web.evaluateJavaScript_completionHandler_(
                    "window.__kavachScroll && window.__kavachScroll("
                    f"{scroll.dx:.5f},{scroll.dy:.5f})",
                    lambda *_: None,
                )

        snapshot, self.pending_snapshot = self.pending_snapshot, None
        if snapshot is not None and self.on_snapshot is not None:
            # Main thread, so the callback may safely touch the status item.
            try:
                self.on_snapshot(snapshot)
            except Exception:
                log.debug("snapshot callback failed", exc_info=True)

        if self._hide_at is not None and time.monotonic() >= self._hide_at:
            self._hide_at = None
            self.hide()


class _PanelBridge(AppKit.NSObject):
    """Receives `window.webkit.messageHandlers.kavach.postMessage({...})`.

    Runs on the main thread by contract — WebKit delivers script messages
    there — which is exactly where the panel may be resized and the app quit,
    so nothing needs marshalling.
    """

    def initWithOverlay_(self, overlay):
        self = objc.super(_PanelBridge, self).init()
        if self is None:
            return None
        self._overlay = overlay
        return self

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        try:
            body = message.body()
            command = str(body.get("cmd", "")) if hasattr(body, "get") else ""
            value = body.get("value") if hasattr(body, "get") else None
        except Exception:
            log.debug("unreadable panel message", exc_info=True)
            return

        log.info("panel command: %s %s", command, value if value is not None else "")
        try:
            self._overlay.handle_panel_command(command, value)
        except Exception:
            log.exception("panel command %r failed", command)


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
                        # A brain exists, so "hide when idle" now has an idle
                        # to come back from.
                        self.overlay.bridge_connected = True
                        for message in ws:
                            if self._stop.is_set():
                                return
                            try:
                                snapshot = json.loads(message) or {}
                                state = snapshot.get("state")
                            except Exception:
                                continue
                            if isinstance(snapshot, dict) and snapshot:
                                self.overlay.pending_snapshot = snapshot
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
                    # The brain is gone. Stop treating idle as a reason to
                    # hide, or the orb vanishes when the voice loop restarts
                    # and never comes back on its own.
                    self.overlay.bridge_connected = False
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
