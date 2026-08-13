"""`kavach-overlay` — run the orb as a floating desktop presence.

    uv run kavach-overlay

Sits invisible until KAVACH starts listening, then fades in above whatever you
are doing and fades out when the turn ends.

Three ways to control it, because they suit different moments:

    🛡 menu bar        sizes, minimise, move/resize, quit
    ⌃⌥⌘Space          talk to KAVACH
    ⌃⌥⌘F              full screen
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
from pathlib import Path

import AppKit
import Foundation

from .controls import MenuBarController
from .overlay import LINGER_SECONDS, BridgeListener, OverlayWindow

RULE = "─" * 62

#: Virtual key codes, which do not shift with layout or modifiers.
KEY_SPACE = 49
KEY_F = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KAVACH desktop orb overlay.")
    parser.add_argument("--url", default="http://127.0.0.1:3100/?overlay=1",
                        help="where the orb is served (needs `next start`)")
    parser.add_argument("--bridge", default="ws://127.0.0.1:8765",
                        help="agent-state bridge")
    parser.add_argument("--size", type=float, default=None,
                        help="override the remembered size, in points")
    parser.add_argument("--no-gestures", action="store_true",
                        help="skip hand tracking and the camera prompt")
    parser.add_argument("--fullscreen", action="store_true",
                        help="start filling the display (⌃⌥⌘F toggles it)")
    parser.add_argument("--always", action="store_true",
                        help="stay visible even when idle (for demos)")
    args = parser.parse_args(argv)

    # One panel, enforced by a file rather than by remembering to pkill.
    #
    # Four of these were once running at once, drawing two panels on top of
    # each other, because `pkill -f` missed some. Process-name matching is what
    # keeps failing here; a lock the process takes itself does not care what
    # the command line looks like. A stale lock is still taken over, so a hard
    # kill cannot leave the panel unstartable.
    from ..single import InstanceLock

    panel_lock = InstanceLock("overlay")
    if not panel_lock.acquire():
        print(f"✗ another KAVACH overlay is already running "
              f"({panel_lock.describe_holder()})", file=sys.stderr)
        print("  quit it from the 🛡 menu, or kill that pid, then retry.",
              file=sys.stderr)
        return 1

    # Also to a file. Launched through KAVACH.app by Launch Services — which
    # is what makes the bundle its own responsible process for the camera —
    # stdout goes nowhere, and "did it get the camera?" then has no answer.
    _log_path = Path.home() / ".kavach" / "logs" / "overlay.log"
    _log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(_log_path)],
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

    tracker = None

    def on_quit() -> None:
        if tracker is not None:
            tracker.stop()
        listener.stop()
        try:
            panel_lock.release()
        except Exception:
            log.debug("could not release the overlay lock", exc_info=True)
        app.terminate_(None)

    listener = BridgeListener(overlay, args.bridge)
    listener.start()

    # ——— hand tracking ———
    #
    # Here rather than in the voice loop, because the camera prompt is UI and
    # only an NSApplication can raise it. A plain CLI process asks and nothing
    # appears — the request returns "not yet asked" forever, which reads as a
    # broken camera. Gestures go to the brain over the same bridge.
    # §14. The camera lives in THIS process, not the voice loop — so ghost
    # mode reaches it here, over the same snapshot stream the menu bar uses.
    #
    # Found in live testing, not in the tests: the voice loop's ghost stopped
    # the mic and reported `stopped: ["mic"]` with no camera in the list,
    # because there was no tracker in that process to stop. The unit test
    # passed because it attached a fake tracker in-process. A ghost mode that
    # leaves the camera running is exactly the lie this phase is about.
    from ..privacy.camera_gate import CameraGate

    camera_gate = CameraGate()

    if not args.no_gestures:
        from ..gestures.permission import camera_status, request_camera
        from ..gestures.tracker import HandTracker

        if request_camera(timeout=45):
            def on_gesture(event) -> None:
                # Logged so gestures can be verified without guessing: hold one
                # at the camera and watch ~/.kavach/logs/overlay.out. Progress
                # is reported as it builds, so a hold that never completes is
                # visibly different from one the camera never saw.
                if event.gesture.value != "none":
                    log.info("gesture %s %.0f%%%s", event.gesture.value,
                             event.progress * 100,
                             "  ← FIRED" if event.fired else "")
                if event.fired:
                    listener.send({
                        "cmd": "gesture",
                        "gesture": event.gesture.value,
                    })
                overlay.pending_gesture = (event.gesture.value, event.progress)

            # Rebuilt rather than resumed when ghost mode ends: HandTracker is
            # a Thread, and a stopped thread cannot be restarted.
            def make_tracker():
                t = HandTracker(on_event=on_gesture)
                t.start()
                return t

            camera_gate.make_tracker = make_tracker
            camera_gate.start()
        else:
            log.warning("gestures off — no camera access")

    # The menubar's ghost toggle needs a way to reach the brain. The listener
    # already owns that channel, so it is handed over rather than a second
    # websocket client being opened here.
    overlay.send_command = listener.send

    controller = MenuBarController.alloc().initWithOverlay_onQuit_(overlay, on_quit)
    # Keep the menu tick honest when move/resize times out on its own.
    overlay._on_interactive_change = controller.refresh
    # §17. Called on the main thread from overlay.tick() with each snapshot,
    # which is the only place it is safe to touch the status item from.
    def on_snapshot(snapshot: dict) -> None:
        controller.apply_snapshot(snapshot)

        camera_gate.apply(bool(snapshot.get("ghost")))

    overlay.on_snapshot = on_snapshot

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
            if code == KEY_F:
                overlay.toggle_fullscreen()
                controller.refresh()
                return
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
    print("  controls   KAVACH menu bar · ⌘-drag to move · ⌃⌥⌘H minimise")
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

    if args.fullscreen:
        # After the page exists: the class is set on a live DOM, and setting it
        # before the first paint would be dropped by the load that follows.
        def go_fullscreen(_timer) -> None:
            # Wrapped, because anything raised inside a pyobjc block is
            # swallowed whole: the first version of this simply never ran and
            # left no trace at all in the log.
            try:
                overlay.toggle_fullscreen()
            except Exception:
                log.exception("could not enter full screen")

        Foundation.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            4.0, False, go_fullscreen
        )

    signal.signal(signal.SIGINT, lambda *_: app.terminate_(None))
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
