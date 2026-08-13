"""`kavach-overlay` — run the orb as a floating desktop presence.

    uv run kavach-overlay

Sits invisible until KAVACH starts listening, then fades in above whatever you
are doing and fades out when the turn ends.

Three ways to control it, because they suit different moments:

    🛡 menu bar        sizes, minimise, move/resize, quit
    ⌃⌥⌘Space          talk to KAVACH
    ⌃⌥⌘M              toggle resize
    ⌃⌥⌘H              minimise / restore
    ⌃⌥⌘= / ⌃⌥⌘-       step size up / down

Click-through is the default, and the menu and hotkeys preserve it. Only
move/resize gives it up, and only while it is switched on.

**Requires `next start`, not `next dev`.** Next's dev server depends on an HMR
websocket that fails inside WKWebView, and React then never hydrates — the
panel renders server HTML with no orb and no state, and nothing about it looks
wrong from outside.

Pair it with the voice loop:

    uv run python -m kavach.voice        # in one terminal
    uv run kavach-overlay                # in another
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

import AppKit
import Foundation

from .controls import MenuBarController
from .overlay import LINGER_SECONDS, BridgeListener, OverlayWindow

RULE = "─" * 62

#: Virtual key codes, which do not shift with layout or modifiers.
KEY_SPACE = 49


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KAVACH desktop orb overlay.")
    parser.add_argument("--url", default="http://127.0.0.1:3100/?overlay=1",
                        help="where the orb is served (needs `next start`)")
    parser.add_argument("--bridge", default="ws://127.0.0.1:8765",
                        help="agent-state bridge")
    parser.add_argument("--size", type=float, default=None,
                        help="override the remembered size, in points")
    parser.add_argument("--always", action="store_true",
                        help="stay visible even when idle (for demos)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("kavach.presence")

    app = AppKit.NSApplication.sharedApplication()
    # Accessory: no Dock icon, no app switcher entry. It is a presence, not an
    # app you switch to.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    overlay = OverlayWindow(args.url, size=args.size)
    # Restore move/resize if it was left on.
    if overlay.geometry.interactive:
        overlay.set_interactive(True)
    if args.always:
        overlay.show()

    listener = BridgeListener(overlay, args.bridge)
    listener.start()

    def on_quit() -> None:
        listener.stop()
        app.terminate_(None)

    controller = MenuBarController.alloc().initWithOverlay_onQuit_(overlay, on_quit)
    # Keep the menu tick honest when move/resize times out on its own.
    overlay._on_interactive_change = controller.refresh

    # ——— global hotkeys ———
    # Same mechanism as the kill switch: a global monitor only ever sees keys
    # dispatched to *other* applications, which is all this process gets.
    MODIFIERS = (
        AppKit.NSEventModifierFlagControl
        | AppKit.NSEventModifierFlagOption
        | AppKit.NSEventModifierFlagCommand
    )

    def on_key(event) -> None:
        try:
            if (event.modifierFlags() & MODIFIERS) != MODIFIERS:
                return
            # keyCode, not characters. With Control held,
            # charactersIgnoringModifiers is unreliable, and ⌃Space is macOS's
            # own input-source switcher — so the chord may be reshaped or eaten
            # before it arrives. Key codes are layout- and modifier-independent.
            code = event.keyCode()
            log.debug("chord: keyCode=%s chars=%r", code, event.characters())
            if code == KEY_SPACE:
                # Talk. The panel never takes focus, so the page cannot hear a
                # key — this is the only way to start a turn while looking at
                # the orb rather than at a browser window.
                overlay.show()
                if listener.send({"cmd": "talk"}):
                    log.info("talk requested")
                return
            key = (event.charactersIgnoringModifiers() or "").lower()
            if key == "m":
                overlay.set_interactive(not overlay.interactive)
            elif key == "h":
                overlay.set_pinned_hidden(not overlay.geometry.hidden)
                if not overlay.geometry.hidden:
                    overlay.show()
            elif key in ("=", "+"):
                overlay.geometry.step_size(+1)
                overlay.set_size(overlay.geometry.size)
            elif key in ("-", "_"):
                overlay.geometry.step_size(-1)
                overlay.set_size(overlay.geometry.size)
            else:
                return
            controller.refresh()
        except Exception:
            # A bad key event must never take the presence down with it.
            log.exception("hotkey handler failed")

    AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
        AppKit.NSEventMaskKeyDown, on_key
    )

    import Quartz

    can_listen = bool(Quartz.CGPreflightListenEventAccess())
    if not can_listen:
        log.warning(
            "no Input Monitoring for this process — global hotkeys will "
            "silently do nothing. System Settings → Privacy & Security → "
            "Input Monitoring. Requesting now…"
        )
        Quartz.CGRequestListenEventAccess()

    print(RULE)
    print("  KAVACH desktop orb")
    print(RULE)
    print(f"  orb        {args.url}")
    print(f"  bridge     {args.bridge}")
    print(f"  size       {overlay.geometry.size:.0f}pt"
          f"{'  (minimised)' if overlay.geometry.hidden else ''}")
    print("  behaviour  hidden while idle, fades in when listening,")
    print(f"             lingers {LINGER_SECONDS}s after a turn")
    print(f"  hotkeys    {'LIVE' if can_listen else 'BLOCKED — grant Input Monitoring'}")
    print("  talk       ⌃⌥⌘Space   (Space alone cannot reach a panel that\n                          never takes focus)")
    print("  controls   🛡 menu bar · ⌘-drag to move · ⌃⌥⌘H minimise")
    print("             ⌃⌥⌘= larger · ⌃⌥⌘- smaller")
    print("  click-through and never takes focus, except in move/resize mode")
    print(RULE)
    print("  Ctrl-C to stop")
    sys.stdout.flush()

    def tick(_timer) -> None:
        # Anything raised inside a pyobjc block is swallowed and can tear down
        # the run loop, which presents as a clean exit with no traceback.
        try:
            overlay.tick()
        except Exception:
            log.exception("overlay tick failed")

    Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.25, True, tick)

    # One-shot probe once the page has settled. The panel has no console and
    # no inspector, so this is the only way to tell a stale bundle from a
    # dropped query string from a styling bug.
    Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        6.0, False, lambda _t: overlay.probe()
    )

    signal.signal(signal.SIGINT, lambda *_: app.terminate_(None))
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
