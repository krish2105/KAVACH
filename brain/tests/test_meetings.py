"""Meeting-aware muting (Phase 15).

Detection is app-specific because it has to be: there is no public macOS API
for "am I in a call". `kAudioDevicePropertyDeviceIsRunningSomewhere` looks like
the answer and isn't — KAVACH holds the microphone itself so it reads true
permanently, and Bluetooth microphones report inactive regardless.

So this reads window titles, which means it is a set of heuristics, and the
tests below are mostly about being honest regarding which ones are solid. Zoom
naming its in-call window `Zoom Meeting` is a reliable signal. A browser tab
called "Meet" is a guess, and the code says so rather than pretending.

The other half is the resume rule, which is the part that could actually hurt
you: this module suspends the wake word, so it may only ever un-suspend what it
suspended. Ghost mode and the kill switch outrank it in both directions.
"""

import pytest

from kavach.privacy.meetings import (
    MeetingWatcher,
    Detection,
    detect_call,
)


def window(owner: str, name: str = "") -> dict:
    """One entry shaped like CGWindowListCopyWindowInfo returns."""
    return {"kCGWindowOwnerName": owner, "kCGWindowName": name}


# ═══ 1. the reliable signals ═══

def test_zoom_in_a_call_is_detected(   ):
    found = detect_call([window("zoom.us", "Zoom Meeting")])

    assert found is not None
    assert found.app == "Zoom"
    assert found.confidence == "high"


def test_zoom_merely_open_is_not_a_call(  ):
    """The Zoom home window is not a meeting. Muting on app-launch would make
    the feature useless — Zoom sits open all day."""
    assert detect_call([window("zoom.us", "Zoom")]) is None
    assert detect_call([window("zoom.us", "Settings")]) is None


def test_facetime_in_a_call_is_detected():
    found = detect_call([window("FaceTime", "FaceTime")])
    assert found is not None and found.app == "FaceTime"


def test_teams_in_a_call_is_detected():
    found = detect_call([window("Microsoft Teams", "Meeting in progress")])
    assert found is not None and found.app == "Teams"


def test_an_empty_desktop_is_not_a_call():
    assert detect_call([]) is None


def test_unrelated_windows_are_not_calls():
    assert detect_call([
        window("Safari", "KAVACH — GitHub"),
        window("Terminal", "brain — python"),
        window("Music", "Now Playing"),
    ]) is None


# ═══ 2. the honest one ═══

def test_google_meet_is_detected_but_marked_low_confidence():
    """A browser tab title is a guess, and must be labelled as one.

    Reported rather than hidden because the failure mode is silent: a tab
    called "Meet the team — Notion" would mute KAVACH mid-sentence and you
    would have no idea why.
    """
    found = detect_call([window("Google Chrome", "Meet — abc-defg-hij")])

    assert found is not None
    assert found.app == "Google Meet"
    assert found.confidence == "low", "a tab title must not claim to be certain"


def test_a_page_merely_mentioning_meet_is_not_a_call():
    assert detect_call([window("Google Chrome", "How to meet people")]) is None
    assert detect_call([window("Safari", "Meeting notes — Notion")]) is None


# ═══ 3. the resume rule — the part that could hurt ═══

class FakeLoop:
    def __init__(self):
        self.wake_suspended = False


def test_a_call_suspends_the_wake_word():
    loop, watcher = FakeLoop(), None
    watcher = MeetingWatcher(loop=loop)

    watcher.evaluate([window("zoom.us", "Zoom Meeting")])

    assert loop.wake_suspended is True


def test_the_call_ending_resumes_it():
    loop = FakeLoop()
    watcher = MeetingWatcher(loop=loop)
    watcher.evaluate([window("zoom.us", "Zoom Meeting")])

    watcher.evaluate([])

    assert loop.wake_suspended is False


def test_it_only_resumes_what_it_suspended():
    """If you suspended the wake word yourself, a call ending must not undo it."""
    loop = FakeLoop()
    loop.wake_suspended = True           # you turned it off, not us
    watcher = MeetingWatcher(loop=loop)

    watcher.evaluate([])                 # no call in progress

    assert loop.wake_suspended is True, "it resumed something it never suspended"


def test_a_call_ending_does_not_leave_ghost_mode():
    """Ghost outranks this entirely — in both directions."""
    from kavach.privacy.ghost import GhostMode

    loop = FakeLoop()
    ghost = GhostMode()
    ghost.enter(source="test")
    watcher = MeetingWatcher(loop=loop, ghost=ghost)

    watcher.evaluate([window("zoom.us", "Zoom Meeting")])
    watcher.evaluate([])

    assert ghost.is_active, "a call ending turned the microphone back on"
    assert loop.wake_suspended is True, "resumed while in ghost mode"


def test_a_call_ending_does_not_resume_while_latched():
    from kavach.killswitch.core import KillSwitch
    from kavach.killswitch.log import ActionLog

    loop = FakeLoop()
    ks = KillSwitch(log=ActionLog(__import__("pathlib").Path("/dev/null")))
    watcher = MeetingWatcher(loop=loop, kill_switch=ks)
    watcher.evaluate([window("zoom.us", "Zoom Meeting")])
    ks.trigger(source="test", reason="latched mid-call")

    watcher.evaluate([])

    assert loop.wake_suspended is True, "resumed while the switch was latched"


def test_staying_in_a_call_does_not_re_suspend_repeatedly():
    """Polling every 5s must not spam the log or thrash the wake word."""
    loop = FakeLoop()
    watcher = MeetingWatcher(loop=loop)

    changes = [watcher.evaluate([window("zoom.us", "Zoom Meeting")])
               for _ in range(4)]

    assert changes[0] is True, "the first poll should have changed something"
    assert changes[1:] == [False, False, False]
