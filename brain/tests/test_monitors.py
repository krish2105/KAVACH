"""Phase 32 — scheduled checks that observe and never act.

Three monitors: calendar conflicts, battery, and KAVACH's own health. Each
returns findings; none of them does anything about what it finds. That
separation is the phase's whole design — a monitor that could act would be a
second action path, gated separately from the first, and this project has now
found six instances of one fact living in two places.

Tier AUTO, because there is nothing to approve about looking. Anything worth
*doing* goes to the Phase 33 queue, where the user decides.

**Phase 23's morning briefing does not exist in this repo.** The spec routes
findings to "the briefing and/or the queue"; only the queue is real, so that is
where they go. Inventing a briefing to satisfy a reference would be building on
a foundation that was never poured.

**A monitor that cannot run reports that it could not run.** It never reports
"all clear", because an unread battery and a healthy battery produce the same
silence and only one of them is true — the same rule that makes a missing Full
Disk Access grant an explicit refusal rather than an empty file list.
"""

import pytest

from kavach.autonomy.monitors import (
    Finding,
    check_battery,
    check_self_health,
    run_all,
)


# ═══ findings, not actions ═══

def test_monitors_never_act():
    """A monitor that could act would be a second action path with its own
    gate. Asserted on the source, because the property is 'this code cannot',
    not 'this code currently does not'."""
    import inspect

    from kavach.autonomy import monitors

    from ._sourcecheck import code_text

    source = code_text(inspect.getmodule(monitors))
    for forbidden in ("subprocess.run", "osascript", "os.system", "Popen",
                      "unlink", "write_text", "requests.", "urlopen"):
        assert forbidden not in source, (
            f"{forbidden} in monitors.py — monitors observe, they do not act"
        )


# ═══ battery ═══

def test_battery_low_is_a_finding():
    found = check_battery(percent=8, charging=False)

    assert found is not None
    assert found.severity == "warn"
    assert "8" in found.detail


def test_battery_low_while_charging_is_not_a_problem():
    """Plugged in at 8% is a battery doing its job."""
    assert check_battery(percent=8, charging=True) is None


def test_a_healthy_battery_is_silent():
    assert check_battery(percent=80, charging=False) is None


def test_an_unreadable_battery_says_so():
    """Not "all clear". An unread battery and a healthy battery produce the
    same silence, and only one of them is true."""
    found = check_battery(percent=None, charging=None)

    assert found is not None
    assert found.severity == "unknown"


# ═══ self-health ═══

def test_a_dead_process_is_a_finding():
    found = check_self_health({"voice": False, "overlay": True})

    assert found is not None
    assert "voice" in found.detail
    assert found.severity == "warn"


def test_everything_running_is_silent():
    assert check_self_health({"voice": True, "overlay": True}) is None


def test_no_information_is_not_good_news():
    found = check_self_health({})

    assert found is not None and found.severity == "unknown"


# ═══ running them together ═══

def test_one_failing_monitor_does_not_stop_the_others():
    """A monitor that raises must not silence the rest — that would turn one
    bug into total blindness."""
    def broken():
        raise RuntimeError("nope")

    def fine():
        return Finding("test", "warn", "something")

    findings = run_all([broken, fine])

    assert len(findings) == 1
    assert findings[0].detail == "something"


def test_findings_carry_what_produced_them():
    """A finding with no source is one nobody can act on."""
    found = check_battery(percent=5, charging=False)

    assert found.source == "battery"


def test_run_all_with_nothing_to_report_is_empty():
    assert run_all([lambda: None, lambda: None]) == []
