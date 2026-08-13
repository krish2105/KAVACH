"""Continuous hand control — pinch to grab, move to rotate, spread to zoom.

Different in kind from the five existing gestures. Those fire once after an
0.8s hold and then stop mattering; this one reacts to every frame while your
hand is pinched, so the thing that makes it usable is a clear, deliberate
engage and disengage rather than a threshold that flickers.

The geometry is pure, so it tests without a camera. Landmarks are MediaPipe's
21 points, normalised 0-1, and only three matter here: the wrist for scale, and
the thumb and index tips for the pinch.
"""

import pytest

from kavach.gestures.pinch import (
    LOST_FRAME_GRACE,
    PinchTracker,
    is_pinching,
    pinch_distance,
)


def hand(thumb=(0.5, 0.5), index=(0.5, 0.5), wrist=(0.5, 0.9)):
    """21 landmarks, with only the three that matter placed deliberately."""
    points = [(0.5, 0.5, 0.0) for _ in range(21)]
    points[0] = (wrist[0], wrist[1], 0.0)
    points[4] = (thumb[0], thumb[1], 0.0)     # thumb tip
    points[8] = (index[0], index[1], 0.0)     # index tip
    points[5] = (0.45, 0.6, 0.0)              # index base, for hand scale
    return points


# ═══ 1. the pinch itself ═══

def test_touching_fingers_are_a_pinch():
    assert is_pinching(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))


def test_spread_fingers_are_not_a_pinch():
    assert not is_pinching(hand(thumb=(0.30, 0.50), index=(0.70, 0.50)))


def test_the_pinch_is_measured_against_hand_size_not_pixels():
    """Otherwise it works at one distance from the camera and no other.

    A hand at arm's length spans a fraction of the frame that a hand at 20cm
    does not, so a fixed threshold in normalised coordinates would demand a
    tighter and tighter pinch as you lean back.
    """
    near = hand(thumb=(0.40, 0.40), index=(0.44, 0.40), wrist=(0.40, 0.90))
    far = hand(thumb=(0.48, 0.48), index=(0.49, 0.48), wrist=(0.48, 0.60))

    assert is_pinching(near) == is_pinching(far)


# ═══ 2. engaging and letting go ═══

def test_a_pinch_engages_and_reports_no_movement_on_the_first_frame():
    """The frame you close your fingers on must not jump the orb: there is no
    previous position to measure against, so the delta is zero by definition."""
    tracker = PinchTracker()

    move = tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))

    assert move is not None and move.engaged
    assert move.dx == 0.0 and move.dy == 0.0


def test_moving_while_pinched_reports_the_delta():
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))

    move = tracker.update(hand(thumb=(0.60, 0.55), index=(0.61, 0.55)))

    assert move.engaged
    assert move.dx > 0 and move.dy > 0


def test_opening_the_hand_disengages():
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))

    move = tracker.update(hand(thumb=(0.30, 0.50), index=(0.70, 0.50)))

    assert not move.engaged


def test_re_pinching_starts_fresh_rather_than_jumping():
    """Let go, move your hand across the desk, pinch again — the orb must not
    leap by the distance your hand travelled while not pinching."""
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.20, 0.20), index=(0.21, 0.20)))
    tracker.update(hand(thumb=(0.30, 0.50), index=(0.70, 0.50)))   # released

    move = tracker.update(hand(thumb=(0.80, 0.80), index=(0.81, 0.80)))

    assert move.engaged
    assert move.dx == 0.0 and move.dy == 0.0, "it jumped on re-engaging"


def test_losing_the_hand_entirely_disengages():
    """Your hand leaving the frame is a release, not a freeze.

    Takes a few frames rather than one. This originally asserted that a single
    missing frame released, which was wrong about real video: MediaPipe drops
    the hand for individual frames constantly, and the live log showed grips
    lasting about a fifth of a second because of it. The property — hand gone,
    grip gone — is unchanged; only how patient it is before believing it.
    """
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))

    for _ in range(LOST_FRAME_GRACE + 1):
        move = tracker.update(None)

    assert move is None or not move.engaged


# ═══ 3. zoom, from how wide the pinch is ═══

def test_spreading_the_pinch_zooms_out_and_closing_zooms_in():
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))

    wider = tracker.update(hand(thumb=(0.48, 0.50), index=(0.55, 0.50)))
    assert wider.scale > 1.0

    tracker2 = PinchTracker()
    tracker2.update(hand(thumb=(0.46, 0.50), index=(0.56, 0.50)))
    closer = tracker2.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))
    assert closer.scale < 1.0


def test_scale_is_one_when_the_pinch_width_is_unchanged():
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.52, 0.50)))

    move = tracker.update(hand(thumb=(0.60, 0.50), index=(0.62, 0.50)))

    assert move.scale == pytest.approx(1.0, abs=0.05)


# ═══ 4. it must never fight a confirmation ═══

def test_control_is_refused_while_a_confirmation_is_pending():
    """Your choice, and the one genuinely dangerous interaction.

    A hand moving near a pending approve/deny is exactly how a thumbs-up gets
    misread. While KAVACH is waiting on a §7 answer, continuous control is off
    entirely rather than merely deprioritised.
    """
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))

    move = tracker.update(hand(thumb=(0.70, 0.70), index=(0.71, 0.70)),
                          confirmation_pending=True)

    assert move is None or not move.engaged


def test_control_resumes_once_the_confirmation_is_answered():
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)),
                   confirmation_pending=True)

    move = tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))

    assert move is not None and move.engaged


# ═══ 5. what a real hand exposed ═══
#
# The first version worked in the sense that it engaged and produced deltas,
# and would have been unusable. From the live log:
#
#   pinch ENGAGED (gap 0.11 of hand span)
#   pinch dx=+0.011 dy=-0.011 zoom=2.187      <- 2x camera distance, one frame
#   pinch released                            <- 0.2s later, hand lost 1 frame
#
# Two failures neither geometry nor a fixture would surface: a tight pinch
# makes the width ratio enormous under fingertip jitter, and MediaPipe drops
# the hand for single frames constantly.

def test_one_frame_cannot_zoom_wildly():
    """A pinch of 0.11 fluctuating by a millimetre yielded zoom=2.187 —
    doubling the camera distance between two frames of video."""
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.500, 0.50), index=(0.504, 0.50)))

    move = tracker.update(hand(thumb=(0.500, 0.50), index=(0.512, 0.50)))

    assert 0.9 <= move.scale <= 1.1, f"one frame zoomed by {move.scale}"


def test_zoom_still_responds_over_several_frames():
    """Clamped, not disabled: spreading steadily must still zoom out."""
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))

    total = 1.0
    for gap in (0.02, 0.03, 0.04, 0.05, 0.06):
        move = tracker.update(hand(thumb=(0.50, 0.50), index=(0.50 + gap, 0.50)))
        total *= move.scale

    assert total > 1.1, "steady spreading did not accumulate any zoom"


def test_a_single_dropped_frame_does_not_let_go():
    """MediaPipe loses the hand for one frame constantly. Releasing on the
    first miss made the grip last a fifth of a second."""
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))

    move = tracker.update(None)

    assert move.engaged, "one lost frame released the pinch"


def test_the_hand_really_leaving_does_let_go():
    """Forgiving a blink is not the same as never letting go."""
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))

    for _ in range(10):
        move = tracker.update(None)

    assert not move.engaged, "the hand left and it never released"


def test_movement_across_a_dropped_frame_is_not_a_jump():
    """The frame after a miss must measure from the last KNOWN position, not
    treat the gap as travel."""
    tracker = PinchTracker()
    tracker.update(hand(thumb=(0.50, 0.50), index=(0.51, 0.50)))
    tracker.update(None)

    move = tracker.update(hand(thumb=(0.52, 0.50), index=(0.53, 0.50)))

    assert move.engaged
    assert abs(move.dx) < 0.05, f"jumped {move.dx} after a dropped frame"
