"""Menu bar surface — a visible PANIC item and a live state readout.

Built directly on ``NSStatusItem`` rather than ``rumps``, whose last release
was October 2022. The status title doubles as an always-visible indicator of
whether KAVACH is armed, which matters when demoing: the audience can see the
switch, not just hear about it.
"""

from __future__ import annotations

import logging
from typing import Callable

import Cocoa
import objc

from .core import KillSwitch, State

log = logging.getLogger("kavach.killswitch.menubar")

ARMED_TITLE = "🛡"
DISARMED_TITLE = "⛔"


class MenuBarController(Cocoa.NSObject):
    """NSObject subclass so AppKit can target its selectors.

    Built with ``alloc().init()`` and configured afterwards — ObjC init
    conventions make custom initialisers with Python arguments more trouble
    than they are worth here.
    """

    def configureWithSwitch_onQuit_(self, ks: KillSwitch, on_quit: Callable[[], None]):
        self.ks = ks
        self.on_quit = on_quit

        bar = Cocoa.NSStatusBar.systemStatusBar()
        # Strong reference required: a released status item vanishes silently.
        self.item = bar.statusItemWithLength_(Cocoa.NSVariableStatusItemLength)

        menu = Cocoa.NSMenu.alloc().init()
        self._add(menu, "PANIC — Halt Everything", "panic:")
        menu.addItem_(Cocoa.NSMenuItem.separatorItem())
        self._add(menu, "Re-arm", "rearm:")
        self._add(menu, "Show Status", "showStatus:")
        menu.addItem_(Cocoa.NSMenuItem.separatorItem())
        self._add(menu, "Quit KAVACH daemon", "quit:")

        self.item.setMenu_(menu)
        self.refresh()
        return self

    @objc.python_method
    def _add(self, menu, title: str, selector: str) -> None:
        # pyobjc converts the plain string to a SEL for action arguments, and
        # a `def panic_` on an NSObject subclass is already exposed as `panic:`.
        menu_item = Cocoa.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            title, selector, ""
        )
        menu_item.setTarget_(self)
        menu.addItem_(menu_item)

    @objc.python_method
    def refresh(self) -> None:
        armed = self.ks.state is State.ARMED
        button = self.item.button()
        if button is not None:
            button.setTitle_(ARMED_TITLE if armed else DISARMED_TITLE)
            button.setToolTip_(
                "KAVACH: ARMED" if armed
                else "KAVACH: DISARMED — latched. Re-arm from this menu."
            )

    # --- menu actions -----------------------------------------------------

    def panic_(self, sender) -> None:
        log.warning("MENUBAR PANIC — triggering kill switch")
        self.ks.trigger(source="menubar", reason="panic menu item")
        self.refresh()

    def rearm_(self, sender) -> None:
        self.ks.rearm(source="menubar")
        self.refresh()

    def showStatus_(self, sender) -> None:
        status = self.ks.status()
        alert = Cocoa.NSAlert.alloc().init()
        alert.setMessageText_(f"KAVACH — {status['state'].upper()}")
        alert.setInformativeText_(
            f"In-flight tasks: {status['in_flight_tasks']}\n"
            f"Live processes:  {status['live_processes']}\n"
            f"Action log:      {status['log_path']}"
        )
        alert.runModal()

    def quit_(self, sender) -> None:
        # Halt before exiting: leaving MCP subprocesses running after the
        # supervisor quits is exactly the orphan case §7 is about.
        self.ks.trigger(source="menubar", reason="daemon quit")
        self.on_quit()
