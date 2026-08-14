"""When the orb is on screen, and why it was not.

After a reboot the panel was invisible and the size menu appeared to do
nothing. Neither was a rendering failure. Three things combined:

* the panel was **minimised**, and `hidden` persists across launches
* `set_size()` resizes the window but never shows it, so every size click
  while minimised is a no-op you cannot see — that is "not able to resize it"
* the overlay hides when idle, and with no voice loop running the state is
  *always* idle, so it never comes back on its own

Each is defensible alone. Together they make a panel that is invisible, cannot
be resized, and gives no clue which of the two is happening.

`OverlayWindow` needs AppKit and a window server, so these test the geometry
and the decisions around it rather than the drawing.
"""

import json
from pathlib import Path

import pytest

from kavach.presence import controls
from kavach.presence.controls import Geometry, should_hide_when_idle


@pytest.fixture
def geometry(tmp_path, monkeypatch):
    monkeypatch.setattr(controls, "GEOMETRY_PATH", tmp_path / "overlay.json")
    return Geometry()


# ═══ choosing a size means "show it at this size" ═══

def test_choosing_a_size_un_minimises(geometry):
    """The bug, stated as a test.

    Minimised, every entry in the size menu resized a window nobody could see.
    The click worked, the geometry changed, the panel stayed gone — which reads
    exactly like a menu that does nothing.
    """
    geometry.hidden = True

    geometry.apply_size(560.0)

    assert geometry.size == 560.0
    assert geometry.hidden is False, \
        "choosing a size left the panel minimised, so nothing appears"


def test_choosing_a_size_is_remembered(geometry, tmp_path):
    geometry.apply_size(560.0)

    saved = json.loads(controls.GEOMETRY_PATH.read_text())

    assert saved["size"] == 560.0
    assert saved["hidden"] is False


def test_minimise_still_minimises(geometry):
    """The fix must not break the feature: staying out of the way on purpose
    is the whole point of Minimise."""
    geometry.hidden = False

    geometry.set_hidden(True)

    assert geometry.hidden is True


# ═══ hiding when idle needs something that can stop being idle ═══

def test_it_stays_visible_when_no_brain_is_connected():
    """Otherwise the orb is invisible forever and nothing says why.

    The panel hides when idle and fades in when KAVACH starts listening. With
    no voice loop running, nothing ever sets a non-idle state, so "hide when
    idle" means "hide". After a reboot that is a black window and a menu bar
    item, and no way to tell a broken orb from a quiet one.
    """
    assert should_hide_when_idle(bridge_connected=False, always=False) is False


def test_it_hides_when_idle_once_a_brain_is_there():
    assert should_hide_when_idle(bridge_connected=True, always=False) is True


def test_always_show_still_wins(geometry):
    """`always` is the demo switch and must override both."""
    assert should_hide_when_idle(bridge_connected=True, always=True) is False
    assert should_hide_when_idle(bridge_connected=False, always=True) is False


# ═══ a size that cannot be undone is not a size ═══

def test_every_named_size_can_be_reached_from_every_other(geometry):
    """Small was a trap once already, in the menu markup. It must not become
    one again through geometry."""
    from kavach.presence.controls import SIZES

    for start in SIZES.values():
        for target in SIZES.values():
            geometry.apply_size(start)
            geometry.apply_size(target)
            assert geometry.size == target
            assert geometry.hidden is False


def test_a_panel_stranded_on_an_unplugged_display_comes_back(geometry,
                                                             monkeypatch):
    """The other way to be invisible while insisting you are shown.

    The position is remembered, so a panel last placed on an external monitor
    keeps those coordinates after it is unplugged. x=-2400 is exactly as
    invisible as minimised, and looks identical from the outside.
    """
    monkeypatch.setattr(controls, "screen_frames",
                        lambda: [(0.0, 0.0, 1512.0, 944.0)])
    geometry.x, geometry.y = -2400.0, 300.0

    geometry.clamp()

    assert geometry.x is None, "still off screen, so still invisible"


def test_a_panel_on_a_real_display_is_left_alone(geometry, monkeypatch):
    """Recentring a correctly-placed panel would be its own bug."""
    monkeypatch.setattr(controls, "screen_frames",
                        lambda: [(0.0, 0.0, 1512.0, 944.0)])
    geometry.x, geometry.y = 98.0, 27.0

    geometry.clamp()

    assert (geometry.x, geometry.y) == (98.0, 27.0)


def test_no_window_server_moves_nothing(geometry, monkeypatch):
    """Headless, guessing would shove a panel that may be perfectly placed."""
    monkeypatch.setattr(controls, "screen_frames", list)
    geometry.x, geometry.y = 98.0, 27.0

    geometry.clamp()

    assert (geometry.x, geometry.y) == (98.0, 27.0)


# ═══ every route back out of Minimise ═══

def test_minimise_is_never_a_one_way_door():
    """Three commands mean "show me the panel", and each must clear `hidden`.

    Not a style point. Minimised, the size menu did nothing visible, and with
    no voice loop running nothing else would ever show the orb again — so the
    only way out was to know the hotkey, which is dead without Input
    Monitoring. A state with no exit is the same bug the 🛡 menu had at 280pt.
    """
    source = (Path(__file__).resolve().parents[1] / "kavach" / "presence"
              / "overlay.py").read_text()

    for routine in ("def set_size", "def toggle_fullscreen", "def reset_position"):
        start = source.index(routine)
        body = source[start:start + 1400]
        assert "hidden = False" in body or "apply_size" in body, \
            f"{routine} can leave the panel minimised and invisible"


def test_minimise_is_enforced_even_with_no_brain():
    """It used to `return` here, which enforced nothing when no voice loop was
    running — Minimise worked with a brain and silently did not without one."""
    source = (Path(__file__).resolve().parents[1] / "kavach" / "presence"
              / "overlay.py").read_text()
    start = source.index("def apply_state")
    body = source[start:start + 1600]

    assert "self.hide()" in body, \
        "apply_state returns on `hidden` without ever hiding the window"


def test_a_minimised_panel_does_not_reappear_at_launch():
    """The flag has to mean the same thing with and without a voice loop.

    Enforced only in apply_state(), which nothing calls when no brain is
    running, a minimised orb came back on every launch and then vanished on
    the first snapshot once one connected.
    """
    source = (Path(__file__).resolve().parents[1] / "kavach" / "presence"
              / "overlay.py").read_text()

    start = source.index("self.panel.setContentView_(self.web)")
    body = source[start:start + 900]

    assert "if self.geometry.hidden:" in body, \
        "the window is ordered front at startup regardless of Minimise"


def test_a_hand_near_the_camera_does_not_un_minimise():
    """Hand tracking publishes a control target on every tick, and that path
    called show(). So Minimise held only until you moved — which, with the
    camera watching, is seconds."""
    source = (Path(__file__).resolve().parents[1] / "kavach" / "presence"
              / "overlay.py").read_text()
    start = source.index("def tick")

    assert "if not self._visible:\n" not in source[start:], \
        "a tick path shows the panel without checking Minimise"
