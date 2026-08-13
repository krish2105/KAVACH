"""Hand tracking in Python, so the camera prompt can actually appear.

The orb originally tracked hands in the browser with MediaPipe's JS build.
That works in a normal tab, but not in the floating panel: WKWebView grants
`getUserMedia` only when the *host app* holds camera permission, and the host
here is a launchd `python3`, not a bundled `.app` with an
`NSCameraUsageDescription`. macOS will never even prompt, so gestures could
never reach the panel — the button just printed TRACKING INIT FAILED.

Running MediaPipe here fixes that at the root: this process *can* be granted
the camera, and the gestures it recognises flow over the same bridge the voice
loop already publishes on.

§7 applies as it does to the wake word: frames are never written to disk, and
a gesture that is not acted on leaves nothing behind but a counter.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .pinch import PinchTracker
from .recognise import Gesture, classify

log = logging.getLogger("kavach.gestures.tracker")

#: A gesture must be held this long before it counts.
#:
#: Two of these answer a confirmation for a destructive action, so a flicker
#: while your hand passes the camera must not approve anything. Holding is
#: also visible — the orb can show the progress, so a commitment is something
#: you watch yourself make rather than something that happens to you.
HOLD_SECONDS = 0.8

#: Below this, the detection is not trusted at all.
MIN_CONFIDENCE = 0.6

#: How willing MediaPipe is to KEEP tracking a hand it already found.
#:
#: Deliberately lower than detection. A pinch is the exact moment the model
#: struggles: the thumb occludes the index finger and the silhouette stops
#: looking like a hand, so at the default 0.5 it drops the hand mid-grip.
#: Measured before changing it — grips were lasting about 300ms, every one of
#: them ending in a lost hand rather than fingers opening.
#:
#: Losing a hand that has left the frame a little late costs nothing; losing
#: one that is still there costs the whole feature.
MIN_TRACKING_CONFIDENCE = 0.25
MIN_PRESENCE_CONFIDENCE = 0.25


@dataclass
class GestureEvent:
    gesture: Gesture
    #: 0-1 through the hold. 1.0 means it fired.
    progress: float
    fired: bool


class HandTracker(threading.Thread):
    """Watches the webcam and reports held gestures."""

    daemon = True

    def __init__(self, on_event: Callable[[GestureEvent], None], camera: int = 0,
                 on_pinch: Callable | None = None):
        super().__init__(name="kavach-gestures")
        self.on_event = on_event
        #: Continuous rotate/zoom. Optional — nothing changes when unset.
        self.on_pinch = on_pinch
        self._pinch = PinchTracker()
        #: Set by the presence process while KAVACH waits on a §7 answer, so a
        #: hand moving near the prompt cannot be read as a thumbs-up.
        self.confirmation_pending = False
        self.camera = camera
        self._stop = threading.Event()
        self._landmarker = None
        self.available = False
        self.detections = 0

    # ——— lifecycle ———

    def _build(self):
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        model = _ensure_model()
        options = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=MIN_CONFIDENCE,
            min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
            min_hand_presence_confidence=MIN_PRESENCE_CONFIDENCE,
        )
        return vision.HandLandmarker.create_from_options(options)

    def run(self) -> None:
        import cv2
        import mediapipe as mp
        import numpy as np

        try:
            self._landmarker = self._build()
        except Exception as exc:
            log.warning("hand tracking unavailable: %s", exc)
            return

        capture = cv2.VideoCapture(self.camera)
        if not capture.isOpened():
            # The most likely cause by far, and worth naming rather than
            # leaving as a generic failure.
            log.warning(
                "camera did not open — grant Camera in System Settings → "
                "Privacy & Security, then restart. Gestures are off until then."
            )
            return

        self.available = True
        log.info("hand tracking live")

        held: Gesture = Gesture.NONE
        held_since = 0.0
        fired_for: Gesture | None = None

        try:
            while not self._stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    time.sleep(0.05)
                    continue

                # Mirror, so moving your hand right moves it right on screen.
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = self._landmarker.detect_for_video(
                    image, int(time.monotonic() * 1000)
                )

                points = []
                if result.hand_landmarks:
                    points = [(p.x, p.y, p.z) for p in result.hand_landmarks[0]]

                # Continuous control runs alongside the held gestures rather
                # than instead of them: a pinch is not one of the five, so
                # classify() sees NONE and the two never compete.
                if self.on_pinch is not None:
                    try:
                        move = self._pinch.update(
                            points or None,
                            confirmation_pending=self.confirmation_pending,
                        )
                        if move is not None:
                            self.on_pinch(move)
                    except Exception:
                        log.debug("pinch update failed", exc_info=True)

                gesture = classify(points)

                now = time.monotonic()
                if gesture != held:
                    held, held_since, fired_for = gesture, now, None

                if held is Gesture.NONE:
                    self.on_event(GestureEvent(Gesture.NONE, 0.0, False))
                    continue

                progress = min(1.0, (now - held_since) / HOLD_SECONDS)
                fired = progress >= 1.0 and fired_for is not held
                if fired:
                    fired_for = held
                    self.detections += 1
                    # The gesture only — never the frame (§7).
                    log.info("gesture %s", held.value)
                self.on_event(GestureEvent(held, progress, fired))
        finally:
            capture.release()
            if self._landmarker is not None:
                self._landmarker.close()
            self.available = False
            log.info("hand tracking stopped")

    def stop(self) -> None:
        self._stop.set()


_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)


def _ensure_model():
    """Fetch the landmark model once, next to the other models."""
    from pathlib import Path
    from urllib.request import urlretrieve

    target = Path(__file__).resolve().parents[2] / "models" / "hand_landmarker.task"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        log.info("downloading hand landmark model (~7MB)")
        urlretrieve(_MODEL_URL, target)
    return target
