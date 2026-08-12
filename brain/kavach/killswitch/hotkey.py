"""Global hotkey surface — the §7 "one keyboard shortcut".

Default chord: **⌃⌥⌘K** (control-option-command-K). Three modifiers is
deliberate. A global monitor is passive — it cannot swallow the event — so a
chord that collides with an app shortcut would fire *both*. ⌃⌥⌘K is
effectively unclaimed.

Built on ``NSEvent.addGlobalMonitorForEventsMatchingMask_handler_`` rather than
pynput, which silently drops ctrl/alt hotkeys on macOS
(moses-palmer/pynput#297). Silent failure is the one thing a kill switch may
never do.

macOS gates global key monitoring behind **Input Monitoring**
(``kTCCServiceListenEvent``). ``CGPreflightListenEventAccess()`` reports
whether it has been granted, and :func:`self_test` refuses to report success
without it — an ungranted monitor installs cleanly and then never fires, which
would leave you believing you had a kill switch when you did not.
"""

from __future__ import annotations

import logging
from typing import Callable

import Cocoa
import Quartz

log = logging.getLogger("kavach.killswitch.hotkey")

# ⌃⌥⌘K
DEFAULT_MODIFIERS = (
    Cocoa.NSEventModifierFlagControl
    | Cocoa.NSEventModifierFlagOption
    | Cocoa.NSEventModifierFlagCommand
)
DEFAULT_KEY = "k"

# Only these bits count when comparing; the rest (caps lock, numeric pad,
# function) vary with keyboard state and would break an equality check.
_MODIFIER_MASK = (
    Cocoa.NSEventModifierFlagControl
    | Cocoa.NSEventModifierFlagOption
    | Cocoa.NSEventModifierFlagCommand
    | Cocoa.NSEventModifierFlagShift
)


def describe(modifiers: int = DEFAULT_MODIFIERS, key: str = DEFAULT_KEY) -> str:
    parts = []
    if modifiers & Cocoa.NSEventModifierFlagControl:
        parts.append("⌃")
    if modifiers & Cocoa.NSEventModifierFlagOption:
        parts.append("⌥")
    if modifiers & Cocoa.NSEventModifierFlagShift:
        parts.append("⇧")
    if modifiers & Cocoa.NSEventModifierFlagCommand:
        parts.append("⌘")
    return "".join(parts) + key.upper()


def has_input_monitoring() -> bool:
    """True if this process may observe global key events."""
    return bool(Quartz.CGPreflightListenEventAccess())


def request_input_monitoring() -> bool:
    """Ask macOS to prompt for Input Monitoring. The grant only takes effect
    after the process restarts, which is a macOS constraint, not ours."""
    return bool(Quartz.CGRequestListenEventAccess())


def self_test() -> tuple[bool, str]:
    """Report whether the hotkey can actually work right now.

    Returns ``(ok, message)``. Callers must surface the message loudly when
    ``ok`` is False rather than starting up quietly.
    """
    if has_input_monitoring():
        return True, f"Input Monitoring granted — {describe()} is live."
    return False, (
        "Input Monitoring is NOT granted, so the global hotkey will never "
        "fire.\n"
        "  Grant it: System Settings → Privacy & Security → Input Monitoring\n"
        "  Then restart the daemon (macOS only applies the grant on restart).\n"
        "  Meanwhile `kavach kill` over the socket still works."
    )


class HotkeyListener:
    """Installs a passive global monitor and calls ``on_trigger`` on the chord.

    The callback runs on the AppKit main thread, not the asyncio loop — which
    is exactly why ``KillSwitch.trigger`` is thread-safe.
    """

    def __init__(
        self,
        on_trigger: Callable[[], None],
        modifiers: int = DEFAULT_MODIFIERS,
        key: str = DEFAULT_KEY,
    ) -> None:
        self.on_trigger = on_trigger
        self.modifiers = modifiers
        self.key = key.lower()
        self._monitor = None

    def _matches(self, event) -> bool:
        if (event.modifierFlags() & _MODIFIER_MASK) != self.modifiers:
            return False
        chars = event.charactersIgnoringModifiers()
        return bool(chars) and chars.lower() == self.key

    def start(self) -> bool:
        """Install the monitor. Returns False if Input Monitoring is missing."""
        ok, message = self_test()
        if not ok:
            log.warning(message)

        def handler(event) -> None:
            try:
                if self._matches(event):
                    log.warning("HOTKEY %s — triggering kill switch", describe(
                        self.modifiers, self.key))
                    self.on_trigger()
            except Exception:
                # Never let an exception escape into the AppKit event loop;
                # a dead monitor is a dead kill switch.
                log.exception("hotkey handler failed")

        self._monitor = Cocoa.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            Cocoa.NSEventMaskKeyDown, handler
        )
        return ok

    def stop(self) -> None:
        if self._monitor is not None:
            Cocoa.NSEvent.removeMonitor_(self._monitor)
            self._monitor = None
