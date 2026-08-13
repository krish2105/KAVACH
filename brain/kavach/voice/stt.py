"""Speech-to-text via whisper.cpp (Metal-accelerated).

Uses `pywhispercpp`, which ships prebuilt macOS arm64 wheels — so despite spec
§3 implying a whisper.cpp build, no cmake and no compilation are needed here.

Model is `large-v3-turbo`: best accuracy of the turbo line and still fast on
Apple Silicon. It is downloaded once to the platform cache, not into the repo.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("kavach.voice.stt")

DEFAULT_MODEL = "large-v3-turbo"


@dataclass
class Transcript:
    text: str
    # Whisper reports no confidence directly; segment count is a weak proxy for
    # how fragmented the decode was, which is worth logging while tuning.
    segments: int
    model: str
    #: ISO-639-1 code Whisper detected, or None. Drives the reply voice.
    language: str | None = None


class SpeechToText:
    def __init__(self, model_name: str = DEFAULT_MODEL, n_threads: int | None = None):
        self.model_name = model_name
        self.n_threads = n_threads or max(4, (os.cpu_count() or 8) - 2)
        self._model = None

    def load(self) -> None:
        """Load the model, downloading it on first use.

        Called explicitly at startup rather than lazily on the first turn:
        loading costs seconds and would otherwise be charged to the user's
        first sentence, which is exactly where latency is most noticeable.
        """
        if self._model is not None:
            return
        from pywhispercpp.model import Model

        log.info("loading whisper model %s (first run downloads it)", self.model_name)
        self._model = Model(
            self.model_name,
            n_threads=self.n_threads,
            print_progress=False,
            print_realtime=False,
            single_segment=False,
        )
        log.info("whisper ready (%d threads)", self.n_threads)

    def transcribe(self, audio: np.ndarray) -> Transcript:
        """Transcribe 16 kHz mono float32 audio."""
        if self._model is None:
            self.load()
        assert self._model is not None

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        segments = self._model.transcribe(audio)
        text = " ".join(s.text.strip() for s in segments).strip()
        # Whisper emits these for silence or non-speech rather than returning
        # nothing, and they should not reach the router as a user utterance.
        for marker in ("[BLANK_AUDIO]", "(silence)", "[silence]", "[ Silence ]"):
            text = text.replace(marker, "")
        text = text.strip()

        if is_hallucination(text):
            log.info("discarding known silence hallucination: %r", text)
            text = ""

        language = None
        try:
            # whisper.cpp exposes the language it settled on; used to pick
            # a matching Kokoro voice for the reply.
            language = self._model.get_params().get("language") or None
            if language in ("auto", ""):
                language = None
        except Exception:
            pass

        return Transcript(text=text, segments=len(segments),
                          model=self.model_name, language=language)


# Whisper does not return nothing for silence — it confabulates, and always
# from the same small set drawn from its training data (YouTube captions).
# Observed here: near-silent audio transcribed as "Thank you."
#
# This matters well beyond tidiness: from Phase 4 these strings reach a router
# that can act on them. An assistant that invents commands out of room noise
# is the exact failure §7 exists to prevent.
_HALLUCINATIONS = {
    "thank you.", "thank you", "thanks for watching!", "thanks for watching.",
    "you", "you.", "bye.", "bye", ".", "so", "so.", "oh", "oh.",
    "please subscribe", "subtitles by the amara.org community",
    "thank you for watching.", "thank you for watching",
    "i'm going to go get some water.",
}


def is_hallucination(text: str) -> bool:
    return text.strip().casefold() in _HALLUCINATIONS


def is_probably_silence(audio: np.ndarray, threshold: float = 0.006) -> bool:
    """True if the clip carries no plausible speech energy.

    Cheaper and far more reliable than trying to filter Whisper's output after
    the fact — and it also saves a multi-second decode on nothing.
    """
    if not len(audio):
        return True
    rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
    peak = float(np.max(np.abs(audio)))
    return rms < threshold or peak < 0.02
