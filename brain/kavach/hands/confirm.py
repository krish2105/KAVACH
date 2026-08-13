"""Spoken confirmation (spec §7).

> KAVACH should speak the action back to you and wait for a spoken or
> Space-bar confirmation.

Every branch that isn't an unambiguous yes returns **False**. Not just the
explicit "no": a timeout, an empty transcript, a Whisper confabulation, a
kill switch fired mid-question, an exception in the audio path. Consent has to
be given, never merely not-withheld.

That asymmetry is deliberate and is why this module has no "assume yes"
fallback anywhere in it.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("kavach.hands.confirm")

#: Deliberately short and unambiguous. "sure" and "ok" are absent: they show
#: up in ordinary speech far too easily to authorise deleting something.
_AFFIRMATIVE = {
    "yes", "yeah", "yep", "confirm", "confirmed", "do it", "go ahead",
    "yes please", "affirmative", "approved", "proceed",
}
_NEGATIVE = {
    "no", "nope", "cancel", "stop", "don't", "dont", "abort", "never mind",
    "nevermind", "negative",
}

CONFIRM_TIMEOUT_S = 12.0


def interpret(transcript: str) -> bool | None:
    """True = yes, False = no, None = didn't understand.

    None is treated as a denial by the caller. An unparsed answer to "shall I
    delete this?" must never be read as permission.
    """
    text = (transcript or "").strip().casefold().rstrip(".!?")
    if not text:
        return None
    if text in _AFFIRMATIVE:
        return True
    if text in _NEGATIVE:
        return False
    # Allow a little politeness around the keyword ("yes, go ahead").
    words = set(text.replace(",", " ").split())
    if words & {"yes", "confirm", "confirmed", "yeah", "yep", "proceed"}:
        return True
    if words & {"no", "cancel", "stop", "abort", "nope"}:
        return False
    return None


class VoiceConfirmer:
    """Speaks the action back and waits for the user's voice.

    Holds a reference to the running :class:`~kavach.voice.loop.VoiceLoop` so
    it can borrow its microphone, STT and TTS rather than opening a second
    audio stack.
    """

    def __init__(self, voice_loop, timeout: float = CONFIRM_TIMEOUT_S):
        self.voice = voice_loop
        self.timeout = timeout

    async def confirm(self, prompt: str) -> bool:
        try:
            return await asyncio.wait_for(self._ask(prompt), timeout=self.timeout)
        except asyncio.TimeoutError:
            log.warning("confirmation timed out — treating as no")
            self.voice.ks.log.append("confirm.timeout", prompt=prompt)
            return False
        except Exception:
            log.exception("confirmation failed — treating as no")
            return False

    async def _ask(self, prompt: str) -> bool:
        voice = self.voice

        # A kill switch that fired between the request and the question means
        # the answer is already no.
        if not voice.ks.is_armed:
            return False

        voice.set_state("speaking", transcript=prompt)
        await asyncio.to_thread(voice.speak, prompt)

        if not voice.ks.is_armed:
            return False

        voice.set_state("listening", transcript=prompt, partial="")
        audio = await asyncio.to_thread(voice._record_with_meter)

        from ..voice import stt as stt_mod

        if len(audio) < 16_000 * 0.2 or stt_mod.is_probably_silence(audio):
            log.info("no answer heard — treating as no")
            voice.ks.log.append("confirm.no_answer", prompt=prompt)
            return False

        voice.set_state("thinking", transcript=prompt)
        result = await asyncio.to_thread(voice.stt.transcribe, audio)
        verdict = interpret(result.text)

        voice.ks.log.append(
            "confirm.answer",
            prompt=prompt,
            heard=result.text,
            verdict={True: "yes", False: "no", None: "unclear"}[verdict],
        )
        log.info("confirmation heard %r → %s", result.text, verdict)

        # None (didn't understand) is a denial, not a retry.
        return verdict is True
