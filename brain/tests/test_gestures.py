"""Gesture recognition tests.

Classification is kept separate from the camera so it can be tested against
hand-built landmark sets rather than a webcam. Every gesture here maps to an
action KAVACH will take, and two of them answer a confirmation for a
destructive operation — so a gesture that fires when it should not is a §7
problem, not a UX one.

Landmarks follow MediaPipe's 21-point hand model: 0 is the wrist, then four
points per digit from thumb (1-4) to pinky (17-20), with the tip last.
"""

import pytest

from kavach.gestures.recognise import (
    Gesture,
    classify,
    fingers_extended,
)


def hand(**overrides) -> list[tuple[float, float, float]]:
    """A neutral open palm, palm toward the camera, fingers up.

    y decreases upward in MediaPipe's normalised space, so a tip above its
    knuckle means a smaller y.
    """
    pts = [(0.5, 0.9, 0.0)]  # wrist
    # thumb 1-4, then index/middle/ring/pinky 5-20, tips extended upward
    for finger, x in enumerate([0.35, 0.45, 0.5, 0.55, 0.6]):
        base_y = 0.8
        for joint in range(4):
            pts.append((x, base_y - joint * 0.09, 0.0))
    out = pts[:21]
    for index, point in overrides.items():
        out[int(index)] = point
    return out


def curl(points, finger: int):
    """Fold one finger down: tip below its own knuckle.

    The thumb folds *across the palm* rather than straight down — anatomically
    it cannot do otherwise, and an earlier version of this helper placed a
    curled thumb at the same coordinate as a thumbs-down thumb, which no
    classifier could have separated. That was a broken fixture, not a broken
    classifier.
    """
    points = list(points)
    base = 1 + finger * 4
    if finger == 0:
        palm = points[5]
        for joint in range(1, 4):
            points[base + joint] = (palm[0] + 0.01 * joint, palm[1] - 0.02, 0.0)
        return points
    knuckle_y = points[base][1]
    for joint in range(1, 4):
        points[base + joint] = (points[base + joint][0], knuckle_y + 0.06 * joint, 0.0)
    return points


# ═══ finger extension, the primitive everything else builds on ═══

def test_open_palm_has_every_finger_extended():
    assert fingers_extended(hand()) == [True, True, True, True, True]


def test_a_curled_finger_reads_as_folded():
    assert fingers_extended(curl(hand(), 1))[1] is False


def test_a_fist_has_nothing_extended():
    points = hand()
    for finger in range(5):
        points = curl(points, finger)
    assert not any(fingers_extended(points))


# ═══ the vocabulary ═══

def test_open_palm_is_stop():
    assert classify(hand()) is Gesture.STOP


def test_fist_is_none_not_a_command():
    """A resting hand curls naturally. Firing on that would make the camera
    unusable while it is on."""
    points = hand()
    for finger in range(5):
        points = curl(points, finger)
    assert classify(points) is Gesture.NONE


def test_index_only_is_point():
    points = hand()
    for finger in (0, 2, 3, 4):
        points = curl(points, finger)
    assert classify(points) is Gesture.POINT


def test_index_and_middle_is_peace():
    points = hand()
    for finger in (0, 3, 4):
        points = curl(points, finger)
    assert classify(points) is Gesture.PEACE


def test_thumb_up_is_confirm():
    points = hand()
    for finger in (1, 2, 3, 4):
        points = curl(points, finger)
    # thumb clearly above the wrist
    points[4] = (0.35, 0.45, 0.0)
    assert classify(points) is Gesture.CONFIRM


def test_thumb_down_is_deny():
    points = hand()
    for finger in (1, 2, 3, 4):
        points = curl(points, finger)
    points[4] = (0.30, 1.05, 0.0)  # extended, below and away from the palm
    assert classify(points) is Gesture.DENY


def test_confirm_and_deny_are_not_confusable():
    """These two answer a destructive-action prompt. Mistaking one for the
    other is the worst failure in the vocabulary."""
    up, down = hand(), hand()
    for finger in (1, 2, 3, 4):
        up, down = curl(up, finger), curl(down, finger)
    up[4] = (0.35, 0.45, 0.0)
    down[4] = (0.30, 1.05, 0.0)
    assert classify(up) is Gesture.CONFIRM
    assert classify(down) is Gesture.DENY


# ═══ robustness ═══

@pytest.mark.parametrize("points", [[], [(0.5, 0.5, 0.0)] * 5, None])
def test_malformed_landmarks_are_not_a_gesture(points):
    """A partially detected hand must read as nothing, never as a command."""
    assert classify(points) is Gesture.NONE
