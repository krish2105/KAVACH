"""`kavach-overlay` — run the orb as a floating desktop presence.

    uv run kavach-overlay

Sits invisible until KAVACH starts listening, then fades in above whatever you
are doing and fades out when the turn ends. Never takes focus, never blocks a
click.

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

from .overlay import LINGER_SECONDS, BridgeListener, OverlayWindow

RULE = "─" * 62


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KAVACH desktop orb overlay.")
    parser.add_argument("--url", default="http://127.0.0.1:3100/?overlay=1",
                        help="where the orb is served")
    parser.add_argument("--bridge", default="ws://127.0.0.1:8765",
                        help="agent-state bridge")
    parser.add_argument("--size", type=float, default=340.0)
    parser.add_argument("--always", action="store_true",
                        help="stay visible even when idle (for demos)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    log = logging.getLogger("kavach.presence")

    app = AppKit.NSApplication.sharedApplication()
    # Accessory: no Dock icon, no menu bar takeover. It is a presence, not an
    # app you switch to.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    overlay = OverlayWindow(args.url, size=args.size)
    if args.always:
        overlay.show()

    listener = BridgeListener(overlay, args.bridge)
    listener.start()

    print(RULE)
    print("  KAVACH desktop orb")
    print(RULE)
    print(f"  orb        {args.url}")
    print(f"  bridge     {args.bridge}")
    print(f"  behaviour  hidden while idle, fades in when listening")
    print(f"             lingers {LINGER_SECONDS}s after a turn ends")
    print("  never takes focus · never blocks clicks · follows you across Spaces")
    print(RULE)
    print("  Ctrl-C to stop")
    sys.stdout.flush()

    # Drive the linger timer from the AppKit run loop.
    #
    # Anything raised inside a pyobjc block is swallowed and can tear down the
    # run loop, which presents as the process exiting cleanly with no
    # traceback. Catching here keeps a bad frame from taking the whole
    # presence down, and makes the cause visible.
    def tick(_timer) -> None:
        try:
            overlay.tick()
        except Exception:
            log.exception("overlay tick failed")

    Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.25, True, tick)

    # One-shot probe a few seconds in, once the page has settled.
    Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        6.0, False, lambda _t: overlay.probe()
    )

    signal.signal(signal.SIGINT, lambda *_: app.terminate_(None))
    app.run()
    log.warning("AppKit run loop RETURNED — process will now exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
