"""The kill-switch daemon — runs all four trigger surfaces at once.

Two run loops have to coexist: AppKit's (for the hotkey monitor and the menu
bar item, both of which must own the main thread) and asyncio's (for the
control socket). The socket loop therefore runs on a background thread, and
``KillSwitch`` is thread-safe precisely so a hotkey firing on the AppKit thread
can cancel a task owned by the asyncio thread.

    uv run python -m kavach.killswitch.daemon

Headless mode (``--no-gui``) runs the socket alone, which is what tests and
CI use — and what still works when Input Monitoring has not been granted.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import threading
from pathlib import Path

from .core import KillSwitch
from .ipc import DEFAULT_SOCKET_PATH, serve
from .log import DEFAULT_LOG_PATH, ActionLog

log = logging.getLogger("kavach.killswitch.daemon")


class SocketThread:
    """Runs the asyncio control socket on its own thread."""

    def __init__(self, ks: KillSwitch, socket_path: Path) -> None:
        self.ks = ks
        self.socket_path = socket_path
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="kavach-socket",
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()
        if not self.ready.wait(timeout=10):
            raise RuntimeError("control socket failed to start within 10s")
        if self.error is not None:
            raise self.error

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            server = self.loop.run_until_complete(serve(self.ks, self.socket_path))
        except BaseException as exc:
            self.error = exc
            self.ready.set()
            return

        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            server.close()
            self.loop.run_until_complete(server.wait_closed())
            self.loop.close()

    def stop(self) -> None:
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)


def _banner(ks: KillSwitch, socket_path: Path, hotkey_ok: bool,
            hotkey_msg: str, gui: bool) -> None:
    from . import hotkey as hotkey_mod

    print("─" * 62)
    print("  KAVACH kill switch — armed")
    print("─" * 62)
    print(f"  socket    {socket_path}")
    print(f"  log       {ks.log.path}")
    print(f"  CLI       kavach kill | kavach status | kavach rearm")
    if gui:
        state = "LIVE" if hotkey_ok else "NOT WORKING"
        print(f"  hotkey    {hotkey_mod.describe()}  [{state}]")
        print(f"  menubar   🛡 armed / ⛔ disarmed")
    else:
        print("  hotkey    disabled (--no-gui)")
        print("  menubar   disabled (--no-gui)")
    print("─" * 62)
    if gui and not hotkey_ok:
        print(hotkey_msg)
        print("─" * 62)
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kavach-daemon", description="KAVACH kill-switch daemon (spec §7)."
    )
    parser.add_argument("--socket", default=str(DEFAULT_SOCKET_PATH))
    parser.add_argument("--log", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--no-gui", action="store_true",
                        help="socket only; no hotkey or menu bar")
    parser.add_argument("--request-permission", action="store_true",
                        help="prompt for Input Monitoring, then exit")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    ks = KillSwitch(log=ActionLog(args.log))
    socket_path = Path(args.socket)

    if args.request_permission:
        from . import hotkey as hotkey_mod
        granted = hotkey_mod.request_input_monitoring()
        print(f"Input Monitoring granted: {granted}")
        print("If a dialog appeared, approve it and restart the daemon.")
        return 0 if granted else 1

    socket_thread = SocketThread(ks, socket_path)
    socket_thread.start()
    ks.log.append("daemon.start", socket=str(socket_path), gui=not args.no_gui)

    if args.no_gui:
        _banner(ks, socket_path, False, "", gui=False)
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        try:
            stop.wait()
        finally:
            socket_thread.stop()
            ks.log.append("daemon.stop", reason="signal")
        return 0

    # --- GUI mode: AppKit owns the main thread ---------------------------
    import Cocoa

    from . import hotkey as hotkey_mod
    from .menubar import MenuBarController

    app = Cocoa.NSApplication.sharedApplication()
    # Accessory: menu bar item, no Dock icon, no app switcher entry.
    app.setActivationPolicy_(Cocoa.NSApplicationActivationPolicyAccessory)

    def on_quit() -> None:
        socket_thread.stop()
        ks.log.append("daemon.stop", reason="menubar quit")
        app.terminate_(None)

    controller = MenuBarController.alloc().init().configureWithSwitch_onQuit_(
        ks, on_quit
    )

    def on_hotkey() -> None:
        ks.trigger(source="hotkey", reason=hotkey_mod.describe())
        controller.refresh()

    listener = hotkey_mod.HotkeyListener(on_hotkey)
    hotkey_ok = listener.start()
    hotkey_msg = hotkey_mod.self_test()[1]

    if not hotkey_ok:
        ks.log.append("daemon.warning", warning="input_monitoring_not_granted")

    _banner(ks, socket_path, hotkey_ok, hotkey_msg, gui=True)

    # Keep the menu bar title honest when a kill arrives over the socket
    # rather than through the GUI.
    def poll_state(timer) -> None:
        controller.refresh()

    Cocoa.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.5, True, poll_state)

    try:
        app.run()
    finally:
        listener.stop()
        socket_thread.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
