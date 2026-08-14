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

    # `starts_hidden()` rather than the raw flag since 2026-08-15: it is the
    # same check at the same place, and it additionally lets "always visible"
    # override a saved minimise. The guarantee this test exists for — that
    # startup honours Minimise at all — is unchanged.
    assert "if self.geometry.starts_hidden():" in body, \
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


# ═══ controls that exist must do something ═══

def test_the_panel_offers_a_gestures_toggle():
    """G is a deliberate no-op in the panel and cannot fire there anyway.

    The key toggles gestures in a browser tab, is explicitly skipped in overlay
    mode (`if (!overlayMode)`), and no keydown reaches a non-activating panel
    regardless. So the hint row advertised a control that could not work, and
    the only way to stop the camera was Ghost mode — which also stops the
    microphone.
    """
    overlay = (Path(__file__).resolve().parents[1] / "kavach" / "presence"
               / "overlay.py").read_text()

    assert 'command == "gestures"' in overlay, \
        "no way to toggle the camera from the panel"
    assert "camera_gate" in overlay


def test_the_panel_does_not_advertise_keys_that_cannot_fire():
    """A non-activating panel never becomes key, so G/R/K/ESC are dead there
    however correctly they are wired in the page."""
    orb = (Path(__file__).resolve().parents[2] / "apps" / "orb"
           / "components" / "JarvisOrb.tsx").read_text()
    hint = orb[orb.index('className="hud hud-hint"'):]
    hint = hint[:hint.index("hud-controls")]

    assert "overlayMode ?" in hint, \
        "the panel prints the browser's keyboard hints"
    assert "⌃⌥⌘SPACE" in hint, "the one shortcut that does work is not shown"


def test_gesture_state_is_pushed_not_guessed():
    """The tracker lives in the presence process, so a label the page worked
    out for itself would be a guess — and a menu that misreports the camera is
    worse than no menu."""
    overlay = (Path(__file__).resolve().parents[1] / "kavach" / "presence"
               / "overlay.py").read_text()

    assert "__kavachGestures" in overlay


# ═══ a held chord is one press, not ninety-six ═══
#
# Measured 2026-08-14 from ~/.kavach/logs/overlay.log — 200 lines of it:
#
#     96  kavach.presence: talk requested        81ms apart
#     33  page rendering on
#     33  page rendering PAUSED
#
# macOS auto-repeat on ⌃⌥⌘Space. Each repeat opened its own websocket to the
# bridge (`BridgeFollower.send` gives every command a fresh connection, on the
# documented assumption that commands arrive "once every few minutes") and
# queued another turn — `record_ms: 15009` on a turn nobody spoke for fifteen
# seconds.
#
# Two fixes were tried before this one, and instrumenting the handler is what
# killed each of them.
#
# **"Ignore events with isARepeat set"** — dead hotkey. ⌃Space is macOS's own
# input-source switcher, so the chord's first keyDown is often eaten before a
# global monitor sees it, and the whole hold arrives flagged as repeats:
#
#     23:16:38,671  chord: keyCode=49 repeat=True     13 in a row,
#     23:16:38,752  chord: keyCode=49 repeat=True     no repeat=False at all
#     ...
#     23:16:41,155  chord: keyCode=49 repeat=False    the next press, 1.7s later
#
# **"Debounce on time alone"** — two turns per hold. macOS waits ~500ms before
# it starts repeating, so the first repeat is further from the press than any
# debounce that still feels responsive:
#
#     23:20:53,005  chord accepted  (inf since the last)    the press
#     23:20:53,503  chord accepted  (0.50s since the last)  the first repeat
#
# So both signals are used, each for what it can prove: the gap kills the 83ms
# stream and rescues an eaten first press, the flag kills the first repeat.

from kavach.presence.controls import (  # noqa: E402
    CHORD_REPEAT_GAP_S,
    HOLD_GAP_S,
    should_act_on_hotkey,
)

#: The measured auto-repeat interval on this machine.
_REPEAT_S = 0.083

#: The measured "Delay Until Repeat" before the stream starts.
_INITIAL_DELAY_S = 0.50

#: No previous chord event — the gap is effectively unbounded.
_FIRST = 1e9


def _count(events) -> int:
    """Replay (gap, is_repeat) pairs the way the handler does: every chord
    event updates the clock, whether or not it is acted on."""
    return sum(
        should_act_on_hotkey(modifiers_held=True, seconds_since_previous=gap,
                             is_repeat=repeat)
        for gap, repeat in events
    )


def test_a_fresh_press_acts():
    assert should_act_on_hotkey(modifiers_held=True,
                                seconds_since_previous=_FIRST, is_repeat=False)


def test_the_wrong_modifiers_never_act():
    for gap in (_FIRST, _REPEAT_S):
        for repeat in (False, True):
            assert not should_act_on_hotkey(modifiers_held=False,
                                            seconds_since_previous=gap,
                                            is_repeat=repeat)


def test_an_auto_repeat_does_not_act_again():
    assert not should_act_on_hotkey(modifiers_held=True,
                                    seconds_since_previous=_REPEAT_S,
                                    is_repeat=True)


def test_the_first_repeat_after_the_delay_does_not_act_again():
    """The second firing, measured. Time alone cannot tell this from someone
    pressing again 500ms later; the flag can."""
    assert not should_act_on_hotkey(modifiers_held=True,
                                    seconds_since_previous=_INITIAL_DELAY_S,
                                    is_repeat=True)


def test_a_hold_with_the_press_delivered_is_one_action():
    """The 23:20 sequence: press, 500ms, then the 83ms stream."""
    events = ([(_FIRST, False), (_INITIAL_DELAY_S, True)]
              + [(_REPEAT_S, True)] * 20)

    assert _count(events) == 1, f"one hold fired {_count(events)} times"


def test_a_hold_whose_first_press_was_eaten_still_acts_once():
    """The 23:16 burst: thirteen repeats and not one fresh press among them.
    Filtering on the flag gives zero here — a hotkey that does nothing."""
    events = [(_FIRST, True)] + [(_REPEAT_S, True)] * 12

    assert _count(events) == 1, f"an eaten first press fired {_count(events)} times"


def test_holding_for_two_seconds_is_still_one_action():
    events = ([(_FIRST, False), (_INITIAL_DELAY_S, True)]
              + [(_REPEAT_S, True)] * int(2.0 / _REPEAT_S))

    assert _count(events) == 1, f"a two-second hold fired {_count(events)} times"


def test_letting_go_and_pressing_again_acts_again():
    """Measured: the next press came 1.7s after the last repeat. Debouncing
    must not turn a deliberate second press into nothing."""
    assert should_act_on_hotkey(modifiers_held=True, seconds_since_previous=1.7,
                                is_repeat=False)


def test_the_thresholds_bracket_what_was_measured():
    """Under the repeat interval and the repeats get through again; over a
    human double-press and deliberate presses start vanishing."""
    assert _REPEAT_S < CHORD_REPEAT_GAP_S < 0.5
    assert HOLD_GAP_S > _INITIAL_DELAY_S


# ═══ "always visible" must beat "minimised" ═══
#
# Both are persisted, they contradict each other, and the invisible one won.
# Live, with `always: true` and `hidden: true` on disk, the orb showed for a
# turn and then vanished about a second later — every time:
#
#     01:08:41  page rendering on
#     01:08:42  page rendering PAUSED
#     01:08:43  page rendering on
#     01:08:44  page rendering PAUSED
#
# `hidden` persists across launches, so once it is set the panel starts
# minimised for ever and `always` is dead the moment it is saved. The user hit
# it twice in one night, because ⌃⌥⌘H is one key from ⌃⌥⌘Space.
#
# `always` is the more recent, more deliberate instruction — you cannot set it
# by accident — so it wins.

def test_always_visible_defeats_a_saved_minimise():
    from kavach.presence.controls import Geometry

    geometry = Geometry(hidden=True, always=True)

    assert geometry.starts_hidden() is False, (
        "the panel would start minimised despite 'always visible' — the "
        "setting you cannot see beating the one you chose"
    )


def test_a_saved_minimise_is_honoured_on_its_own():
    from kavach.presence.controls import Geometry

    assert Geometry(hidden=True, always=False).starts_hidden() is True


def test_an_unminimised_panel_starts_visible():
    from kavach.presence.controls import Geometry

    for always in (False, True):
        assert Geometry(hidden=False, always=always).starts_hidden() is False


def test_minimising_still_works_while_always_is_on():
    """The rule is about STARTUP, not about the toggle. ⌃⌥⌘H must still hide
    the panel in the moment, or the key stops doing anything."""
    from kavach.presence.controls import Geometry

    geometry = Geometry(always=True)
    geometry.set_hidden(True)

    assert geometry.hidden is True
