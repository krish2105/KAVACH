"""Menu bar, hotkeys and window geometry for the desktop orb.

The panel is click-through and non-activating by default: it floats over your
work, never steals focus, and never swallows a click meant for the window
underneath. That is what makes it a presence rather than another window.

Dragging and resizing need the opposite — a panel that accepts mouse events.
Rather than give up click-through permanently, *interactive mode* is a toggle:
off by default, on while you are positioning it, off again afterwards. So both
behaviours exist without one quietly costing you the other.

Geometry persists, because a floating window you have to reposition at every
login is worse than no floating window.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import AppKit
import Foundation
import objc

log = logging.getLogger("kavach.presence.controls")

GEOMETRY_PATH = Path.home() / ".kavach" / "overlay.json"

#: Named sizes, in points. Small is glanceable; large is for the demo video.
SIZES = {"small": 280.0, "medium": 400.0, "large": 560.0, "huge": 760.0}
DEFAULT_SIZE = "large"

MIN_SIZE = 200.0
MAX_SIZE = 1200.0


@dataclass
class Geometry:
    """Where the panel sits and how big it is."""

    size: float = SIZES[DEFAULT_SIZE]
    #: None means "bottom-right corner", recomputed for the current screen.
    x: float | None = None
    y: float | None = None
    hidden: bool = False
    #: Persisted, unlike a normal mode. Restarting the agent otherwise turns
    #: dragging silently off and the panel looks broken again.
    interactive: bool = False
    #: Stay on screen even when idle. Off by default — a presence that is
    #: always there is just clutter — but "where did it go?" turned out to be
    #: the more common problem, so it needs to be one click away.
    always: bool = False

    @classmethod
    def load(cls) -> "Geometry":
        try:
            data = json.loads(GEOMETRY_PATH.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})
        except Exception:
            return cls()

    def save(self) -> None:
        try:
            GEOMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
            GEOMETRY_PATH.write_text(json.dumps(asdict(self), indent=2))
        except Exception as exc:  # never let a save failure kill the presence
            log.debug("could not save overlay geometry: %s", exc)

    def clamp(self) -> None:
        self.size = max(MIN_SIZE, min(MAX_SIZE, self.size))

    def step_size(self, direction: int) -> None:
        """Move one named size up or down, then fall back to a ratio.

        Stepping through named sizes keeps the hotkeys landing on the same
        sizes the menu offers, instead of drifting to arbitrary numbers.
        """
        ordered = sorted(SIZES.values())
        if direction > 0:
            larger = [s for s in ordered if s > self.size + 1]
            self.size = larger[0] if larger else self.size * 1.15
        else:
            smaller = [s for s in ordered if s < self.size - 1]
            self.size = smaller[-1] if smaller else self.size / 1.15
        self.clamp()


class DragView(AppKit.NSView):
    """A transparent layer that makes the panel draggable.

    `movableByWindowBackground` moves a window when you drag its *background* —
    but the panel's entire content is a WKWebView, which consumes every mouse
    event before the window sees one. So switching interactive mode on
    appeared to do nothing: the panel accepted clicks and still refused to
    move.

    This sits above the web view and hands the drag to the window itself.
    """

    def acceptsFirstMouse_(self, _event):
        # The panel never becomes key, so without this the first click after
        # focusing another app is swallowed just to activate — meaning every
        # drag would need two attempts.
        return True

    def hitTest_(self, point):
        """Claim the mouse only while ⌘ is held; otherwise let it fall through.

        Sitting permanently over the web view, this would swallow every click
        meant for the HUD buttons. Gating on a modifier means one layer serves
        both: ⌘-drag moves the panel from anywhere on it, and an ordinary click
        passes straight through to whatever is underneath.

        This replaces a "move/resize" mode, which required remembering to turn
        dragging on before it would work — and silently did nothing when you
        forgot, which is exactly how it presented.
        """
        modifiers = AppKit.NSEvent.modifierFlags()
        if modifiers & AppKit.NSEventModifierFlagCommand:
            return self
        return None

    def mouseDown_(self, event):
        self.window().performWindowDragWithEvent_(event)


#: What the status item shows per state (§17).
#:
#: WidgetKit was the original plan and is impossible here — a Widget Extension
#: has to be archived from Xcode, and this machine has Command Line Tools only.
#: The stated intent was "KAVACH's status without the orb window open", and a
#: menu bar item delivers exactly that with no Xcode and no download.
#:
#: Ghost and the latched switch spell themselves out in words rather than
#: relying on a glyph. Whether KAVACH is listening is the one thing about it
#: that must never need interpreting, and an emoji at menu-bar size is easy to
#: misread at a glance.
STATUS_TITLES = {
    "boot": "🛡",
    "idle": "🛡",
    "listening": "🛡 ●",
    "thinking": "🛡 ⋯",
    "acting": "🛡 ⚡",
    "speaking": "🛡 ▶",
    "halted": "⛔ STOPPED",
}
GHOST_TITLE = "👻 GHOST"
LATCHED_TITLE = "⛔ STOPPED"


def status_title(snapshot: dict) -> str:
    """The menu-bar title for a snapshot.

    Pure, and separate from the AppKit call, so the precedence rules can be
    tested without a status bar to put them in.

    Precedence is deliberate and not alphabetical: a latched kill switch
    outranks everything, then ghost mode, then whatever KAVACH is doing. Both
    of those are conditions you need to see *regardless* of the activity
    underneath — "listening" while latched would be a lie, and "thinking" while
    in ghost mode would be worse.
    """
    if snapshot.get("killSwitch") == "disarmed":
        return LATCHED_TITLE
    if snapshot.get("ghost"):
        return GHOST_TITLE
    return STATUS_TITLES.get(str(snapshot.get("state", "idle")), "🛡")


class MenuBarController(AppKit.NSObject):
    """The 🛡 menu: live status, ghost mode, sizes, minimise, quit."""

    def initWithOverlay_onQuit_(self, overlay, on_quit):
        self = objc.super(MenuBarController, self).init()
        if self is None:
            return None
        self._overlay = overlay
        self._on_quit = on_quit

        bar = AppKit.NSStatusBar.systemStatusBar()
        self._item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        button = self._item.button()
        if button is None:
            # Worth shouting about: with no button there is no menu bar icon,
            # and the only way to quit or reach ghost mode is to kill the pid.
            log.error("menu bar item has no button — the 🛡 will not appear")
        else:
            button.setTitle_("🛡")
            log.info("menu bar item created")

        self._ghost_active = False
        self._menu = AppKit.NSMenu.alloc().init()
        self._build()
        self._item.setMenu_(self._menu)
        return self

    # ——— building ———

    # pyobjc exposes every method on an NSObject subclass as a selector and
    # checks the argument count against the selector name. Helpers that are
    # not actions have to say so, or the class fails to build at import.
    @objc.python_method
    def _add(self, title, action, key="", target=None):
        entry = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, action, key
        )
        entry.setTarget_(target or self)
        self._menu.addItem_(entry)
        return entry

    @objc.python_method
    def _build(self) -> None:
        self._menu.removeAllItems()

        header = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "KAVACH orb  —  ⌘-drag to move", None, ""
        )
        header.setEnabled_(False)
        self._menu.addItem_(header)
        self._menu.addItem_(AppKit.NSMenuItem.separatorItem())

        for name in ("small", "medium", "large", "huge"):
            item = self._add(f"  {name.capitalize()}", b"setSize:")
            item.setRepresentedObject_(name)
            if abs(self._overlay.geometry.size - SIZES[name]) < 1:
                item.setState_(AppKit.NSControlStateValueOn)

        self._menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self._interactive_item = self._add(
            "  Resizable  ⌃⌥⌘M", b"toggleInteractive:"
        )
        self._interactive_item.setState_(
            AppKit.NSControlStateValueOn if self._overlay.interactive
            else AppKit.NSControlStateValueOff
        )

        # Minimise persists across restarts, so its state has to be visible.
        # Without a tick it silently swallows every appearance and looks like
        # the panel is broken rather than switched off.
        fs = self._add("  Full screen  ⌃⌥⌘F", b"toggleFullscreen:")
        fs.setState_(
            AppKit.NSControlStateValueOn if self._overlay.is_fullscreen
            else AppKit.NSControlStateValueOff
        )

        self._always_item = self._add(
            "  Always show", b"toggleAlways:"
        )
        self._always_item.setState_(
            AppKit.NSControlStateValueOn if self._overlay.geometry.always
            else AppKit.NSControlStateValueOff
        )

        self._hide_item = self._add(
            "  Minimised  ⌃⌥⌘H" if self._overlay.geometry.hidden
            else "  Minimise  ⌃⌥⌘H",
            b"toggleHidden:",
        )
        self._hide_item.setState_(
            AppKit.NSControlStateValueOn if self._overlay.geometry.hidden
            else AppKit.NSControlStateValueOff
        )
        self._add("  Reset position", b"resetPosition:")

        self._menu.addItem_(AppKit.NSMenuItem.separatorItem())
        ghost = self._add(
            "  Ghost mode  —  stop listening" if not self._ghost_active
            else "  Ghost mode  —  RESUME listening",
            b"toggleGhost:",
        )
        ghost.setState_(
            AppKit.NSControlStateValueOn if self._ghost_active
            else AppKit.NSControlStateValueOff
        )

        self._menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self._add("  Quit orb", b"quit:")

    def refresh(self) -> None:
        self._build()

    @objc.python_method
    def apply_snapshot(self, snapshot: dict) -> None:
        """Show the current state in the menu bar. **Main thread only.**

        Called from `OverlayWindow.tick()`, which is a main-thread timer.
        AppKit is not thread-safe and touching the status item from the bridge
        thread takes the process down with no traceback at all — so the
        snapshot is handed over by attribute assignment and read here.
        """
        ghost = bool(snapshot.get("ghost"))
        title = status_title(snapshot)

        try:
            self._item.button().setTitle_(title)
        except Exception:
            pass

        if ghost != self._ghost_active:
            self._ghost_active = ghost
            self._build()

    # ——— actions ———

    def setSize_(self, sender):
        self._overlay.set_size(SIZES[sender.representedObject()])
        self.refresh()

    def toggleInteractive_(self, _sender):
        self._overlay.set_interactive(not self._overlay.interactive)
        self.refresh()

    def toggleFullscreen_(self, _sender):
        self._overlay.toggle_fullscreen()
        self.refresh()

    def toggleAlways_(self, _sender):
        self._overlay.set_always(not self._overlay.geometry.always)
        self.refresh()

    def toggleHidden_(self, _sender):
        self._overlay.set_pinned_hidden(not self._overlay.geometry.hidden)
        self.refresh()

    def resetPosition_(self, _sender):
        self._overlay.reset_position()
        self.refresh()

    def toggleGhost_(self, _sender):
        """Both directions — this is the local control, at the machine.

        `POST /ghost` can only enter ghost mode; turning the microphone back on
        deliberately requires being here.
        """
        want = not self._ghost_active
        sender = getattr(self._overlay, "send_command", None)
        if sender is not None:
            sender({"cmd": "ghost", "on": want, "source": "menubar"})
        # The real state arrives back over the bridge; this only stops the menu
        # looking stale between now and the next snapshot.
        self._ghost_active = want
        self._build()

    def quit_(self, _sender):
        self._on_quit()
