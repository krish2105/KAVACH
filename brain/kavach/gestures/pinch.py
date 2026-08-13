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
MIDDLE_TIP = 12
MIDDLE_BASE = 9

#: Pinch closed when the gap is under this fraction of the hand's own span.
#:
#: Measured from real grips, not chosen: engagements landed at 0.09, 0.26,
#: 0.33, 0.39 and 0.45 — the last of those scraping a 0.45 limit, which is
#: almost certainly why some pinches never registered at all. 0.55 accepts a
#: lighter, more comfortable pinch, at the price of the occasional grab when
#: fingers happen to be close.
ENGAGE_RATIO = 0.55
#: And open again only past this. The gap between the two is what stops a hand
#: resting on the boundary from flickering in and out of control.
RELEASE_RATIO = 0.72

#: The most a single frame may zoom.
#:
#: Measured from a real hand: a tight pinch (gap 0.11 of hand span) fluctuating
#: by a fingertip's worth of detection noise produced scale=2.187 — doubling
#: the camera distance between two frames of video. The ratio is only
#: meaningful over several frames, so no one frame gets to matter much.
MAX_FRAME_ZOOM = 1.06

#: How many frames the hand may vanish for before the grip is dropped.
#:
#: MediaPipe loses a hand for single frames constantly, and releasing on the
#: first miss made a grip last about a fifth of a second.
#:
#: Six was not enough. The capture loop sleeps 50ms between frames, so six is
#: only ~0.3s — and measured grips were ending at almost exactly that, every
#: one from a lost hand rather than fingers opening. Fifteen is around 0.75s:
#: long enough to ride through the occlusion a pinch causes, short enough that
#: a hand genuinely leaving still lets go while it feels deliberate.
LOST_FRAME_GRACE = 15


@dataclass(frozen=True)
class PinchMove:
    """One frame of continuous control."""

    engaged: bool
    #: Movement since the previous frame, in normalised units.
    dx: float = 0.0
    dy: float = 0.0
    #: Multiplier from the change in pinch width. >1 spreading, <1 closing.
    scale: float = 1.0
    #: Thumb-to-index gap as a fraction of hand span. Carried so a threshold
    #: that is wrong for a real hand can be seen rather than guessed at — the
    #: constants here were chosen from geometry, not from anyone's fingers.
    ratio: float = 0.0


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
        self._missing = 0

    def _release(self) -> PinchMove:
        was = self._engaged
        self._engaged = False
        self._last = None
        self._last_width = None
        self._missing = 0
        if was:
            log.info("pinch released")
        return PinchMove(engaged=False)

    def update(self, points, confirmation_pending: bool = False) -> PinchMove | None:
        """Feed one frame. None when there is no hand to speak of."""
        if points is None:
            # A blink, not a release. The hand is dropped for single frames
            # constantly; letting go on the first miss made a grip unusable.
            # The last known position is kept, so the frame it returns on is
            # measured from there rather than counted as travel.
            if self._engaged and self._missing < LOST_FRAME_GRACE:
                self._missing += 1
                return PinchMove(engaged=True, ratio=self._last_width or 0.0)
            return self._release()

        if confirmation_pending:
            # Off entirely, not merely ignored: a moving hand next to an
            # approve/deny prompt is how an accidental yes happens.
            if self._engaged:
                log.info("confirmation pending — hand control suspended")
            return self._release()

        self._missing = 0
        width = pinch_distance(points)
        # Hysteresis: harder to leave than to enter.
        threshold = RELEASE_RATIO if self._engaged else ENGAGE_RATIO
        if width > threshold:
            if self._engaged:
                return self._release()
            return PinchMove(engaged=False, ratio=width)

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
            return PinchMove(engaged=True, ratio=width)

        dx, dy = x - self._last[0], y - self._last[1]
        previous = self._last_width or width
        scale = 1.0 if previous <= 0 else (width / previous)
        # Clamped, not smoothed: spreading steadily still accumulates zoom
        # across frames, but no single frame of jitter can throw the camera.
        scale = max(1 / MAX_FRAME_ZOOM, min(MAX_FRAME_ZOOM, scale))

        self._last = (x, y)
        self._last_width = width
        return PinchMove(engaged=True, dx=dx, dy=dy, scale=scale, ratio=width)


#: Two extended fingers must be at least this far apart, relative to hand span,
#: to count as the scroll pose rather than a closed hand.
SCROLL_MIN_SPREAD = 0.15
#: And no further than this, so a splayed open palm — which is STOP — is not
#: mistaken for two deliberate fingers.
SCROLL_MAX_SPREAD = 0.95


def is_scrolling(points) -> bool:
    """Index and middle extended together — the two-finger swipe pose.

    Distinct from a pinch by construction: a pinch brings the THUMB to the
    index, this separates index from MIDDLE, so the two can never be read as
    each other. It takes over the shape that used to start a turn; the TALK
    button and the wake word still do that, so nothing was lost.
    """
    span = _hand_span(points)
    ix, iy = points[INDEX_TIP][0], points[INDEX_TIP][1]
    mx, my = points[MIDDLE_TIP][0], points[MIDDLE_TIP][1]
    spread = math.hypot(mx - ix, my - iy) / span
    if not (SCROLL_MIN_SPREAD <= spread <= SCROLL_MAX_SPREAD):
        return False

    # Both must actually be extended — reaching above their own knuckles —
    # or a loose fist with fingers slightly apart would scroll.
    base_y = points[MIDDLE_BASE][1]
    return iy < base_y and my < base_y


@dataclass(frozen=True)
class ScrollMove:
    engaged: bool
    dx: float = 0.0
    dy: float = 0.0


class ScrollTracker:
    """Two extended fingers moving = scroll, with the same engage rules as the
    pinch: no jump on the first frame, and nothing at all while a confirmation
    is waiting."""

    def __init__(self) -> None:
        self._engaged = False
        self._last: tuple[float, float] | None = None
        self._missing = 0

    def _release(self) -> ScrollMove:
        self._engaged = False
        self._last = None
        self._missing = 0
        return ScrollMove(engaged=False)

    def update(self, points, confirmation_pending: bool = False) -> ScrollMove:
        if points is None:
            if self._engaged and self._missing < LOST_FRAME_GRACE:
                self._missing += 1
                return ScrollMove(engaged=True)
            return self._release()

        if confirmation_pending or not is_scrolling(points):
            return self._release()

        self._missing = 0
        # Midpoint of the two fingertips, for the same reason the pinch uses
        # the midpoint of thumb and index: one fingertip alone wobbles.
        x = (points[INDEX_TIP][0] + points[MIDDLE_TIP][0]) / 2
        y = (points[INDEX_TIP][1] + points[MIDDLE_TIP][1]) / 2

        if not self._engaged or self._last is None:
            self._engaged = True
            self._last = (x, y)
            return ScrollMove(engaged=True)

        dx, dy = x - self._last[0], y - self._last[1]
        self._last = (x, y)
        return ScrollMove(engaged=True, dx=dx, dy=dy)
