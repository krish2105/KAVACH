"""Live kill-switch demonstration — all four trigger surfaces at once.

    uv run python -m kavach.killswitch.demo

Starts the daemon with a real workload attached: a real child process and a
real async task, both registered with the kill switch. Then kill it any of
four ways and watch both actually die:

    ⌃⌥⌘K                    global hotkey
    menu bar 🛡 → PANIC      menu item
    kavach kill             from any other terminal
    the Unix socket         directly

This is a thin wrapper over ``daemon --demo`` rather than a second
implementation. An earlier version stood up its own event loop and socket but
no hotkey or menu bar, so pressing ⌃⌥⌘K did nothing — the demo has to exercise
the same wiring the real daemon uses, or it is not evidence of anything.
"""

from __future__ import annotations

import sys

from .daemon import main as daemon_main

if __name__ == "__main__":
    raise SystemExit(daemon_main(["--demo", *sys.argv[1:]]))
