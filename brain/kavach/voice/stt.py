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
        return Transcript(text=text.strip(), segments=len(segments), model=self.model_name)
