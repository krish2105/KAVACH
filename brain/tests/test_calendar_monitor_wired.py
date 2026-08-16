"""`check_calendar_conflicts` was written, exported, and never called.

`kavach/autonomy/monitors.py` defines it and names it in `__all__`.
`observe/__main__.py` runs `check_battery`, `check_self_health` and
`check_shadow_readiness` — and not this one. So it has never produced a
finding.

Eleventh built-but-unwired instance in this project.

The reader is new and lives in `observe/__main__.py`, matching `_battery`
and `_processes`: the monitor takes events and cannot fetch them, so nothing
in `monitors.py` can reach the calendar even by accident.

**Two properties the other readers already have and this one needs more:**

* **Unreadable is not empty.** `None` for "could not read" and `[]` for
  "no events today" are different facts, and the monitor already
  distinguishes them — it reports "could not read the calendar" for the
  first and stays silent for the second. A reader that returned `[]` on
  failure would report "no conflicts" for a calendar it never opened.
* **Nothing user-supplied reaches the AppleScript.** The script is a fixed
  string with no interpolation, the same rule `MacActions` follows.

Reading Calendar.app needs an Automation grant per source→target pair, and
this daemon has never sent it an AppleEvent. **The first run may fail once,
silently, before the grant exists** — that is TCC, recorded here so nobody
debugs the monitor for it.
"""

import inspect

import pytest

from kavach.autonomy.monitors import check_calendar_conflicts
from kavach.observe import __main__ as observer


def test_the_monitor_is_actually_run():
    """The whole point. It was defined and exported and never called."""
    source = inspect.getsource(observer.run_checks)
    assert "check_calendar_conflicts" in source, (
        "check_calendar_conflicts is still not in the monitor list, so it "
        "cannot ever produce a finding"
    )


def test_there_is_a_reader():
    assert hasattr(observer, "_calendar_events")


def test_the_reader_does_not_interpolate_anything():
    """A fixed script, like MacActions. Nothing transcribed or fetched may
    reach an AppleScript."""
    source = inspect.getsource(observer._calendar_events)
    assert "format(" not in source
    assert "f\"" not in source.replace('f"""', "") or "%s" not in source


def test_an_unreadable_calendar_is_not_an_empty_one(monkeypatch):
    """`None` and `[]` are different facts. A reader that returned `[]` on
    failure would report "no conflicts" for a calendar it never opened."""
    import subprocess

    def boom(*args, **kwargs):
        raise OSError("osascript is not available")

    monkeypatch.setattr(subprocess, "run", boom)

    assert observer._calendar_events() is None


def test_a_refused_grant_reads_as_unknown(monkeypatch):
    """Automation is granted per source→target pair and this daemon has
    never sent Calendar an AppleEvent. A refusal must not look like a free
    afternoon."""
    import subprocess

    class Refused:
        returncode = 1
        stdout = ""
        stderr = "Not authorised to send Apple events to Calendar."

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Refused())

    assert observer._calendar_events() is None


def test_the_monitor_reports_that_it_could_not_read():
    """The pairing that makes the above matter."""
    finding = check_calendar_conflicts(None)

    assert finding is not None
    assert "could not read" in finding.detail


def test_no_events_is_silence_not_a_finding():
    assert check_calendar_conflicts([]) is None
    assert check_calendar_conflicts([{"start": 1, "end": 2, "title": "one"}]) is None


def test_overlapping_events_are_reported():
    finding = check_calendar_conflicts([
        {"start": 10, "end": 20, "title": "standup"},
        {"start": 15, "end": 25, "title": "review"},
    ])

    assert finding is not None
    assert "standup" in finding.detail and "review" in finding.detail


def test_a_parsed_row_has_the_shape_the_monitor_expects(monkeypatch):
    """The reader and the monitor agree on `start`/`end`/`title`, or the
    monitor silently returns 'calendar entries were unreadable' forever."""
    import subprocess

    class Ok:
        returncode = 0
        stderr = ""
        stdout = ("standup\t2026-08-16 10:00:00\t2026-08-16 10:30:00\n"
                  "review\t2026-08-16 10:15:00\t2026-08-16 11:00:00\n")

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Ok())

    events = observer._calendar_events()

    assert events is not None and len(events) == 2
    assert set(events[0]) >= {"start", "end", "title"}
    # And the monitor must find the clash in exactly that shape.
    finding = check_calendar_conflicts(events)
    assert finding is not None and "standup" in finding.detail


def test_a_malformed_row_is_skipped_not_fatal(monkeypatch):
    import subprocess

    class Ok:
        returncode = 0
        stderr = ""
        stdout = "broken row with no tabs\nstandup\t2026-08-16 10:00:00\t2026-08-16 10:30:00\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Ok())

    events = observer._calendar_events()

    assert events is not None
    assert len(events) == 1
