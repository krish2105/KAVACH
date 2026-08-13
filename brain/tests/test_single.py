"""Single-instance guard (Phase 18).

> A simple heartbeat/lock-file guard so only one instance runs the wake-word
> listener if KAVACH ever runs on a second Mac. Don't overbuild this into a
> distributed system.

Not hypothetical. Earlier in this project **25 overlay instances were running
at once** — they burned real CPU, and because a `pgrep` pattern didn't match
the process name, an hour went into diagnosing a "crash" that was actually a
crowd.

The design constraint is the second sentence of the spec. This is a lock file
with a heartbeat and nothing else: no election, no consensus, no network. The
failure modes it must survive are a crash (PID gone) and a hang (PID alive,
heartbeat stale), and those are what the tests below are about.
"""

import json
import os

import pytest

from kavach.single import WakeWordLock


@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "wake.lock"


# ═══ 1. the basic guarantee ═══

def test_the_first_instance_gets_the_listener(lock_path):
    first = WakeWordLock(path=lock_path)
    assert first.acquire() is True
    assert first.held is True


def test_a_second_instance_does_not(lock_path):
    """The whole feature. Two wake-word listeners on one machine means two
    processes fighting over the microphone and answering the same 'KAVACH'."""
    first = WakeWordLock(path=lock_path)
    first.acquire()

    second = WakeWordLock(path=lock_path)

    assert second.acquire() is False
    assert second.held is False


def test_releasing_lets_the_next_instance_in(lock_path):
    first = WakeWordLock(path=lock_path)
    first.acquire()
    first.release()

    second = WakeWordLock(path=lock_path)
    assert second.acquire() is True


def test_the_holder_can_re_acquire_its_own_lock(lock_path):
    """Restarting the listener inside one process must not deadlock it."""
    lock = WakeWordLock(path=lock_path)
    lock.acquire()
    assert lock.acquire() is True


# ═══ 2. surviving a crash ═══

def test_a_lock_from_a_dead_process_is_taken_over(lock_path):
    """A crash must not lock KAVACH out of its own microphone until someone
    finds and deletes a file they don't know exists."""
    lock_path.write_text(json.dumps({
        "pid": 999_999,          # no such process
        "host": os.uname().nodename,
        "heartbeat": 1_000_000_000.0,
        "started": 1_000_000_000.0,
    }))

    lock = WakeWordLock(path=lock_path)
    assert lock.acquire() is True


def test_a_stale_heartbeat_is_taken_over(lock_path):
    """A hung process keeps its PID. The heartbeat is what catches that."""
    import time

    lock_path.write_text(json.dumps({
        "pid": os.getpid(),                 # alive — this very process
        "host": os.uname().nodename,
        "heartbeat": time.time() - 3600,    # but silent for an hour
        "started": time.time() - 7200,
    }))

    lock = WakeWordLock(path=lock_path, stale_after=30)
    assert lock.acquire() is True, "a hung holder blocked forever"


def test_a_fresh_heartbeat_is_respected(lock_path):
    import time

    lock_path.write_text(json.dumps({
        "pid": os.getpid(),
        "host": os.uname().nodename,
        "heartbeat": time.time(),
        "started": time.time(),
    }))

    lock = WakeWordLock(path=lock_path, stale_after=30)
    assert lock.acquire() is False, "took a lock that was still alive"


def test_a_corrupt_lock_file_does_not_wedge_it(lock_path):
    """A torn write during a power cut must not be permanent."""
    lock_path.write_text("{not json at all")

    assert WakeWordLock(path=lock_path).acquire() is True


def test_an_empty_lock_file_does_not_wedge_it(lock_path):
    lock_path.write_text("")
    assert WakeWordLock(path=lock_path).acquire() is True


# ═══ 3. what it records ═══

def test_the_lock_says_who_holds_it(lock_path):
    """"Which machine is listening?" should be answerable by reading a file."""
    lock = WakeWordLock(path=lock_path)
    lock.acquire()

    data = json.loads(lock_path.read_text())
    assert data["pid"] == os.getpid()
    assert data["host"] == os.uname().nodename


def test_the_heartbeat_advances(lock_path):
    lock = WakeWordLock(path=lock_path)
    lock.acquire()
    before = json.loads(lock_path.read_text())["heartbeat"]

    lock.beat()

    assert json.loads(lock_path.read_text())["heartbeat"] > before


def test_beating_without_the_lock_does_nothing(lock_path):
    """A process that lost the lock must not resurrect its claim by beating."""
    holder = WakeWordLock(path=lock_path)
    holder.acquire()
    other = WakeWordLock(path=lock_path)

    other.beat()

    assert json.loads(lock_path.read_text())["pid"] == os.getpid()
    assert other.held is False


def test_describe_reports_the_other_holder(lock_path):
    """The message a declined instance prints has to be actionable."""
    WakeWordLock(path=lock_path).acquire()

    described = WakeWordLock(path=lock_path).describe_holder()

    assert str(os.getpid()) in described
    assert os.uname().nodename in described


def test_releasing_a_lock_you_do_not_hold_leaves_it_alone(lock_path):
    holder = WakeWordLock(path=lock_path)
    holder.acquire()

    WakeWordLock(path=lock_path).release()

    assert lock_path.exists(), "a non-holder deleted the live lock"
