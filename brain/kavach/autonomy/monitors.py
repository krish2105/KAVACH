"""Phase 32 — scheduled checks that observe and never act.

Calendar conflicts, battery, and KAVACH's own health. Each returns findings;
**none of them does anything about what it finds.**

That separation is the design, not a simplification. A monitor that could act
would be a second action path with its own gate, and this project has found six
instances of one fact living in two places — the startup banner, the Ollama
model name, the agent prompt, a duplicated constant, two disagreeing server
lists, and endpointing logic fixed in the copy that does not run. Anything
worth *doing* goes to the Phase 33 queue, where the user decides.

Tier AUTO (Phase 30), because there is nothing to approve about looking.

**Phase 23's morning briefing does not exist in this repo.** The spec routes
findings to "the briefing and/or the queue"; only the queue is real, so that is
where they go. Building a briefing to satisfy a reference would be pouring a
foundation to hold up a wall nobody asked for.

**A monitor that cannot run says so.** It never returns "all clear" from a
failed reading, because an unread battery and a healthy battery produce the
same silence and only one of them is true. Same rule as the missing Full Disk
Access grant being reported as a refusal rather than an empty file list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("kavach.autonomy.monitors")

#: Below this, on battery, is worth mentioning once.
LOW_BATTERY_PERCENT = 15


@dataclass
class Finding:
    """Something observed. Not something done, and not something to do."""

    source: str
    #: `warn` — worth telling the user. `unknown` — the check could not run,
    #: which is different from nothing being wrong and is never rounded to it.
    severity: str
    detail: str


def check_battery(percent: int | None, charging: bool | None) -> Finding | None:
    """Low battery, on battery power.

    Arguments rather than a reading, so the policy is testable without a
    Mac — and so this module holds no way to shell out for one.
    """
    if percent is None or charging is None:
        return Finding("battery", "unknown", "could not read the battery")
    if charging:
        # Plugged in at 8% is a battery doing its job.
        return None
    if percent <= LOW_BATTERY_PERCENT:
        return Finding("battery", "warn", f"battery at {percent}%, unplugged")
    return None


def check_self_health(processes: dict[str, bool]) -> Finding | None:
    """Whether KAVACH's own pieces are alive.

    `processes` maps a name to whether it is running. An empty mapping is
    **unknown**, not healthy: nobody reported, which is not the same as
    everybody being fine.
    """
    if not processes:
        return Finding("self", "unknown", "no process information available")
    down = sorted(name for name, alive in processes.items() if not alive)
    if not down:
        return None
    return Finding("self", "warn", f"not running: {', '.join(down)}")


def check_calendar_conflicts(events: list[dict] | None) -> Finding | None:
    """Overlapping events in a list of `{start, end, title}` dicts.

    Takes the events rather than fetching them, for the same reason as the
    battery: this module must not be able to reach out and touch anything.
    """
    if events is None:
        return Finding("calendar", "unknown", "could not read the calendar")
    if len(events) < 2:
        return None

    try:
        ordered = sorted(events, key=lambda e: e["start"])
    except (KeyError, TypeError):
        return Finding("calendar", "unknown", "calendar entries were unreadable")

    clashes = []
    for earlier, later in zip(ordered, ordered[1:]):
        try:
            if later["start"] < earlier["end"]:
                clashes.append(f"{earlier.get('title', '?')} / "
                               f"{later.get('title', '?')}")
        except (KeyError, TypeError):
            continue
    if not clashes:
        return None
    return Finding("calendar", "warn", "overlapping: " + "; ".join(clashes[:3]))


#: How many scored turns before a threshold is worth reading. A floor, not
#: the test — see `check_shadow_readiness` for why count is the least
#: interesting of the three conditions.
SHADOW_MIN_SAMPLES = 10

#: The scores must span at least this much. All-identical samples describe one
#: condition however many there are.
SHADOW_MIN_SPREAD = 0.15

#: And they must come from at least this many hours apart. "One sitting" is
#: the specific error that produced 0.803, 0.577 and 0.383 — every one of
#: those had plenty of samples.
SHADOW_MIN_HOURS = 6.0


def check_shadow_readiness(samples) -> Finding | None:
    """Whether shadow mode has collected something worth reading.

    `samples` is `[(similarity, timestamp_seconds), ...]`.

    **Count is the least interesting condition.** Three thresholds have been
    set from samples that were plentiful and unrepresentative — enrolment
    clips recorded back to back, and six sentences read aloud in one sitting.
    Each had enough; none had spread.

    Returns None until all three hold, and None is the common answer. A
    monitor that says "not yet" every five minutes is one nobody reads.
    """
    if len(samples) < SHADOW_MIN_SAMPLES:
        return None

    values = sorted(float(v) for v, _ in samples)
    if values[-1] - values[0] < SHADOW_MIN_SPREAD:
        return None

    times = [float(t) for _, t in samples]
    if (max(times) - min(times)) < SHADOW_MIN_HOURS * 3600:
        return None

    return Finding(
        "voiceprint", "ready",
        f"{len(values)} voice samples spanning {values[0]:.2f}-{values[-1]:.2f} "
        f"— enough to set the speaker threshold. Run: kavach-speaker scores",
    )


def run_all(checks) -> list[Finding]:
    """Run every check, collecting findings.

    Each is wrapped: a monitor that raises must not silence the others, or one
    bug becomes total blindness — the same reason the wake-word scorer catches
    everything rather than letting an exception end listening.
    """
    findings: list[Finding] = []
    for check in checks:
        try:
            found = check()
        except Exception:
            log.debug("a monitor failed", exc_info=True)
            continue
        if found is not None:
            findings.append(found)
    return findings


__all__ = ["Finding", "check_battery", "check_self_health",
           "check_calendar_conflicts", "check_shadow_readiness", "run_all",
           "LOW_BATTERY_PERCENT", "SHADOW_MIN_SAMPLES", "SHADOW_MIN_SPREAD",
           "SHADOW_MIN_HOURS"]
