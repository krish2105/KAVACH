"""The latch must outlive the process that fired it.

Found by wiring `kavach-memory index` to the gate and then testing it for
real, from a shell, instead of only in-process:

    $ uv run kavach kill --reason "proving the indexer is gated"
    ✓ latched
    $ uv run python -c "…; print(KillSwitch().state)"
      State.ARMED                       ← a NEW process disagrees
    $ uv run kavach-memory index ~/notes
      ✓ 1 file(s) indexed                ← read the disk while latched

`__init__` set `State.ARMED` unconditionally and `trigger()` only mutated
memory, so the latch lived in the daemon and nowhere else. Every separately
launched process — every CLI — started armed no matter what had just
happened. The unit test for the indexer passed the whole time because it
shared one `KillSwitch` object with the code it was testing.

§C says the switch "latches disarmed — no auto-recovery" and that `guard()`
gates every action path. A gate that answers yes in every process but one is
not a gate; it is the shape of one.

**The state file lives beside the action log**, not at a fixed path, so a
test with a `tmp_path` log is isolated by construction and cannot be made
order-dependent by a stale file in `~/.kavach`.
"""

import pytest

from kavach.killswitch.core import KillSwitch, KillSwitchDisarmed, State
from kavach.killswitch.log import ActionLog


@pytest.fixture
def home(tmp_path):
    """One directory standing in for `~/.kavach`. Two switches over it are
    two processes."""
    return tmp_path


def switch(home) -> KillSwitch:
    """A freshly constructed switch — i.e. a newly launched process."""
    return KillSwitch(log=ActionLog(home / "actions.jsonl"))


# ═══ the latch crosses process boundaries ═══

def test_a_latch_is_visible_to_a_process_that_did_not_fire_it(home):
    switch(home).trigger(source="test", reason="halt")

    assert switch(home).state is State.DISARMED


def test_that_process_actually_refuses_to_act(home):
    """State is only worth persisting if `guard()` reads it."""
    switch(home).trigger(source="test", reason="halt")

    with pytest.raises(KillSwitchDisarmed):
        switch(home).guard("file.read")


def test_re_arming_clears_it_everywhere(home):
    switch(home).trigger(source="test", reason="halt")
    switch(home).rearm(source="test", reason="understood")

    assert switch(home).state is State.ARMED
    switch(home).guard("file.read")   # must not raise


# ═══ the two states that are not a latch ═══

def test_a_fresh_install_is_armed(home):
    """No state file is not ambiguity — nothing has ever happened. Booting
    disarmed would mean KAVACH ships dead."""
    assert switch(home).state is State.ARMED


def test_an_unreadable_state_file_stays_stopped(home):
    """§C: an ambiguous state stays stopped. A truncated or hand-edited file
    is exactly the case where guessing "armed" is the expensive guess."""
    (home / "killswitch.state").write_text("\x00\x00 not json at all")

    assert switch(home).state is State.DISARMED


def test_an_unwritable_state_directory_does_not_break_the_latch(home):
    """If persistence fails, the in-memory latch must still hold. Losing the
    ability to *record* the stop cannot become a failure to *stop*."""
    s = switch(home)
    s._state_path = home / "no-such-dir" / "killswitch.state"

    s.trigger(source="test", reason="halt")

    assert s.state is State.DISARMED
    with pytest.raises(KillSwitchDisarmed):
        s.guard("file.read")


# ═══ end to end: the CLI that found this ═══

def test_the_memory_indexer_refuses_while_latched(home, tmp_path, monkeypatch):
    """The measurement that started this, as a test. Two separate KillSwitch
    objects, exactly as two processes would have."""
    from kavach.memory import cli

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("something worth not reading right now")

    switch(home).trigger(source="test", reason="halt")

    monkeypatch.setattr(cli, "_kill_switch", lambda: switch(home))
    monkeypatch.setattr(
        cli, "_open_store",
        lambda: pytest.fail("opened the store before checking the switch"),
    )

    assert cli.main(["index", str(docs)]) != 0
