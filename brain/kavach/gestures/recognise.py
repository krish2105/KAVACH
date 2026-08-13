"""Turn hand landmarks into a small vocabulary of intents.

Deliberately separate from the camera. Classification is pure geometry, so it
can be tested against hand-built landmark sets rather than a webcam — which
matters because two of these gestures answer a confirmation prompt for a
destructive action. A gesture that fires when it should not is a §7 problem.

MediaPipe's 21-point model: 0 is the wrist, then four points per digit from
thumb (1-4) to pinky (17-20), tip last. y decreases upward.
"""

from __future__ import annotations

from enum import Enum

#: Landmark indices.
WRIST = 0
FINGER_BASES = (1, 5, 9, 13, 17)
THUMB_TIP = 4
INDEX_BASE = 5


class Gesture(str, Enum):
    NONE = "none"
    #: Held to approve a confirmation (§7).
    CONFIRM = "confirm"
    #: Held to refuse one.
    DENY = "deny"
    #: Open palm — interrupt whatever is happening.
    STOP = "stop"
    #: Index finger — select what is under the pointer.
    POINT = "point"
    #: Two fingers — start a turn, the gesture equivalent of the Talk button.
    PEACE = "peace"


def fingers_extended(points) -> list[bool]:
    """Which of the five digits are extended, thumb first.

    A finger is extended when its tip sits above its own knuckle. Comparing
    against the knuckle rather than the wrist keeps this working when the hand
    is tilted, which it always is.
    """
    if not points or len(points) < 21:
        return [False] * 5

    out: list[bool] = []
    for base in FINGER_BASES:
        if base == 1:
            # The thumb needs a different measure. "Tip above knuckle" is
            # meaningless for it: a thumbs-DOWN thumb is fully extended and
            # points downward, so a vertical test calls it folded. Distance
            # from the index knuckle separates the two cases properly — an
            # extended thumb reaches away from the palm whichever way it
            # points, a folded one tucks across it.
            tip = points[THUMB_TIP]
            palm = points[INDEX_BASE]
            span = ((tip[0] - palm[0]) ** 2 + (tip[1] - palm[1]) ** 2) ** 0.5
            out.append(span > 0.12)
            continue
        knuckle_y = points[base][1]
        tip_y = points[base + 3][1]
        out.append(tip_y < knuckle_y - 0.02)
    return out


def classify(points) -> Gesture:
    """The gesture these landmarks represent, or NONE.

    NONE is the default at every uncertain step. A hand at rest curls
    naturally, and a half-detected hand produces nonsense — neither should
    ever read as a command, or the camera becomes unusable while it is on.
    """
    if not points or len(points) < 21:
        return Gesture.NONE

    thumb, index, middle, ring, pinky = fingers_extended(points)

    if index and middle and ring and pinky:
        return Gesture.STOP

    if index and middle and not ring and not pinky:
        return Gesture.PEACE

    if index and not middle and not ring and not pinky:
        return Gesture.POINT

    # Thumb up or down: the thumb *extended*, everything else folded, and the
    # tip clearly above or below the wrist.
    #
    # Requiring extension is what separates these from a fist. Without it a
    # relaxed fist — thumb curled and therefore low — classified as DENY, so
    # resting your hand in view would silently refuse a pending confirmation.
    # The vertical margins matter for the same reason: confusing confirm with
    # deny is the one mistake this vocabulary must never make.
    if thumb and not (index or middle or ring or pinky):
        wrist_y = points[WRIST][1]
        thumb_y = points[THUMB_TIP][1]
        if thumb_y < wrist_y - 0.15:
            return Gesture.CONFIRM
        if thumb_y > wrist_y + 0.05:
            return Gesture.DENY

    return Gesture.NONE
