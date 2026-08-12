"""Kill switch behaviour tests (spec §7, working agreement §B/§C).

These are written before the implementation exists and must be seen failing
first. Do not edit a test to make it pass — if one looks wrong, flag it.

The contract under test:
  - A fresh switch is ARMED and lets guarded actions run.
  - Triggering halts everything in flight and LATCHES DISARMED.
  - Latched means latched: no auto-recovery, only an explicit rearm().
  - Every trigger leaves an audit record behind.
  - All of the above holds when the trigger arrives from another process.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from kavach.killswitch.core import KillSwitch, KillSwitchDisarmed, State
from kavach.killswitch.log import ActionLog


@pytest.fixture
def log(tmp_path):
    return ActionLog(tmp_path / "actions.jsonl")


@pytest.fixture
def ks(log):
    return KillSwitch(log=log)


@pytest.fixture
def short_tmp_dir():
    """A temp dir short enough for an AF_UNIX path.

    pytest's ``tmp_path`` is far too long for macOS's 104-byte ``sun_path``
    limit. This only changes *where* the socket file lives — no assertion in
    the socket tests below is relaxed by it.
    """
    import shutil
    import tempfile

    path = tempfile.mkdtemp(prefix="/tmp/kv")
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


# --- 1. baseline -----------------------------------------------------------

def test_fresh_switch_is_armed_and_guard_passes(ks):
    assert ks.state is State.ARMED
    assert ks.is_armed
    ks.guard("open Safari")  # must not raise


# --- 2. trigger disarms ----------------------------------------------------

def test_trigger_disarms_and_guard_then_raises(ks):
    ks.trigger(source="test")

    assert ks.state is State.DISARMED
    assert not ks.is_armed
    with pytest.raises(KillSwitchDisarmed):
        ks.guard("open Safari")


# --- 3. in-flight async work is cancelled, fast ----------------------------

async def test_cancels_inflight_task_within_200ms(ks):
    started = asyncio.Event()

    async def long_running_action():
        started.set()
        await asyncio.sleep(300)

    task = asyncio.create_task(long_running_action())
    ks.register_task(task)
    await started.wait()

    t0 = time.perf_counter()
    ks.trigger(source="test")
    await asyncio.wait({task}, timeout=2.0)
    elapsed = time.perf_counter() - t0

    assert task.done(), "in-flight task should not survive the kill switch"
    assert task.cancelled()
    assert elapsed < 0.2, f"kill took {elapsed*1000:.0f}ms, budget is 200ms"


# --- 4. real subprocesses die ----------------------------------------------

def test_kills_registered_subprocess(ks):
    proc = subprocess.Popen(["sleep", "300"])
    ks.register_process(proc)
    assert proc.poll() is None, "sanity: process should be alive before trigger"

    ks.trigger(source="test")

    # Popen.wait() reaps it; if the kill failed this times out and fails.
    proc.wait(timeout=2.0)
    assert proc.poll() is not None, "MCP server subprocess survived the kill switch"


# --- 5. THE IMPORTANT ONE: the latch holds ---------------------------------

def test_latch_holds_no_auto_recovery(ks):
    """An ambiguous state stays stopped. This is the whole safety model."""
    ks.trigger(source="test")

    for attempt in range(5):
        time.sleep(0.02)
        with pytest.raises(KillSwitchDisarmed):
            ks.guard(f"attempt {attempt}")

    assert ks.state is State.DISARMED


# --- 6. explicit rearm is the only way back --------------------------------

def test_rearm_restores_armed_state(ks):
    ks.trigger(source="test")
    assert not ks.is_armed

    ks.rearm(source="test")

    assert ks.state is State.ARMED
    ks.guard("open Safari")  # must not raise


# --- 7. idempotence --------------------------------------------------------

def test_trigger_is_idempotent(ks):
    ks.trigger(source="test")
    ks.trigger(source="test")  # must not raise
    assert ks.state is State.DISARMED


def test_trigger_on_dead_process_does_not_raise(ks):
    proc = subprocess.Popen(["sleep", "300"])
    ks.register_process(proc)
    proc.kill()
    proc.wait(timeout=2.0)

    ks.trigger(source="test")  # already-dead child must not blow up the kill path
    assert ks.state is State.DISARMED


# --- 8. audit trail --------------------------------------------------------

def test_each_trigger_appends_exactly_one_record(ks, log):
    assert log.read_all() == []

    ks.trigger(source="hotkey", reason="user panic")
    records = log.read_all()
    assert len(records) == 1

    ks.trigger(source="cli", reason="second press")
    records = log.read_all()
    assert len(records) == 2, "every press must be auditable, including repeats"


def test_trigger_record_carries_source_and_iso_timestamp(ks, log):
    ks.trigger(source="menubar", reason="user panic")

    (record,) = log.read_all()
    assert record["event"] == "killswitch.trigger"
    assert record["source"] == "menubar"
    assert record["reason"] == "user panic"
    # Must parse as a real ISO-8601 timestamp, not a free-form string.
    assert datetime.fromisoformat(record["ts"])


# --- 9. cross-process: the socket surface ----------------------------------

async def test_socket_trigger_from_separate_process(short_tmp_dir, ks):
    """A `kavach kill` from another process must disarm this one."""
    from kavach.killswitch.ipc import serve

    sock_path = short_tmp_dir / "kill.sock"
    server = await serve(ks, sock_path)

    assert ks.is_armed

    # Must be spawned *without* blocking this event loop: the socket server
    # runs on it, so a blocking subprocess.run() would deadlock — the server
    # could never accept the connection it is waiting on.
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "kavach.killswitch.cli",
        "kill", "--socket", str(sock_path), "--reason", "from another process",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

    assert proc.returncode == 0, f"CLI failed: {stderr.decode()}"

    assert ks.state is State.DISARMED, "socket trigger did not reach the switch"
    assert any(r["source"] == "cli" for r in ks.log.read_all())

    server.close()
    await server.wait_closed()


async def test_socket_file_is_owner_only(short_tmp_dir, ks):
    """Anyone who can write to this socket can re-arm the switch."""
    from kavach.killswitch.ipc import serve

    sock_path = short_tmp_dir / "kill.sock"
    server = await serve(ks, sock_path)

    mode = os.stat(sock_path).st_mode & 0o777
    assert mode == 0o600, f"socket is {oct(mode)}, expected 0o600"

    server.close()
    await server.wait_closed()
