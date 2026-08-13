"""The orb's page server — the thing whose absence made the panel blank.

The panel is a WKWebView pointed at 127.0.0.1:3100. Nothing served that URL
except a `next start` somebody had typed into a terminal, so closing the
terminal turned the orb into an empty transparent window: no error, no dialog,
nothing to click, indistinguishable from an orb sitting idle.

A launch agent was the obvious answer and cannot work on this machine — the
project is under ~/Desktop, which is TCC-protected, and a launchd job reading
it *hangs* in open() rather than failing. So the process that shows the page
serves it too.

Nothing here starts a real server: `next start` takes seconds and binds a real
port, and a suite that does that is a suite people stop running.
"""

import socket
import subprocess
from pathlib import Path

import pytest

from kavach.presence import pageserver
from kavach.presence.pageserver import PageServer

ORB = Path(__file__).resolve().parents[2] / "apps" / "orb"


@pytest.fixture
def server(tmp_path):
    return PageServer(ORB, port=39100, log_path=tmp_path / "orb-server.log")


class FakePopen:
    """Stands in for `next start`. Records rather than runs."""

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.pid = 4242
        self._exit = None

    def poll(self):
        return self._exit

    def wait(self, timeout=None):
        return self._exit


# ═══ it does not fight a server that is already there ═══

def test_an_existing_server_is_left_alone(server, monkeypatch):
    """A dev server the user started by hand must keep working.

    Starting a second one would fail to bind and read as a crash — and killing
    theirs would be worse.
    """
    monkeypatch.setattr(pageserver, "port_open", lambda *a, **k: True)
    started = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: started.append(a) or FakePopen())

    assert server.start() is True
    assert started == [], "started a second server over a working one"
    assert server.adopted is True


def test_an_adopted_server_is_not_killed_on_stop(server, monkeypatch):
    monkeypatch.setattr(pageserver, "port_open", lambda *a, **k: True)
    server.start()

    server.stop()  # must not raise, and has nothing of its own to stop

    assert server.process is None


# ═══ what it launches ═══

def test_it_runs_the_project_node_not_whatever_is_on_path(server, monkeypatch):
    """`.nvmrc` pins 24 while the user's global nvm default stays on 20.

    Resolving `node` from PATH silently picks the wrong one, and launchd hands
    a process almost no PATH at all.
    """
    node = pageserver.find_node()
    if node is None:
        pytest.skip("no node on this machine")

    version = subprocess.run([str(node), "--version"],
                             capture_output=True, text=True).stdout.strip()

    assert node.is_absolute()
    assert int(version.lstrip("v").split(".")[0]) >= 24, \
        f"resolved {version}; this project needs 24"


def test_it_starts_next_in_production_mode(server, monkeypatch):
    """`next dev` cannot work here: its HMR websocket fails inside WKWebView,
    React never hydrates, and the panel renders server HTML with no orb —
    which looks like nothing being wrong at all."""
    monkeypatch.setattr(pageserver, "port_open",
                        lambda *a, **k: len(calls) > 0)
    calls = []

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        return FakePopen()

    monkeypatch.setattr(subprocess, "Popen", record)
    server.start()

    argv, kwargs = calls[0]
    assert "start" in argv[0], "not `next start`"
    assert "dev" not in argv[0]
    assert kwargs["env"]["NODE_ENV"] == "production"


def test_it_serves_the_port_the_overlay_reads(server, monkeypatch):
    """The silent drift: change one and the panel goes blank with no error."""
    source = (Path(__file__).resolve().parents[1] / "kavach" / "presence"
              / "__main__.py").read_text()

    assert "127.0.0.1:3100" in source
    # And the server takes its port from that URL rather than repeating it.
    assert "urlparse(args.url).port" in source, \
        "the port is written twice and will drift"


def test_it_runs_in_its_own_process_group(server, monkeypatch):
    """Otherwise `next start`'s workers outlive the orb and hold the port —
    and the next launch adopts an orphan it cannot supervise."""
    monkeypatch.setattr(pageserver, "port_open", lambda *a, **k: len(calls) > 0)
    calls = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: calls.append(k) or FakePopen())

    server.start()

    assert calls[0]["start_new_session"] is True


# ═══ it says so when it cannot ═══

def test_a_missing_build_is_reported_not_guessed(tmp_path, monkeypatch, caplog):
    """`next start` with no build exits immediately, which looked exactly like
    every other cause of a blank panel."""
    monkeypatch.setattr(pageserver, "port_open", lambda *a, **k: False)
    empty = tmp_path / "orb"
    (empty / "node_modules" / "next" / "dist" / "bin").mkdir(parents=True)
    (empty / "node_modules" / "next" / "dist" / "bin" / "next").touch()

    server = PageServer(empty, port=39101, log_path=tmp_path / "log")
    assert server.start() is False
    assert "build" in caplog.text.lower()


def test_a_missing_next_is_reported(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(pageserver, "port_open", lambda *a, **k: False)
    empty = tmp_path / "orb"
    empty.mkdir()

    server = PageServer(empty, port=39102, log_path=tmp_path / "log")

    assert server.start() is False
    assert "npm install" in caplog.text


def test_the_port_probe_does_not_hang(server):
    """It runs before the window appears, so a slow probe is a slow launch."""
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]

        assert pageserver.port_open(port) is True

    assert pageserver.port_open(port) is False
