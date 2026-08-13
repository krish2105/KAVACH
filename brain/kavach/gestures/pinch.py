"""Continuous hand control — pinch to grab, move to rotate, spread to zoom.

Different in kind from the five gestures in `recognise.py`. Those fire once
after an 0.8 s hold and then stop mattering. This reacts to every frame while
your fingers are together, so what makes it usable is not accuracy but a clear,
deliberate engage and release — a threshold that flickers would make the orb
twitch every time your hand relaxed.

Three decisions carry the feel of it:

* **The pinch is measured against the size of your hand**, not in raw
  normalised coordinates. A hand at arm's length spans a fraction of the frame
  that a hand at 20 cm does not, so a fixed threshold would silently demand a
  tighter pinch the further back you sat.
* **Engaging reports zero movement.** On the frame your fingers meet there is
  no previous position to measure from, and re-engaging after moving your hand
  across the desk must not fling the orb by the distance it travelled while
  released.
* **Hysteresis on the threshold.** Releasing takes a wider gap than engaging
  did, so a hand held at exactly the boundary does not chatter between states.

It is disabled outright while a confirmation is pending. A hand moving near an
approve/deny prompt is precisely how a thumbs-up gets misread, and §7 consent
must be given deliberately rather than collected from someone gesturing at a
3D model.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

log = logging.getLogger("kavach.gestures.pinch")

WRIST = 0
THUMB_TIP = 4
INDEX_BASE = 5
INDEX_TIP = 8

#: Pinch closed when the gap is under this fraction of the hand's own span.
ENGAGE_RATIO = 0.45
#: And open again only past this. The gap between the two is what stops a hand
#: resting on the boundary from flickering in and out of control.
RELEASE_RATIO = 0.62


@dataclass(frozen=True)
class PinchMove:
    """One frame of continuous control."""

    engaged: bool
    #: Movement since the previous frame, in normalised units.
    dx: float = 0.0
    dy: float = 0.0
    #: Multiplier from the change in pinch width. >1 spreading, <1 closing.
    scale: float = 1.0


def _hand_span(points) -> float:
    """Wrist to index knuckle — a stable stand-in for how big the hand looks.

    Chosen over the bounding box because it does not change as fingers curl,
    so the pinch threshold stays put while you are actually pinching.
    """
    wx, wy = points[WRIST][0], points[WRIST][1]
    bx, by = points[INDEX_BASE][0], points[INDEX_BASE][1]
    return max(1e-6, math.hypot(bx - wx, by - wy))


def pinch_distance(points) -> float:
    """Thumb-tip to index-tip, as a fraction of the hand's span."""
    tx, ty = points[THUMB_TIP][0], points[THUMB_TIP][1]
    ix, iy = points[INDEX_TIP][0], points[INDEX_TIP][1]
    return math.hypot(ix - tx, iy - ty) / _hand_span(points)


def is_pinching(points) -> bool:
    """Whether the fingers are closed enough to grab."""
    return pinch_distance(points) <= ENGAGE_RATIO


class PinchTracker:
    """Turns a stream of hand landmarks into rotate/zoom deltas."""

    def __init__(self) -> None:
        self._engaged = False
        self._last: tuple[float, float] | None = None
        self._last_width: float | None = None

    def _release(self) -> PinchMove:
        self._engaged = False
        self._last = None
        self._last_width = None
        return PinchMove(engaged=False)

    def update(self, points, confirmation_pending: bool = False) -> PinchMove | None:
        """Feed one frame. None when there is no hand to speak of."""
        if points is None:
            return self._release()

        if confirmation_pending:
            # Off entirely, not merely ignored: a moving hand next to an
            # approve/deny prompt is how an accidental yes happens.
            if self._engaged:
                log.info("confirmation pending — hand control suspended")
            return self._release()

        width = pinch_distance(points)
        # Hysteresis: harder to leave than to enter.
        threshold = RELEASE_RATIO if self._engaged else ENGAGE_RATIO
        if width > threshold:
            return self._release() if self._engaged else PinchMove(engaged=False)

        # Midpoint of the pinch is the thing being dragged — steadier than
        # either fingertip alone, which wobble independently.
        x = (points[THUMB_TIP][0] + points[INDEX_TIP][0]) / 2
        y = (points[THUMB_TIP][1] + points[INDEX_TIP][1]) / 2

        if not self._engaged or self._last is None:
            # First frame of a grab: no history, so no movement. Re-engaging
            # elsewhere in the frame must not fling the orb across.
            self._engaged = True
            self._last = (x, y)
            self._last_width = width
            return PinchMove(engaged=True)

        dx, dy = x - self._last[0], y - self._last[1]
        previous = self._last_width or width
        scale = 1.0 if previous <= 0 else (width / previous)

        self._last = (x, y)
        self._last_width = width
        return PinchMove(engaged=True, dx=dx, dy=dy, scale=scale)
