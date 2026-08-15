"""Show the confirmation before speaking it.

Measured, from CLAUDE.md::

    clock turn   tts 4941ms   → "TTS is 74% of the wait"
    Notes turn   tts 4310ms   → "TTS is 52% of the wait again"

Speaking a confirmation costs ~5s to read out, plus the answer, plus ~5s for
the result — **~12s per shell command**. With every shell command confirming
(spec §2.1), that is the difference between a guardrail and a guardrail the
user routes around, and an ignored guardrail is worse than a calibrated one.

The orb is already there, already receives the snapshot stream, and already
knows when a confirmation is pending. It can *show* the command in one frame
instead of speaking it in five thousand milliseconds.

So the question is published first and the gesture window opens immediately.
Speech becomes a **fallback** after `SPEAK_AFTER_S` — which matters, because
the whole point of a voice assistant is that it works with your back turned.

Deliberately **not** a new orb state. `OrbState` in `apps/orb/lib/orbScene.ts`
is a closed union with a `Record<OrbState, StateProfile>` of visuals, so a
"confirming" state means a Presence-layer change and a visual to verify.
`listening` is what this is — KAVACH waiting for your answer — and the prompt
rides in `transcript`, which the HUD already renders.
"""

import asyncio
import threading

import pytest

from kavach.hands.confirm import VoiceConfirmer


class FakeVoice:
    """Enough of VoiceLoop to drive a confirmation, recording the order."""

    def __init__(self, *, armed: bool = True):
        self.states: list[tuple[str, dict]] = []
        self.spoke: list[str] = []
        self._gesture_answer = None
        self._gesture_event = threading.Event()
        self.ks = FakeKillSwitch(armed)
        self.stt = None

    # — what the confirmer touches —
    def set_state(self, state: str, **fields) -> None:
        self.states.append((state, fields))

    def speak(self, text: str) -> None:
        self.spoke.append(text)

    def arm_gesture_answer(self) -> None:
        self._gesture_answer = None
        self._gesture_event.clear()

    def take_gesture_answer(self):
        answer, self._gesture_answer = self._gesture_answer, None
        self._gesture_event.clear()
        return answer

    def answer_confirmation(self, approved: bool) -> None:
        self._gesture_answer = approved
        self._gesture_event.set()

    def _record_with_meter(self):
        # Nobody spoke. Long enough that the gesture path always wins first.
        import numpy as np
        self._gesture_event.wait(5.0)
        return np.zeros(10, dtype="float32")


class FakeLog:
    def __init__(self):
        self.entries = []

    def append(self, event, **fields):
        self.entries.append((event, fields))


class FakeKillSwitch:
    def __init__(self, armed: bool):
        self.is_armed = armed
        self.log = FakeLog()


# ═══ the orb sees it first ═══

@pytest.mark.asyncio
async def test_the_question_is_published_before_anything_is_spoken():
    voice = FakeVoice()
    confirmer = VoiceConfirmer(voice, timeout=2.0)
    confirmer.SPEAK_AFTER_S = 5.0          # long enough that it cannot speak

    task = asyncio.create_task(confirmer.confirm("run the shell command: ls"))
    await asyncio.sleep(0.1)

    assert voice.states, "nothing reached the orb at all"
    state, fields = voice.states[0]
    assert "ls" in fields.get("transcript", ""), "the orb never saw the command"
    assert voice.spoke == [], "it spoke before showing"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_answering_on_the_orb_costs_no_speech_at_all():
    """The 12 seconds this exists to remove."""
    voice = FakeVoice()
    confirmer = VoiceConfirmer(voice, timeout=2.0)
    confirmer.SPEAK_AFTER_S = 5.0

    task = asyncio.create_task(confirmer.confirm("delete the note"))
    await asyncio.sleep(0.05)
    voice.answer_confirmation(True)

    assert await task is True
    assert voice.spoke == [], "it spoke even though the orb answered"


@pytest.mark.asyncio
async def test_declining_on_the_orb_is_honoured_immediately():
    voice = FakeVoice()
    confirmer = VoiceConfirmer(voice, timeout=2.0)
    confirmer.SPEAK_AFTER_S = 5.0

    task = asyncio.create_task(confirmer.confirm("delete everything"))
    await asyncio.sleep(0.05)
    voice.answer_confirmation(False)

    assert await task is False
    assert voice.spoke == []


# ═══ but it still works with your back turned ═══

@pytest.mark.asyncio
async def test_it_speaks_when_the_orb_goes_unanswered():
    """A confirmation nobody sees must not wait silently until it times out.
    That would read as KAVACH ignoring you."""
    voice = FakeVoice()
    confirmer = VoiceConfirmer(voice, timeout=1.5)
    confirmer.SPEAK_AFTER_S = 0.05

    task = asyncio.create_task(confirmer.confirm("run the shell command: ls"))
    await asyncio.sleep(0.4)

    assert voice.spoke, "silent forever when unattended"
    assert "ls" in voice.spoke[0]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ═══ nothing that was true before stopped being true ═══

@pytest.mark.asyncio
async def test_a_timeout_is_still_a_no():
    voice = FakeVoice()
    confirmer = VoiceConfirmer(voice, timeout=0.2)
    confirmer.SPEAK_AFTER_S = 0.05

    assert await confirmer.confirm("delete the note") is False


@pytest.mark.asyncio
async def test_a_latched_kill_switch_answers_no_without_asking():
    """A halt between the request and the question means the answer is
    already no — and nothing should be spoken or shown."""
    voice = FakeVoice(armed=False)
    confirmer = VoiceConfirmer(voice, timeout=1.0)

    assert await confirmer.confirm("delete the note") is False
    assert voice.spoke == []


@pytest.mark.asyncio
async def test_a_gesture_from_before_the_question_cannot_answer_it():
    """Arming clears stale state. The window opens earlier now than it used
    to — before the speech rather than after — so this matters more, not
    less: a thumbs-up at the orb must not authorise something never asked."""
    voice = FakeVoice()
    voice.answer_confirmation(True)        # made BEFORE any question
    confirmer = VoiceConfirmer(voice, timeout=0.3)
    confirmer.SPEAK_AFTER_S = 5.0

    assert await confirmer.confirm("delete the note") is False


@pytest.mark.asyncio
async def test_the_answer_is_logged_with_how_it_arrived():
    """§7 — and 'via' matters, because an orb answer and a spoken one have
    different threat models."""
    voice = FakeVoice()
    confirmer = VoiceConfirmer(voice, timeout=2.0)
    confirmer.SPEAK_AFTER_S = 5.0

    task = asyncio.create_task(confirmer.confirm("delete the note"))
    await asyncio.sleep(0.05)
    voice.answer_confirmation(True)
    await task

    answers = [f for e, f in voice.ks.log.entries if e == "confirm.answer"]
    assert answers, "an approval left no record"
    assert answers[0]["verdict"] == "yes"
    assert answers[0]["via"] == "gesture"
