"""Ghost mode (Phase 14).

Ghost stops KAVACH *sensing*. The kill switch stops it *acting*. Keeping those
two separate is the whole design: ghost is resumable because turning your mic
back on is routine, and the kill switch latches because resuming action after
an emergency stop should not be.

The tests that matter here are the ones about the gap ghost leaves in the
action log. Ghost is allowed to stop recording what it sees; it is not allowed
to hide *that it stopped*. A gap you cannot see the edges of is
indistinguishable from a gap somebody made on purpose.
"""

import time

import pytest

from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog
from kavach.privacy.ghost import GhostMode


class FakeMic:
    def __init__(self):
        self.running = True

    def stop(self):
        self.running = False

    def start(self):
        self.running = True


class FakeTracker:
    def __init__(self):
        self.running = True

    def stop(self):
        self.running = False


@pytest.fixture
def log(tmp_path):
    return ActionLog(tmp_path / "actions.jsonl")


@pytest.fixture
def ghost(log):
    return GhostMode(log=log)


# ═══ 1. it actually stops sensing ═══

def test_ghost_stops_the_microphone(ghost):
    mic = FakeMic()
    ghost.attach(mic=mic)

    ghost.enter(source="test")

    assert mic.running is False


def test_ghost_stops_the_camera_too(ghost):
    """Both, not one. A ghost mode that leaves the camera running is a lie."""
    mic, tracker = FakeMic(), FakeTracker()
    ghost.attach(mic=mic, tracker=tracker)

    ghost.enter(source="test")

    assert mic.running is False
    assert tracker.running is False, "the camera was still watching"


def test_leaving_ghost_restores_the_microphone(ghost):
    mic = FakeMic()
    ghost.attach(mic=mic)
    ghost.enter(source="test")

    ghost.leave(source="test")

    assert mic.running is True
    assert ghost.is_active is False


def test_entering_twice_is_harmless(ghost):
    mic = FakeMic()
    ghost.attach(mic=mic)
    ghost.enter(source="test")
    ghost.enter(source="test")

    assert ghost.is_active
    assert mic.running is False


def test_leaving_when_not_in_ghost_is_harmless(ghost):
    ghost.leave(source="test")
    assert ghost.is_active is False


# ═══ 2. the log gap has visible edges ═══

def test_entering_ghost_is_logged(ghost, log):
    ghost.enter(source="menubar")

    events = [e["event"] for e in log.read_all()]
    assert "ghost.enter" in events


def test_nothing_is_logged_while_in_ghost(ghost, log):
    ghost.enter(source="test")
    log.append("router.decision", utterance="something private")

    utterances = [e for e in log.read_all() if e.get("event") == "router.decision"]
    assert utterances == [], "ghost mode recorded something it shouldn't have"


def test_leaving_ghost_is_logged_even_though_logging_is_suspended(ghost, log):
    """The load-bearing test of this phase.

    Ghost suspends the log, so the naive implementation cannot write its own
    exit — the suspension eats it, and the log then shows a beginning with no
    end. Read later, that is indistinguishable from KAVACH still being blind,
    or from someone having stopped the log and walked away.
    """
    ghost.enter(source="test")
    ghost.leave(source="test")

    events = [e["event"] for e in log.read_all()]
    assert "ghost.enter" in events
    assert "ghost.leave" in events, "ghost mode swallowed its own exit"
    assert events.index("ghost.enter") < events.index("ghost.leave")


def test_the_exit_record_says_how_long_it_lasted(ghost, log):
    """"How long was KAVACH blind" should not require subtracting timestamps."""
    ghost.enter(source="test")
    time.sleep(0.05)
    ghost.leave(source="test")

    exit_record = [e for e in log.read_all() if e["event"] == "ghost.leave"][0]
    assert exit_record["seconds"] >= 0.05


def test_logging_resumes_after_ghost(ghost, log):
    ghost.enter(source="test")
    ghost.leave(source="test")
    log.append("router.decision", utterance="after")

    assert any(e.get("event") == "router.decision" for e in log.read_all())


def test_the_log_records_who_asked(ghost, log):
    ghost.enter(source="api")

    entry = [e for e in log.read_all() if e["event"] == "ghost.enter"][0]
    assert entry["source"] == "api"


# ═══ 3. ghost is not a bypass ═══

def test_ghost_does_not_re_arm_a_latched_kill_switch(log):
    """Ghost stops sensing. It has no opinion on acting, and must not acquire
    one — leaving ghost mode must never quietly undo an emergency stop."""
    ks = KillSwitch(log=log)
    ghost = GhostMode(log=log, kill_switch=ks)
    ks.trigger(source="test", reason="latched")

    ghost.enter(source="test")
    ghost.leave(source="test")

    assert not ks.is_armed, "leaving ghost re-armed the kill switch"


def test_leaving_ghost_does_not_restart_the_mic_while_latched(log):
    """If the kill switch is latched, nothing should be listening either."""
    ks = KillSwitch(log=log)
    ghost = GhostMode(log=log, kill_switch=ks)
    mic = FakeMic()
    ghost.attach(mic=mic)

    ghost.enter(source="test")
    ks.trigger(source="test", reason="latched during ghost")
    ghost.leave(source="test")

    assert mic.running is False, "the mic came back while the switch was latched"


def test_a_kill_switch_record_still_gets_written_during_ghost(log):
    """Ghost hides what KAVACH *saw*. It must not hide what KAVACH *did* —
    and an emergency stop is the single most important thing to record."""
    ks = KillSwitch(log=log)
    ghost = GhostMode(log=log, kill_switch=ks)
    ghost.enter(source="test")

    ks.trigger(source="test", reason="stopped while blind")

    events = [e["event"] for e in log.read_all()]
    assert "killswitch.trigger" in events, "ghost mode hid an emergency stop"


# ═══ 4. the camera, which lives in another process ═══
#
# These exist because the tests above did NOT catch a real gap. They attached a
# fake tracker to GhostMode and asserted it stopped — but in the running
# system the webcam is owned by the presence process, so the brain's GhostMode
# has no tracker to stop and logged `stopped: ["mic"]`. The tests were green
# and the camera was on.
#
# The lesson is about the fixture, not the feature: attaching a fake to the
# object under test proves the object calls stop(), and proves nothing about
# whether the real thing is reachable from there.

class RebuildableTracker:
    """Like HandTracker: a thread that cannot be restarted once stopped."""

    def __init__(self):
        self.running = True

    def stop(self):
        self.running = False


def test_the_camera_gate_stops_the_tracker_in_ghost_mode():
    from kavach.privacy.camera_gate import CameraGate

    built = []

    def make():
        t = RebuildableTracker()
        built.append(t)
        return t

    gate = CameraGate(make_tracker=make)
    gate.start()

    assert gate.apply(ghost=True) is True
    assert gate.running is False
    assert built[0].running is False


def test_the_camera_gate_rebuilds_rather_than_resuming():
    """HandTracker is a Thread — a stopped one cannot be started again."""
    from kavach.privacy.camera_gate import CameraGate

    built = []
    gate = CameraGate(make_tracker=lambda: built.append(RebuildableTracker())
                      or built[-1])
    gate.start()
    gate.apply(ghost=True)

    gate.apply(ghost=False)

    assert gate.running is True
    assert len(built) == 2, "it tried to reuse the stopped thread"


def test_the_camera_gate_only_restarts_what_it_stopped():
    """Gestures off for another reason must stay off.

    No camera permission, --no-gestures, a crashed tracker: none of those are
    ghost mode, and leaving ghost mode must not switch the webcam on.
    """
    from kavach.privacy.camera_gate import CameraGate

    gate = CameraGate(make_tracker=lambda: RebuildableTracker())
    # Never started — gestures were off from the beginning.

    gate.apply(ghost=True)
    gate.apply(ghost=False)

    assert gate.running is False, "it turned on a camera nobody asked for"


def test_the_camera_gate_is_idempotent():
    from kavach.privacy.camera_gate import CameraGate

    gate = CameraGate(make_tracker=lambda: RebuildableTracker())
    gate.start()
    gate.apply(ghost=True)

    assert gate.apply(ghost=True) is False
    assert gate.running is False


# ═══ 5. ghost hides what KAVACH SAW, never what KAVACH DID ═══
#
# Found by running it, not by reading it: a typed command over the API returned
# HTTP 200 while the log was suspended, so KAVACH could reach a tool with no
# record. That breaks §C ("every tool call, every argument, timestamped"),
# which is not a rule ghost mode gets to suspend.
#
# The entry/exit-only choice rested on "there should be nothing in between".
# The API path breaks that premise — you can still type at it while it is not
# listening — so the boundary is drawn around *perception* instead: what KAVACH
# heard or saw is suppressed, what it did is always recorded.

@pytest.mark.parametrize("event", [
    "tool.decision",        # §C's core: every tool call and argument
    "killswitch.trigger",
    "killswitch.blocked",
    "api.command",          # you typed it deliberately; it can cause action
    "api.confirm",
    "confirm.timeout",      # a consent record
    "ghost.enter",
    "ghost.leave",
])
def test_actions_are_still_recorded_in_ghost_mode(ghost, log, event):
    ghost.enter(source="test")

    log.append(event, detail="something KAVACH did")

    recorded = [e["event"] for e in log.read_all()]
    assert event in recorded, f"ghost mode hid {event}"


@pytest.mark.parametrize("event", [
    "router.decision",      # carries `utterance` — what you said
    "voice.turn",           # the transcript
    "voice.rejected",       # who was speaking
])
def test_perception_is_suppressed_in_ghost_mode(ghost, log, event):
    ghost.enter(source="test")

    log.append(event, utterance="something private")

    recorded = [e["event"] for e in log.read_all()]
    assert event not in recorded, f"ghost mode recorded {event}"


def test_a_tool_call_during_ghost_keeps_its_arguments(ghost, log):
    """Not merely "a tool ran" — §C says every argument."""
    ghost.enter(source="test")

    log.append("tool.decision", tool="Notes.delete",
               arguments={"title": "Draft"}, allowed=True)

    entry = [e for e in log.read_all() if e["event"] == "tool.decision"][0]
    assert entry["arguments"] == {"title": "Draft"}
