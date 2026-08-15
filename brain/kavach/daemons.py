"""Render, install and load the launch agents.

## Why this file exists

The templates in `daemon/` carry placeholders — `__UV__`, `__BRAIN__` — and
SETUP.md said to `cp` them into `~/Library/LaunchAgents` **verbatim**. A plist
whose executable is the literal string `__UV__` cannot start anything, so the
installed copies were fixed up by hand, and from then on the file on disk and
the file in git were two different things that nobody diffed.

That divergence is not a tidiness problem. It is how the machine ended up
running an overlay agent that launched the bare CLI — a process with no app
bundle, which macOS refuses the camera to — while the template in the repo said
something else entirely.

So: one renderer, one install path, and tests that read what this produces
rather than what someone typed once.

## What has to be running

    com.krishna.kavach.overlay   opens the window         (else: no orb)
    com.krishna.kavach           the voice loop           (else: no wake word)

There is deliberately **no agent for the orb page**, though its absence was the
original bug. A launchd job cannot read this project: it lives under ~/Desktop,
which is TCC-protected, and node does not fail on that — it hangs in open()
with the port unbound and launchd reporting the job healthy. The page server
runs inside the overlay instead (`presence/pageserver.py`), which is the app
bundle and already has the grant.

## Bootstrap, not load

`launchctl load` is the deprecated interface and reports success for jobs it
did not start. `bootstrap gui/$UID` is the current one and returns a real
error. The difference matters here: a launch agent that silently fails to load
is indistinguishable from one that loaded and did nothing.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("kavach.daemons")

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "daemon"
INSTALLED = Path.home() / "Library" / "LaunchAgents"
LOGS = Path.home() / ".kavach" / "logs"

#: Installed in this order at login. The page server first, so the window has
#: something to load — though the overlay recovers on its own if it loses the
#: race, because `OverlayWindow.probe()` reloads a page that did not render.
AGENTS = (
    "com.krishna.kavach.overlay",
    "com.krishna.kavach",
    # Phases 31-32. Observation only; it holds no device and can reach no
    # action path, which is why it is KeepAlive-true where the voice agent
    # is KeepAlive-on-crash-only.
    "com.krishna.kavach.observe",
)


class DaemonError(RuntimeError):
    """Raised rather than logged: a half-installed agent is worse than none."""


def _node() -> Path:
    """The Node 24 binary, by absolute path.

    launchd has no shell and almost no PATH, and `nvm use` is a shell function
    that does not exist for it. `npx` would resolve whatever node came first on
    that empty PATH — on this machine the global default, which is 20, while
    the project is pinned to 24.
    """
    versions = Path.home() / ".nvm" / "versions" / "node"
    candidates = sorted(
        (p for p in versions.glob("v*/bin/node") if p.is_file()),
        key=lambda p: [int(n) for n in p.parents[1].name.lstrip("v").split(".")],
        reverse=True,
    )
    for node in candidates:
        major = int(node.parents[1].name.lstrip("v").split(".")[0])
        if major >= 24:
            return node

    found = shutil.which("node")
    if found:
        return Path(found)
    raise DaemonError(
        "no Node >= 24 found under ~/.nvm/versions/node. CLAUDE.md pins this "
        "project to 24 — run `nvm install 24`."
    )


def _uv() -> Path:
    found = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(found).exists():
        raise DaemonError("uv not found — the Python agents cannot start.")
    return Path(found)


def substitutions() -> dict[str, str]:
    """Everything the templates need, resolved on this machine."""
    node = _node()
    return {
        "__UV__": str(_uv()),
        "__BRAIN__": str(REPO / "brain"),
        "__ORB__": str(REPO / "apps" / "orb"),
        "__APP__": str(Path.home() / "Applications" / "KAVACH.app"),
        "__NODE__": str(node),
        "__NODEBIN__": str(node.parent),
        "__VENVBIN__": str(REPO / "brain" / ".venv" / "bin"),
        "__LOGS__": str(LOGS),
    }


def render(name: str) -> str:
    """The template with every placeholder resolved."""
    source = TEMPLATES / f"{name}.plist"
    if not source.exists():
        raise DaemonError(f"no template for {name} at {source}")

    text = source.read_text()
    for key, value in substitutions().items():
        text = text.replace(key, value)

    leftover = [w for w in text.split() if w.startswith("__") and w.endswith("__")]
    if leftover:
        # Silent failure otherwise: launchd treats an unresolved placeholder as
        # a path, cannot find it, and the job never runs.
        raise DaemonError(f"{name}: unresolved placeholders {leftover}")
    return text


def install(name: str) -> Path:
    """Write the rendered plist to ~/Library/LaunchAgents."""
    INSTALLED.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)

    text = render(name)
    plistlib.loads(text.encode())  # parse before installing, not after

    target = INSTALLED / f"{name}.plist"
    target.write_text(text)
    log.info("installed %s", target)
    return target


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def bootout(name: str) -> None:
    """Unload from every domain it might be in, tolerating "not loaded".

    Both domains, because an agent loaded years ago with `launchctl load` sits
    in the legacy `user/<uid>` domain and `bootout gui/<uid>/…` does not touch
    it. The symptom is not an error but the *next* bootstrap failing with
    "Bootstrap failed: 5: Input/output error", having just killed the running
    copy — which is how this machine ended up with no overlay at all for a
    minute. `launchctl unload` covers the same ground for very old jobs.
    """
    for domain in (f"gui/{os.getuid()}", f"user/{os.getuid()}"):
        _launchctl("bootout", f"{domain}/{name}")
    _launchctl("unload", str(INSTALLED / f"{name}.plist"))


def bootstrap(name: str) -> None:
    """Load it, and fail loudly if it did not take."""
    target = INSTALLED / f"{name}.plist"
    done = _launchctl("bootstrap", f"gui/{os.getuid()}", str(target))
    if done.returncode != 0:
        message = (done.stderr or done.stdout).strip()
        # 37 is "already bootstrapped"; anything else is real.
        if "already" not in message.lower() and "37" not in message:
            raise DaemonError(f"could not load {name}: {message}")


def is_loaded(name: str) -> bool:
    """Whether launchd knows about it.

    `launchctl print gui/$UID/<label>` is the documented query and it answered
    "Could not find service" for an agent that `launchctl list` showed as
    running with a live PID — the job was bootstrapped into a different domain.
    Both are checked, because trusting the first one produced a confident,
    wrong report that a running agent was not installed.
    """
    if _launchctl("print", f"gui/{os.getuid()}/{name}").returncode == 0:
        return True
    listed = _launchctl("list")
    return any(line.split("\t")[-1] == name
               for line in listed.stdout.splitlines())


def status() -> list[dict]:
    out = []
    for name in AGENTS:
        installed = (INSTALLED / f"{name}.plist").exists()
        stale = False
        if installed:
            try:
                stale = (INSTALLED / f"{name}.plist").read_text() != render(name)
            except DaemonError:
                stale = True
        out.append({
            "name": name,
            "installed": installed,
            "loaded": is_loaded(name),
            # The failure this whole module exists to prevent.
            "stale": stale,
        })
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Install and load the KAVACH launch agents.")
    parser.add_argument("action", nargs="?", default="status",
                        choices=["status", "install", "uninstall"])
    parser.add_argument("--only", default=None, help="just one agent")
    args = parser.parse_args(argv)

    names = [args.only] if args.only else list(AGENTS)

    if args.action == "install":
        for name in names:
            try:
                install(name)
                bootout(name)      # replace a running copy rather than stack
                bootstrap(name)
                print(f"  ✓ {name}")
            except DaemonError as exc:
                print(f"  ✗ {name}: {exc}", file=sys.stderr)
                return 1
        print("\n  Installed. The orb page is now served by launchd, so the")
        print("  panel no longer depends on a terminal staying open.\n")
        return 0

    if args.action == "uninstall":
        for name in names:
            bootout(name)
            (INSTALLED / f"{name}.plist").unlink(missing_ok=True)
            print(f"  removed {name}")
        return 0

    print()
    for row in status():
        marks = []
        marks.append("installed" if row["installed"] else "NOT INSTALLED")
        marks.append("loaded" if row["loaded"] else "not loaded")
        if row["stale"]:
            marks.append("STALE — differs from the template in git")
        print(f"  {row['name']:34} {' · '.join(marks)}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
