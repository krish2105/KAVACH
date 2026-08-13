"""Hand control of the frontmost app.

The first thing in KAVACH that acts **outside** KAVACH. Until now a misread
gesture wiggled a 3D model; now it can scroll a form you are filling in, so
this is built the way §7 says and most of these tests are refusals.

Every gate is tested separately, because each one is an independent way for a
hand to reach something it should not, and a single "it works" test would pass
with any five of the six in place.

Nothing here posts a real event: the poster is faked, so the suite never moves
your actual windows.
"""

import pytest

from kavach.gestures.appcontrol import AppController, ControlRefused
from kavach.hands.allowlist import Allowlist
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog


class FakePoster:
    """Stands in for CGEventPost. Records rather than acts."""

    def __init__(self, access: bool = True):
        self.events: list[dict] = []
        self.access = access

    def has_access(self) -> bool:
        return self.access

    def post_scroll(self, dx: float, dy: float, command: bool = False) -> None:
        self.events.append({"dx": dx, "dy": dy, "command": command})


class FakeFrontmost:
    """The app in front, as NSWorkspace would report it."""

    def __init__(self, name="Safari", bundle_id="com.apple.Safari"):
        self.name = name
        self.bundle_id = bundle_id

    def __call__(self):
        if self.name is None:
            return None
        return {"name": self.name, "bundle_id": self.bundle_id}


@pytest.fixture
def log(tmp_path):
    return ActionLog(tmp_path / "actions.jsonl")


@pytest.fixture
def kill_switch(log):
    return KillSwitch(log=log)


@pytest.fixture
def controller(log, kill_switch):
    return AppController(
        allowlist=Allowlist(),
        kill_switch=kill_switch,
        poster=FakePoster(),
        frontmost=FakeFrontmost(),
    )


# ═══ 1. it is off until you say otherwise ═══

def test_it_is_disabled_by_default(controller):
    """Arming something that drives your Mac should be a decision you remember
    making today, not one inherited from a previous session."""
    assert controller.enabled is False

    with pytest.raises(ControlRefused):
        controller.scroll(0.0, 0.05)

    assert controller.poster.events == []


def test_nothing_about_the_arming_is_persisted(controller, tmp_path):
    """A restart must leave it disarmed. Deliberately no config file — this is
    the one setting that should not survive.
    """
    import inspect

    from kavach.gestures import appcontrol

    source = inspect.getsource(appcontrol)
    for persistence in ("json.dump", "write_text", "open(", "Path.home"):
        assert persistence not in source, \
            f"{persistence!r} suggests the armed state is being saved"


def test_enabling_lets_an_allowed_app_through(controller):
    controller.enable()

    controller.scroll(0.0, 0.05)

    assert len(controller.poster.events) == 1


# ═══ 2. the allowlist decides which apps ═══

def test_an_app_not_on_the_allowlist_is_refused(controller):
    controller.enable()
    controller.frontmost = FakeFrontmost("Google Chrome", "com.google.Chrome")

    with pytest.raises(ControlRefused) as caught:
        controller.scroll(0.0, 0.05)

    assert "chrome" in str(caught.value).lower()
    assert controller.poster.events == []


def test_an_unidentifiable_app_is_refused(controller):
    """Same rule ToolGate follows: what cannot be checked cannot be allowed."""
    controller.enable()
    controller.frontmost = FakeFrontmost(None, None)

    with pytest.raises(ControlRefused):
        controller.scroll(0.0, 0.05)

    assert controller.poster.events == []


def test_an_app_with_no_bundle_id_is_refused(controller):
    controller.enable()
    controller.frontmost = FakeFrontmost("Something", None)

    with pytest.raises(ControlRefused):
        controller.scroll(0.0, 0.05)


# ═══ 3. the same guards every other action path has ═══

def test_a_latched_kill_switch_refuses_control(controller, kill_switch):
    controller.enable()
    kill_switch.trigger(source="test", reason="latched")

    with pytest.raises(ControlRefused):
        controller.scroll(0.0, 0.05)

    assert controller.poster.events == []


def test_a_pending_confirmation_refuses_control(controller):
    """A hand moving near an approve/deny prompt is how a thumbs-up gets
    misread — and here it could also scroll the thing you are approving."""
    controller.enable()
    controller.confirmation_pending = True

    with pytest.raises(ControlRefused):
        controller.scroll(0.0, 0.05)


def test_ghost_mode_refuses_control(controller):
    from kavach.privacy.ghost import GhostMode

    ghost = GhostMode()
    ghost.enter(source="test")
    controller.ghost = ghost
    controller.enable()

    with pytest.raises(ControlRefused):
        controller.scroll(0.0, 0.05)


def test_missing_post_access_is_refused_with_something_actionable(log, kill_switch):
    controller = AppController(
        allowlist=Allowlist(), kill_switch=kill_switch,
        poster=FakePoster(access=False), frontmost=FakeFrontmost(),
    )
    controller.enable()

    with pytest.raises(ControlRefused) as caught:
        controller.scroll(0.0, 0.05)

    assert "accessibility" in str(caught.value).lower() or \
           "permission" in str(caught.value).lower()


# ═══ 4. what it actually sends ═══

def test_scroll_carries_the_deltas(controller):
    controller.enable()

    controller.scroll(0.01, 0.05)

    event = controller.poster.events[0]
    assert event["dy"] != 0
    assert event["command"] is False


def test_scroll_direction_follows_the_hand(controller):
    """Moving your hand down scrolls the content down, like a trackpad."""
    controller.enable()

    controller.scroll(0.0, 0.05)
    down = controller.poster.events[-1]["dy"]
    controller.scroll(0.0, -0.05)
    up = controller.poster.events[-1]["dy"]

    assert (down > 0) != (up > 0), "both directions scrolled the same way"


def test_zoom_uses_command_scroll(controller):
    """⌘+scroll is the idiom Safari, Preview and Maps already understand, so
    zoom needs no keystroke synthesis and no private API."""
    controller.enable()

    controller.zoom(1.1)

    assert controller.poster.events[-1]["command"] is True


def test_a_negligible_zoom_sends_nothing(controller):
    """Fingertip jitter must not stream ⌘-scroll events at an app."""
    controller.enable()

    controller.zoom(1.0)

    assert controller.poster.events == []


# ═══ 5. it leaves a record ═══

def test_a_control_session_is_logged_with_the_app(controller, log):
    controller.enable()

    controller.scroll(0.0, 0.05)
    controller.end_session()

    events = [e for e in log.read_all() if e.get("event", "").startswith("appcontrol")]
    assert events, "no record of controlling another app"
    assert any(e.get("app") == "Safari" for e in events)


def test_the_record_survives_ghost_mode(controller, log):
    """Ghost hides what KAVACH saw, never what it did — and driving another
    application is very much something it did."""
    from kavach.killswitch.log import ActionLog as AL

    assert "appcontrol.start" not in AL.SUPPRESSED_IN_GHOST
    assert "appcontrol.end" not in AL.SUPPRESSED_IN_GHOST


def test_every_frame_is_not_logged(controller, log):
    """30 lines a second would bury the log it belongs in."""
    controller.enable()

    for _ in range(20):
        controller.scroll(0.0, 0.01)

    starts = [e for e in log.read_all() if e.get("event") == "appcontrol.start"]
    assert len(starts) == 1
