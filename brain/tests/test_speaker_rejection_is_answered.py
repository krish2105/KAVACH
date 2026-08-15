"""A refused turn must say so. Silence is the worst answer available.

Measured live, minutes after the gate was switched on (threshold 0.300):

    20:30:14  similarity 0.6154  voice matches                    ✓
    20:30:21  similarity 0.0     clip too short to verify (2.16s) ✗
    20:30:21  voice.rejected                                       → dropped
    20:30:30  similarity 0.3177  voice matches                    ✓

The middle turn was discarded with **no output of any kind** — no speech, no
orb change, nothing in the transcript. From the user's side that is
indistinguishable from a dead microphone, a crashed daemon, or a hotkey that
did not register. They pressed the key, spoke, and the machine sat there.

An assistant that ignores you without saying why teaches you to stop
trusting it, and the CHI 2023 finding this project already cites is that
abandonment is **task-specific** — one unexplained failure ends that task
permanently.

**Refusing is right; refusing silently is not.** Nothing here loosens the
gate: the turn is still discarded, still never transcribed, still logged.
The only change is that the person in the room is told.

The two reasons need different words, because they need different actions:

* **too short** — the gate cannot judge a clip under `MIN_VERIFY_SECONDS`,
  so this is not a verdict about the voice at all. Saying more, for longer,
  fixes it. This is the common case: a 2.16s "open Terminal" is an ordinary
  command, not an attack.
* **does not match** — an actual verdict. Worth distinguishing so a genuine
  false rejection is legible as one rather than looking like a stutter.
"""

import numpy as np
import pytest

from kavach.identity.voiceprint import VerificationResult
from kavach.voice.loop import rejection_message


def test_a_clip_too_short_is_told_to_say_more():
    result = VerificationResult(
        False, 0.0, 0.3, "clip too short to verify (2.16s)")

    said = rejection_message(result)

    assert said, "a discarded turn produced no message at all"
    assert "short" in said.lower() or "longer" in said.lower(), said


def test_the_short_message_does_not_accuse_the_speaker():
    """It is not a verdict about the voice — the encoder never ran. Telling
    someone they were not recognised, when they were never judged, sends them
    to re-enrol for a problem that is about duration."""
    result = VerificationResult(
        False, 0.0, 0.3, "clip too short to verify (2.16s)")

    said = rejection_message(result).lower()

    assert "match" not in said, said
    assert "recognise" not in said and "recognize" not in said, said


def test_a_voice_mismatch_says_so():
    result = VerificationResult(
        False, 0.11, 0.3, "voice does not match the enrolled speaker")

    said = rejection_message(result)

    assert said
    assert said != rejection_message(
        VerificationResult(False, 0.0, 0.3, "clip too short to verify (2.1s)")
    ), "both refusals give the same message, so neither tells you what to do"


def test_an_accepted_result_says_nothing():
    """Only refusals speak. A confirmation on every successful turn is noise
    that gets the whole feature switched off."""
    assert rejection_message(
        VerificationResult(True, 0.62, 0.3, "voice matches")) == ""


def test_the_messages_are_short():
    """TTS is ~5s on this machine and 74% of a turn's latency. A paragraph
    spoken at someone whose command was just dropped is a second failure."""
    for result in (
        VerificationResult(False, 0.0, 0.3, "clip too short to verify (2.1s)"),
        VerificationResult(False, 0.1, 0.3, "voice does not match the enrolled speaker"),
    ):
        message = rejection_message(result)
        assert len(message) <= 90, f"{len(message)} chars: {message}"


# ═══ the gate itself is unchanged ═══

def test_the_turn_is_still_discarded(monkeypatch):
    """The point of this change is feedback, not permission. A rejected turn
    must still never reach transcription."""
    import inspect

    from kavach.voice.loop import VoiceLoop

    source = inspect.getsource(VoiceLoop._handle_audio) if hasattr(
        VoiceLoop, "_handle_audio") else inspect.getsource(VoiceLoop)
    # The rejection branch returns before `self.stt.transcribe`.
    before, _, after = source.partition("voice.rejected")
    assert after, "the rejection branch is gone"
    assert "return" in after.split("self.stt.transcribe")[0], (
        "a rejected turn no longer returns before transcription"
    )
