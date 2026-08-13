"""Serve the orb's page from the process that shows it.

## The failure this removes

The Presence layer is a WKWebView pointed at `http://127.0.0.1:3100`. That URL
was served by a `next start` somebody had typed into a terminal, and by nothing
else. Close the terminal and the panel became an empty transparent window: no
error, no dialog, no log line — indistinguishable from the orb sitting quietly
idle. `doctor.py` carried the workaround as standing advice to the human:
"start `npx next start -p 3100` in apps/orb".

## Why not a launch agent

That was the obvious fix and it cannot work on this machine. **The project
lives in `~/Desktop`, which is TCC-protected**, and a LaunchAgent has no grant
for it:

    $ launchctl bootstrap gui/$UID <trivial job that cats a file>
    DESKTOP DENIED
    HOME READABLE

Node does not fail on that denial — it *hangs*. Sampled after two minutes, the
process was blocked in a single syscall:

    GetNearestParentPackageJSONType → TraverseParent → ReadFileSync
      → uv_fs_open → open  (100% of samples)

It is walking up parent directories looking for `package.json`, hits the
protected folder, and waits on a consent prompt that a background agent can
never display. Zero bytes of output, port never bound, launchd reporting the
job as healthy and "running". The worst possible shape for a failure.

Granting Full Disk Access to the node binary would also fix it, but it is a
large grant aimed at the wrong thing, and it breaks on the next nvm upgrade
because the path carries the version number.

## Why the overlay is the right owner

This process is `KAVACH.app`, launched by Launch Services, and it already reads
the project — every line of `kavach` it is running came from that same Desktop
path. A child it spawns inherits that, so the server starts where the agent
could not.

It also makes the original failure structurally impossible rather than merely
unlikely: the page is served **exactly when there is a window to show it in**.
No ordering, no dependency between agents, nothing to install separately, and
nothing left running afterwards to be confused by.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("kavach.presence.pageserver")

#: Long enough for a cold `next start` on this machine (measured: ready in
#: ~200ms warm, a few seconds cold), short enough that a genuine hang is
#: reported rather than waited on forever.
START_TIMEOUT = 45.0

#: A crash loop must not spin the CPU, and must not hammer the log.
RESTART_DELAY = 3.0

#: Give up after this many restarts in a row and say so. A server that cannot
#: stay up is a fact worth stating once, not every three seconds.
MAX_RESTARTS = 5


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((host, port)) == 0


def find_node() -> Path | None:
    """Node >= 24, by absolute path.

    The project is pinned to 24 (`.nvmrc`) while the user's global nvm default
    stays on 20, so resolving `node` from PATH can silently pick the wrong one.
    """
    versions = Path.home() / ".nvm" / "versions" / "node"
    found = sorted(
        (p for p in versions.glob("v*/bin/node") if p.is_file()),
        key=lambda p: [int(n) for n in p.parents[1].name.lstrip("v").split(".")],
        reverse=True,
    )
    for node in found:
        if int(node.parents[1].name.lstrip("v").split(".")[0]) >= 24:
            return node

    which = shutil.which("node")
    return Path(which) if which else None


class PageServer:
    """Keeps `next start` alive for as long as the orb is on screen.

    Does nothing at all if the port is already answering — a dev server the
    user started by hand must keep working, and starting a second one would
    just fail to bind and look like a crash.
    """

    def __init__(self, orb_dir: Path, port: int = 3100,
                 log_path: Path | None = None):
        self.orb_dir = Path(orb_dir)
        self.port = port
        self.log_path = log_path or (Path.home() / ".kavach" / "logs"
                                     / "orb-server.log")
        self.process: subprocess.Popen | None = None
        self.adopted = False       # someone else's server; not ours to manage
        self._stop = threading.Event()
        self._restarts = 0

    # ——— starting ———

    def start(self) -> bool:
        """Returns True once something is serving the page."""
        if port_open(self.port):
            self.adopted = True
            log.info(":%d already served — leaving it alone", self.port)
            return True

        node = find_node()
        if node is None:
            log.error("no node found; the orb page cannot be served")
            return False

        cli = self.orb_dir / "node_modules" / "next" / "dist" / "bin" / "next"
        if not cli.exists():
            log.error("next not installed at %s — run npm install", cli)
            return False
        if not (self.orb_dir / ".next").exists():
            log.error("no production build in %s — run `next build`",
                      self.orb_dir)
            return False

        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.log_path.open("a")

        self.process = subprocess.Popen(
            [str(node), str(cli), "start", "-p", str(self.port)],
            cwd=str(self.orb_dir),
            stdout=handle, stderr=handle,
            # Its own group, so stopping the orb stops the server rather than
            # leaving an orphan holding the port that the next launch then
            # "adopts" and never restarts.
            start_new_session=True,
            env={**os.environ, "NODE_ENV": "production",
                 "HOSTNAME": "127.0.0.1"},
        )
        atexit.register(self.stop)
        log.info("serving the orb page (pid %d)", self.process.pid)

        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            if port_open(self.port):
                log.info("orb page ready on :%d", self.port)
                self._supervise()
                return True
            if self.process.poll() is not None:
                log.error("the page server exited at once — see %s",
                          self.log_path)
                return False
            time.sleep(0.25)

        log.error("the page server did not answer in %.0fs — see %s",
                  START_TIMEOUT, self.log_path)
        return False

    # ——— keeping it alive ———

    def _supervise(self) -> None:
        thread = threading.Thread(target=self._watch, name="kavach-pageserver",
                                  daemon=True)
        thread.start()

    def _watch(self) -> None:
        while not self._stop.is_set():
            if self.process is None or self.process.poll() is None:
                self._stop.wait(1.0)
                continue

            if self._restarts >= MAX_RESTARTS:
                log.error("the page server has died %d times; giving up. "
                          "The panel will stay blank until this is fixed — "
                          "see %s", self._restarts, self.log_path)
                return

            self._restarts += 1
            log.warning("page server died; restarting (%d/%d)",
                        self._restarts, MAX_RESTARTS)
            self._stop.wait(RESTART_DELAY)
            if self._stop.is_set():
                return
            self.process = None
            if port_open(self.port):
                continue
            self.start()
            return                 # start() installs a fresh supervisor

    # ——— stopping ———

    def stop(self) -> None:
        self._stop.set()
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        try:
            # The whole group: `next start` spawns workers, and killing only
            # the parent leaves them holding the port.
            os.killpg(os.getpgid(process.pid), 15)
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                log.debug("could not stop the page server", exc_info=True)
        log.info("page server stopped")
