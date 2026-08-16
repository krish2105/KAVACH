"""Answering a pending confirmation skips the speaker check — by decision.

**This narrows what the gate protects against, and the narrowing is the point
of writing it down.**

A one-word answer cannot be speaker-verified. "confirm" is roughly 0.5s of
voiced sound inside a 2.4s clip, `MIN_VERIFY_SECONDS` is 3.0, and lowering the
floor would not fix it — an embedding built mostly from silence is unreliable
at any clip length. Measured live 2026-08-15, with a correctly calibrated
0.383 threshold and everything else working::

    voice.rejected  similarity 0.0  threshold 0.3825
                    reason: "clip too short to verify (2.42s)"

The system was right and said so. The design was what blocked: the
confirmation flow asks a question whose answer it cannot verify, so a spoken
approval could never succeed.

**What the confirmation still guarantees:** KAVACH does not delete, send, buy,
submit or change a system setting without reading the action back and waiting
for an affirmative answer. It cannot act unasked, and it cannot act on a
timeout — expiry is a denial.

**What it no longer guarantees:** that the *answer* came from the enrolled
speaker. Someone else in the room, within the 120s window, having heard the
prompt, could say yes.

The user chose this knowingly over three alternatives (a longer confirmation
phrase, trusting the verified command turn, or measuring first). Recorded here
so a later reader does not "restore" a check that was removed on purpose —
and so nothing in the product claims a protection it does not have.
"""

import numpy as np
import pytest

from kavach.voice.loop import should_verify_speaker


class Registry:
    def __init__(self, pending):
        self._pending = pending

    def list(self):
        return self._pending


# ═══ the decision ═══

def test_a_pending_confirmation_skips_the_speaker_check():
    """The answer is one word and one word cannot be verified."""
    assert not should_verify_speaker(gating=True, pending=Registry(["a prompt"]))


def test_an_ordinary_turn_still_checks():
    """Commands are long enough to verify, and a command can act. This is
    where the check earns its keep."""
    assert should_verify_speaker(gating=True, pending=Registry([]))


def test_nothing_is_checked_when_the_gate_is_off():
    assert not should_verify_speaker(gating=False, pending=Registry([]))
    assert not should_verify_speaker(gating=False, pending=Registry(["x"]))


def test_no_registry_is_an_ordinary_turn():
    """A missing registry must not read as "a confirmation is pending" and
    silently disable the check on every turn."""
    assert should_verify_speaker(gating=True, pending=None)


def test_a_broken_registry_does_not_disable_the_check():
    """Denial is the default. If we cannot tell whether a confirmation is
    pending, the safe reading is that one is not — so the check runs."""

    class Broken:
        def list(self):
            raise RuntimeError("registry unavailable")

    assert should_verify_speaker(gating=True, pending=Broken())


def test_lowering_the_duration_floor_did_not_re_enable_this():
    """`MIN_VERIFY_SECONDS` moved 3.0 → 2.0 when it was re-measured against
    ECAPA, so a 2.4s "confirm" clip now clears the duration check.

    The exemption must remain unconditional. It was justified by the clip
    being mostly silence — 0.5s of voice in 2.4s — not by the floor, and the
    re-measurement supports it: at 1.0s and under, the enrolled speaker and
    strangers overlap. The skip is now doing the work the floor used to do
    incidentally, so removing it would add a wrong check rather than restore
    a right one.
    """
    from kavach.identity.voiceprint import MIN_VERIFY_SECONDS

    assert MIN_VERIFY_SECONDS <= 2.4, "the premise of this test has lapsed"

    class Pending:
        def list(self):
            return [type("P", (), {"payload": {"tool": "delete_file"}})()]

    assert should_verify_speaker(gating=True, pending=Pending()) is False
