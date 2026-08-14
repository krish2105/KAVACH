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
# Medium.
#
# Large fits the full HUD and is too much screen; small cannot hold it. 400pt
# with the compact HUD — no routing-reason line, no transcript while idle, and
# the controls behind the 🛡 rather than stacked — is the size where the orb is
# still the thing you are looking at.
DEFAULT_SIZE = "medium"

MIN_SIZE = 200.0
MAX_SIZE = 1200.0


def screen_frames() -> list[tuple[float, float, float, float]]:
    """Every display's visible frame. Empty when there is no window server."""
    try:
        return [(f.origin.x, f.origin.y, f.size.width, f.size.height)
                for f in (s.visibleFrame() for s in AppKit.NSScreen.screens())]
    except Exception:
        return []


def should_hide_when_idle(bridge_connected: bool, always: bool) -> bool:
    """Whether an idle orb should fade off screen.

    "Hide when idle" needs something that can stop being idle. The overlay
    fades in when KAVACH starts listening — but if the voice loop is not
    running, no snapshot ever arrives, the state is idle forever, and the rule
    means simply "hide". After a reboot that is an empty desktop and a menu bar
    item, with nothing to say whether the orb is broken or merely quiet.

    So an orb with no brain behind it stays on screen. It is the only way to
    see that it is there at all, and the menu is reachable from it.
    """
    if always:
        return False
    return bool(bridge_connected)


#: Auto-repeat arrives every ~83ms. Anything closer together than this is the
#: keyboard repeating, not a person pressing twice — nobody taps a chord seven
#: times a second, and a deliberate second press is always slower than this.
CHORD_REPEAT_GAP_S = 0.15


#: macOS waits before it starts repeating a held key — "Delay Until Repeat",
#: about 500ms by default. That gap is longer than any debounce that still
#: feels responsive, so the *first* repeat of a hold looks exactly like a fresh
#: press if you only measure time. `isARepeat` is what separates them, and it
#: is trustworthy in this direction: an event flagged as a repeat always is
#: one. It is only the absence of the flag that cannot be relied on, because
#: the first keyDown may never arrive at all.
HOLD_GAP_S = 1.5


def should_act_on_hotkey(modifiers_held: bool,
                         seconds_since_previous: float,
                         is_repeat: bool = False) -> bool:
    """Whether a global-hotkey event is a press worth acting on.

    macOS repeats a held key about every 83ms, each one an ordinary keyDown.
    The handler filtered on modifiers and key code only, so holding ⌃⌥⌘Space
    asked for a turn a dozen times a second — 96 "talk requested" lines in 200
    of `~/.kavach/logs/overlay.log`, 81ms apart. Every one opened its own
    websocket to the bridge and queued another turn, which is how a turn ends
    up with `record_ms: 15009`.

    **The obvious fix — ignore events with `isARepeat` set — does not work
    here**, and the log says why:

        23:16:38,671  repeat=True      13 consecutive repeats,
        23:16:38,752  repeat=True      with no repeat=False before them
        ...
        23:16:41,155  repeat=False     the next press, 1.7s later

    ⌃Space is macOS's own input-source switcher, so the *first* keyDown of the
    chord is often consumed before a global monitor sees it and only the
    repeats arrive. Dropping repeats therefore drops the entire hold: the
    hotkey silently does nothing, which is worse than firing too often.

    So both signals are used, each for what it can actually prove:

    * **the gap** rejects the 83ms repeat stream, and rescues a hold whose
      first press was eaten — there the earliest event we see is the press, as
      far as anything here can tell.
    * **`isARepeat`** rejects the *first* repeat, which arrives ~500ms after
      the press and is otherwise indistinguishable from someone pressing
      again. Measured, before this: one hold, two turns.

          23:20:53,005  chord accepted  (inf since the last)    the press
          23:20:53,503  chord accepted  (0.50s since the last)  the first repeat

    A flagged repeat is therefore only honoured when nothing has arrived for
    `HOLD_GAP_S` — long enough that it cannot belong to a hold already in
    progress.
    """
    if not modifiers_held:
        return False
    if seconds_since_previous < CHORD_REPEAT_GAP_S:
        return False
    if is_repeat and seconds_since_previous < HOLD_GAP_S:
        return False
    return True


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
        self.clamp_position()

    def clamp_position(self) -> None:
        """Pull the panel back onto a display that still exists.

        The position is remembered, so a panel last seen on an external monitor
        keeps those coordinates after the monitor is unplugged — and a window
        at x=-2400 is invisible in exactly the same way a minimised one is,
        while insisting it is shown.

        `None` is already the convention for "bottom-right of the current
        screen, worked out when it is shown", so that is what it resets to.
        """
        if self.x is None or self.y is None:
            return

        frames = screen_frames()
        if not frames:
            # No window server (tests, headless). Nothing to check against, and
            # guessing would move a panel that may be perfectly placed.
            return

        if not any(self.x < fx + fw and self.x + self.size > fx
                   and self.y < fy + fh and self.y + self.size > fy
                   for fx, fy, fw, fh in frames):
            log.info("panel was off every display (%.0f, %.0f) — recentring",
                     self.x, self.y)
            self.x = self.y = None

    def apply_size(self, size: float) -> None:
        """Choose a size — which also means "and let me see it".

        Resizing used to leave `hidden` alone, so every entry in the size menu
        resized a window nobody could see. The click landed, the geometry
        changed, the panel stayed gone; from outside it was a menu that did
        nothing. Asking for Large while minimised can only mean one thing.

        Minimise itself is untouched: `set_hidden(True)` still hides, because
        deliberately staying out of the way is the point of it.
        """
        self.size = size
        self.hidden = False
        self.clamp()
        self.save()

    def set_hidden(self, hidden: bool) -> None:
        self.hidden = hidden
        self.save()

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
#: Plain text, not an emoji.
#:
#: The 🛡 was created correctly — the log said so — and could not be found on
#: screen. An emoji in a status item depends on font fallback and on the menu
#: bar having room, and when it loses it renders as nothing at all: an item
#: that exists, occupies space, and is invisible. Letters always draw.
STATUS_TITLES = {
    "boot": "KAVACH",
    "idle": "KAVACH",
    "listening": "KAVACH ●",
    "thinking": "KAVACH ⋯",
    "acting": "KAVACH ⚡",
    "speaking": "KAVACH ▶",
    "halted": "KAVACH STOPPED",
}
GHOST_TITLE = "KAVACH GHOST"
LATCHED_TITLE = "KAVACH STOPPED"


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
    return STATUS_TITLES.get(str(snapshot.get("state", "idle")), "KAVACH")


class MenuBarController(AppKit.NSObject):
    """The KAVACH menu: live status, ghost mode, sizes, minimise, quit."""

    def initWithOverlay_onQuit_(self, overlay, on_quit):
        self = objc.super(MenuBarController, self).init()
        if self is None:
            return None
        self._overlay = overlay
        self._on_quit = on_quit

        bar = AppKit.NSStatusBar.systemStatusBar()
        self._item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        # Never variable-length with a text title: an item that has to
        # negotiate for width is the one macOS drops when the bar is busy.
        button = self._item.button()
        if button is None:
            # Worth shouting about: with no button there is no menu bar icon,
            # and the only way to quit or reach ghost mode is to kill the pid.
            log.error("menu bar item has no button — the 🛡 will not appear")
        else:
            button.setTitle_("KAVACH")
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

    @objc.python_method
    def reattach(self) -> None:
        """Ensure the item is really in the bar, once the run loop is running.

        Idempotent. If the first attempt attached, this confirms it; if it did
        not — which is what happens when the item is created before the
        application finishes launching — this is the one that works.
        """
        import AppKit as _AppKit

        # Always re-created, never "only if the button is missing".
        #
        # The previous version checked button() and found it perfectly alive —
        # so it never retried, and the item it was so confident about was not
        # in the bar at all. A status item made before the application has
        # finished launching reports healthy and attaches to nothing.
        bar = _AppKit.NSStatusBar.systemStatusBar()
        old = self._item
        self._item = bar.statusItemWithLength_(_AppKit.NSSquareStatusItemLength)
        # Fixed width, not variable: a variable-length item has to negotiate
        # for space and is the first thing dropped when the bar is busy.
        self._item.setLength_(72.0)
        self._item.setMenu_(self._menu)
        if old is not None:
            try:
                bar.removeStatusItem_(old)
            except Exception:
                pass
        log.info("menu bar item re-created after launch")

        button = self._item.button()
        if button is not None:
            button.setTitle_("KAVACH")
        # Survives the app being asked to hide its status items.
        try:
            self._item.setVisible_(True)
        except Exception:
            pass
        log.info("menu bar item attached: %s",
                 "yes" if button is not None else "NO")

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
