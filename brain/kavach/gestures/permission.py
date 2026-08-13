"""Ask macOS for the camera, on the main thread, where it is allowed to ask.

OpenCV attempts this itself and fails with "can not spin main run loop from
other thread", then reports only that the camera would not open — which reads
as broken hardware rather than a permission that was never requested.

This is also the difference between gestures working at all and not: WKWebView
could never be granted the camera because its host is not a bundled app, so
Python asking for itself is the only route.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger("kavach.gestures.permission")

#: AVAuthorizationStatus
NOT_DETERMINED, RESTRICTED, DENIED, AUTHORIZED = 0, 1, 2, 3

_LABEL = {
    NOT_DETERMINED: "not yet asked",
    RESTRICTED: "restricted by policy",
    DENIED: "denied",
    AUTHORIZED: "granted",
}


def camera_status() -> int:
    import AVFoundation

    return AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeVideo
    )


def request_camera(timeout: float = 30.0) -> bool:
    """Request access and wait for the answer. True if we may use the camera.

    Must run on the main thread: the prompt is UI, and AVFoundation will
    silently do nothing from a worker.
    """
    import AVFoundation

    status = camera_status()
    if status == AUTHORIZED:
        return True
    if status in (DENIED, RESTRICTED):
        log.warning(
            "camera %s — gestures are off. System Settings → Privacy & "
            "Security → Camera.", _LABEL[status]
        )
        return False

    log.info("requesting camera access — macOS will prompt")
    answered: list[bool] = []
    AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AVFoundation.AVMediaTypeVideo, lambda granted: answered.append(bool(granted))
    )

    deadline = time.monotonic() + timeout
    while not answered and time.monotonic() < deadline:
        # Pump the run loop so the prompt can appear and its answer arrive.
        import Foundation

        Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
            Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1)
        )

    granted = bool(answered and answered[0])
    log.info("camera %s", "granted" if granted else "not granted")
    return granted
