"""Wake word by transcription — the reader that actually hears this microphone.

Four trained ONNX models have failed here, and the project's own measurements
say why. One utterance, one recording, two readers::

    the wake model on the file              0.858
    the wake model on the mic recording     0.019
    whisper on that same recording          "Kavec, Kavec, testing 1, 2, 3."

Whisper reads it perfectly at rms 0.09. The wake model scores it at noise. v4
finally fit the user's voice — median 0.830 on the takes it trained on — and
then scored 0.034 on speech it had not seen. 42 unique utterances cannot
generalise, and copying each 25 times only deepened the memorisation.

So this stops trying to make a deaf model hear. A voice-activity gate watches
the microphone; when a burst of speech ends, a small local Whisper transcribes
**that burst alone** and the text is matched against the wake word.

Three things make it viable rather than merely possible:

* **Silence costs nothing.** The gate is energy-based and runs on 100ms
  blocks; whisper is invoked once per burst of speech, not per block. A quiet
  room never reaches the model at all.
* **Nothing is kept.** Audio is dropped the moment a burst is scored, and the
  transcript of a burst that did not match is never logged, published or
  stored. §7 says wake-word audio that was not acted on leaves no trace, and
  text derived from that audio is the same thing.
* **It cannot take the loop down.** Everything around the transcription is
  wrapped: an exception here would end listening altogether, which is worse
  than missing a wake word.

The trade is honest: a wake takes as long as the burst plus a transcription
(a few hundred ms on a small model) rather than firing mid-word, and a running
whisper costs more CPU than a 3MB ONNX classifier. In exchange it works.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

log = logging.getLogger("kavach.voice.wakewhisper")

SAMPLE_RATE = 16_000

#: Spellings to match against — every one of them observed, none invented.
#:
#: Whisper writes a Sanskrit word by ear, and the model in use is a Hinglish
#: fine-tune that renders कवच with a *j* ending. Measured live over 90 seconds
#: of the user speaking normally, one word, eight spellings::
#:
#:     'Hai kavach, vah time is it.'    'Gavach.'
#:     'Avaj open notes.'               'Hega vaj.'
#:     'Ek avach.'                      'Gaavj.'
#:     'Hey gauj.'                      'Thik hai vajah.'
#:
#: Matching only the canonical spelling caught 3 of 12. With `gavaj`, `gauj`
#: and `vajah` added it catches 10 of 12 and still fires on none of that run's
#: ordinary speech. The variants are not a nicety here; they are the feature.
WAKE_TARGETS = ("kavach", "kawach", "kavatch", "gavaj", "gauj", "vajah")

#: How close a word must be to count. Chosen from measurement, not taste —
#: against the targets above::
#:
#:     kavach  1.000     kavec   0.727   ← the real transcript, must match
#:     kawach  1.000     catch   0.667   ← must not
#:     kavac   0.909     wash    0.600
#:     cavach  0.833     kayak   0.545
#:
#: 0.70 is the only gap in that ordering. Lower and "catch" wakes it; higher
#: and the one transcript we have actually measured does not.
MATCH_RATIO = 0.70

_WORD_RE = re.compile(r"[a-z']+")


def matches_wake(text: str | None) -> bool:
    """Whether a transcript contains the wake word, however it was spelled.

    Word-level rather than whole-string: "ok kavach what time is it" is a wake
    followed by a command, and the user will not pause obligingly between them.
    """
    if not text:
        return False
    for word in _WORD_RE.findall(text.lower()):
        if len(word) < 4:
            # Too short to be this word, and short words are where fuzzy
            # matching produces nonsense.
            continue
        if any(SequenceMatcher(None, word, target).ratio() >= MATCH_RATIO
               for target in WAKE_TARGETS):
            return True
    return False


@dataclass
class WakeHeard:
    """A burst of speech that contained the wake word."""

    text: str
    seconds: float


class Segmenter:
    """Turns a stream of audio blocks into complete utterances.

    Energy-gated on purpose. The alternative — transcribing continuously — is
    both ruinous for CPU and exactly the ambient-audio behaviour that was cut
    from this project on privacy grounds.
    """

    #: Above this, a block counts as speech. Room tone on this machine sits
    #: around 0.005 rms; ordinary speech is 0.02-0.08.
    SPEECH_RMS = 0.015
    #: A burst has to last this long to be worth transcribing. Below it, it is
    #: a key click, a cough, or a chair.
    MIN_UTTERANCE_S = 0.35
    #: Longer than this and it is cut and scored anyway, so someone talking on
    #: a call cannot accumulate audio in memory waiting for a pause.
    MAX_UTTERANCE_S = 3.0
    #: Silence this long ends a burst. Long enough to survive the stop in
    #: "kav-ACH", short enough not to add a wait to every wake.
    HANG_S = 0.35

    def __init__(self) -> None:
        self._parts: list[np.ndarray] = []
        self._silence = 0.0
        #: Seconds of *speech*, not of tape. The minimum has to be measured
        #: against this: the buffer also holds the trailing silence that ends
        #: the burst, so a 50ms click plus its hang-over would otherwise clear
        #: a 350ms minimum and cost a transcription.
        self._speech = 0.0
        self._speaking = False

    @property
    def buffered_seconds(self) -> float:
        return sum(len(p) for p in self._parts) / SAMPLE_RATE

    def reset(self) -> None:
        self._parts.clear()
        self._silence = 0.0
        self._speech = 0.0
        self._speaking = False

    def push(self, block: np.ndarray) -> np.ndarray | None:
        """Feed one block. Returns a complete utterance, or None."""
        if block is None or len(block) == 0:
            return None

        seconds = len(block) / SAMPLE_RATE
        loud = float(np.sqrt(np.mean(block.astype(np.float64) ** 2))) >= self.SPEECH_RMS

        if loud:
            self._speaking = True
            self._silence = 0.0
            self._speech += seconds
            self._parts.append(block.astype(np.float32))
            if self.buffered_seconds >= self.MAX_UTTERANCE_S:
                return self._close()
            return None

        if not self._speaking:
            # Silence outside a burst: nothing to keep, and keeping it would
            # mean holding a rolling recording of the room.
            return None

        # Trailing silence is kept while the burst might still continue — a
        # word cut at its own last consonant transcribes badly.
        self._parts.append(block.astype(np.float32))
        self._silence += seconds
        if self._silence >= self.HANG_S:
            return self._close()
        return None

    def _close(self) -> np.ndarray | None:
        audio = np.concatenate(self._parts) if self._parts else None
        spoken = self._speech
        self.reset()
        if audio is None or spoken < self.MIN_UTTERANCE_S:
            return None
        return audio


class WhisperWakeDetector:
    """Listens for the wake word by transcribing bursts of speech.

    Deliberately the same shape as `WakeWordDetector` — `available`, `load()`,
    `push(block)` — so the voice loop can hold either without knowing which.
    """

    #: A registry name, or anything pywhispercpp accepts. `swift` is a
    #: whisper-base Hinglish fine-tune already on disk here (148MB) — small
    #: enough to run per burst, and tuned for exactly the phonetics of a
    #: Sanskrit wake word.
    DEFAULT_MODEL = "swift"
    #: Used when the chosen model is not installed. Small, English, and
    #: fetched by pywhispercpp on demand: a missing file must not stop KAVACH
    #: listening, the same rule `stt_models.resolve()` follows.
    FALLBACK_MODEL = "base.en"

    def __init__(self, stt=None, model: str | None = None) -> None:
        #: Injected for the tests, and so the loop can share a model rather
        #: than loading a second copy.
        self.stt = stt
        self.model = model or self.DEFAULT_MODEL
        self._segmenter = Segmenter()

    #: The loop refuses to use an uncalibrated ONNX model, because a threshold
    #: nobody measured fires on room noise. This detector has no threshold to
    #: measure — it matches text — so that gate does not apply to it.
    needs_calibration = False

    @property
    def available(self) -> bool:
        return True

    #: Shown in the loop's startup banner and log lines, where the ONNX
    #: detector reports the file it loaded.
    @property
    def model_path(self) -> str:
        return f"whisper:{self.model}"

    def reset(self) -> None:
        """Drop anything buffered. Called after every turn (§7)."""
        self._segmenter.reset()

    @property
    def buffered_seconds(self) -> float:
        return self._segmenter.buffered_seconds

    def resolved_model(self) -> str:
        """What to hand pywhispercpp: a path for a registry model, else a name."""
        try:
            from .stt_models import REGISTRY, is_installed

            entry = REGISTRY.get(self.model)
            if entry is None or entry.repo_id is None:
                return self.model          # already a stock name
            if is_installed(self.model):
                return str(entry.local_path())
            log.warning("wake model %r is not downloaded — using %s",
                        self.model, self.FALLBACK_MODEL)
        except Exception:
            log.debug("could not resolve %r", self.model, exc_info=True)
        return self.FALLBACK_MODEL

    def load(self) -> None:
        if self.stt is not None:
            return
        from .stt import SpeechToText

        resolved = self.resolved_model()
        log.info("loading %s for wake-word transcription", self.model)
        self.stt = SpeechToText(resolved)
        self.stt.load()

    def push(self, block: np.ndarray) -> WakeHeard | None:
        """Feed one block of microphone audio. Returns a wake, or None."""
        try:
            utterance = self._segmenter.push(block)
        except Exception:
            log.debug("segmenter failed", exc_info=True)
            self._segmenter.reset()
            return None

        if utterance is None:
            return None
        return self._score(utterance)

    def _score(self, utterance: np.ndarray) -> WakeHeard | None:
        seconds = len(utterance) / SAMPLE_RATE
        try:
            if self.stt is None:
                self.load()
            result = self.stt.transcribe(utterance)
            text = (getattr(result, "text", "") or "").strip()
        except Exception:
            # Never propagate: this runs on the microphone thread, and an
            # exception here stops KAVACH listening at all — strictly worse
            # than missing one wake word.
            log.debug("wake transcription failed", exc_info=True)
            return None
        finally:
            # Whether it matched or not, the audio is gone by here.
            del utterance

        if not matches_wake(text):
            # §7: what KAVACH heard and did not act on leaves no trace. The
            # transcript is not logged, not published, not counted — the text
            # is the audio, in another form.
            log.debug("wake: no (%.1fs)", seconds)
            return None

        log.info("wake word heard (%.1fs)", seconds)
        return WakeHeard(text=text, seconds=seconds)
