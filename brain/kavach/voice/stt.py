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


#: How sure Whisper must be before KAVACH answers in that language.
#:
#: languages.py already states the principle — "a reply spoken in the wrong
#: language is worse than one spoken in the default" — and this is where it is
#: enforced. Hindi speech measured 0.85 on this machine and the runner-up was
#: 0.10, so a floor here is comfortably clear of a real detection while still
#: rejecting a coin-flip.
MIN_LANGUAGE_CONFIDENCE = 0.5


#: Unicode ranges that identify a language on sight, mapped to the Whisper
#: codes `languages.py` knows voices for.
#:
#: Only scripts that are unambiguous. Latin is deliberately absent: it cannot
#: separate English from Spanish or a romanised Hinglish transliteration, and
#: guessing between them is exactly the wrong-language reply this avoids.
_SCRIPTS: tuple[tuple[str, tuple[tuple[int, int], ...]], ...] = (
    # Kana before Han: Japanese uses both, Mandarin uses only Han, so a text
    # containing kana is Japanese regardless of how much kanji sits beside it.
    ("ja", ((0x3040, 0x309F), (0x30A0, 0x30FF))),
    ("hi", ((0x0900, 0x097F),)),                     # Devanagari
    ("zh", ((0x4E00, 0x9FFF),)),                     # Han
)

#: How much of the text must be in one script before it decides the reply.
#:
#: A rupee sign in an English sentence, or one quoted word, is noise. A third
#: is high enough to ignore that and low enough that "मेरी meeting कितने बजे है"
#: — which is how people actually speak — still counts as Hindi.
_SCRIPT_SHARE = 0.30


def language_of_script(text: str) -> str | None:
    """The language implied by the writing system, or None if it is Latin.

    Free, unlike `detect_language()`, and exact for the scripts it covers.
    whisper.cpp detects the language internally during an `auto` transcribe and
    then does not expose it — the only way to ask costs a second encoder pass,
    measured at 597 ms against a 609 ms transcribe. The returned text already
    carries the answer for Hindi, Japanese and Mandarin, so it is read from
    there instead.
    """
    if not text or not text.strip():
        return None

    letters = [c for c in text if c.isalpha() or ord(c) > 0x2000]
    if not letters:
        return None

    for code, ranges in _SCRIPTS:
        hits = sum(1 for c in letters
                   if any(low <= ord(c) <= high for low, high in ranges))
        if hits / len(letters) >= _SCRIPT_SHARE:
            return code
    return None


#: Hindi function words as people actually type them in Roman script.
#:
#: Function words only. Content words like "meeting", "time" or "office" are
#: shared with English and would flag ordinary English sentences; grammar words
#: are what make a sentence Hindi regardless of its vocabulary. "aaj meri
#: meeting kitne baje hai" is four English letters' worth of English and
#: entirely Hindi structure.
#:
#: Spelling is not standardised in romanised Hindi — "hai/hain", "kya/kyaa",
#: "nahi/nahin" — so common variants are listed rather than stemmed.
_HINGLISH_MARKERS = frozenset({
    "hai", "hain", "haan", "nahi", "nahin", "nai",
    "kya", "kyaa", "kyun", "kyon", "kaise", "kaisa", "kaisi",
    "kitne", "kitna", "kitni", "kab", "kahan", "kaun",
    "mera", "meri", "mere", "tera", "teri", "aap", "aapka", "aapki",
    "mujhe", "mujhko", "hum", "humein", "tum", "tumhara",
    "aaj", "kal", "abhi", "phir", "bhi", "hi", "toh", "tha", "thi", "the",
    "karo", "kar", "karna", "karke", "diya", "dijiye", "batao", "bata",
    "chahiye", "sakta", "sakte", "sakti", "raha", "rahi", "rahe",
    "acha", "accha", "theek", "thik", "bas", "sab", "kuch", "koi",
    "baje", "waqt", "samay", "din", "raat", "subah", "shaam",
    "ka", "ki", "ke", "ko", "se", "mein", "par", "aur", "ya",
})

#: How many marker words before a Latin sentence counts as Hinglish.
#:
#: Two, not one: "say namaste to the team" and "order some chai" are English
#: sentences with a borrowed word, and flipping the whole reply into Hinglish
#: for one loan word would be the wrong-language failure in miniature.
_HINGLISH_MIN_MARKERS = 2


def is_romanised_hindi(text: str) -> bool:
    """Whether Latin-script text is actually Hindi written in Roman letters.

    The script test cannot answer this — Hinglish and English are both Latin —
    and it is the difference between mirroring what you said and answering a
    Hindi question in English.
    """
    if not text or language_of_script(text) is not None:
        return False

    words = [w.strip(".,!?;:'\"()").lower() for w in text.split()]
    hits = sum(1 for w in words if w in _HINGLISH_MARKERS)
    return hits >= _HINGLISH_MIN_MARKERS


def reply_style(text: str) -> str:
    """Which of the three styles to answer in: hindi, hinglish or english.

    Derived from what was heard rather than asked of a model, so it costs
    nothing and cannot disagree with the transcript on screen.
    """
    if language_of_script(text) == "hi":
        return "hindi"
    if is_romanised_hindi(text):
        return "hinglish"
    return "english"


def detect_language(model, audio) -> str | None:
    """What language was actually spoken, or None if it is not clear.

    `get_params()["language"]` — which this used to read — returns the language
    we *configured*, not the one Whisper *heard*. It answered "en" for every
    turn, so the multilingual reply built in Phase 8 could never fire.

    Returns None rather than raising: Whisper is holding the user's actual
    words at this point, and a failed language guess should cost them the
    accent, not the answer.
    """
    try:
        top, _probabilities = model.auto_detect_language(audio)
        code, confidence = top
    except Exception:
        log.debug("language detection failed; replying in the default",
                  exc_info=True)
        return None

    if not code or float(confidence) < MIN_LANGUAGE_CONFIDENCE:
        log.info("language %r only %.2f confident — using the default",
                 code, float(confidence or 0))
        return None
    return str(code)


class SpeechToText:
    def __init__(self, model_name: str | None = None, n_threads: int | None = None):
        # §21. None means "whatever is selected", which is stock unless you
        # chose otherwise — and resolves back to stock if the chosen model is
        # not on disk. An explicit name still wins, so tests and --stt-model
        # can pin one.
        if model_name is None:
            from .stt_models import resolve

            model_name = resolve()
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

    def transcribe(self, audio: np.ndarray,
                   language: str | None = None) -> Transcript:
        """Transcribe 16 kHz mono float32 audio.

        `language` pins the decoder. Leave it None for ordinary turns — see
        the note below on why `auto` is load-bearing there. The **wake word**
        passes `"en"`, and that is a different problem with a different
        answer: it needs one word spotted, not a sentence understood.
        Measured on 42 real recordings of this user's wake word::

            auto   recognised 12/42   and wrote 'ковыч!' and 'कवच'
            en     recognised 17/42   and wrote 'kovach.' and 'go watch.'

        `matches_wake` extracts `[a-z']+`, so a correct transcription in
        Devanagari or Cyrillic is invisible to it — the model heard the word
        and the matcher threw it away. Pinning to English makes whisper
        transliterate instead, which is the form the matcher can read.
        """
        if self._model is None:
            self.load()
        assert self._model is not None

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # `auto` is what fixes the transcription. Left unset, the decoder is
        # pinned to English and Hindi comes back as a mistranslation — "today
        # my meeting is how many hours are" — which is what KAVACH did until
        # now. whisper.cpp detects internally in this mode and decodes properly.
        #
        # The *reply* language is then read off the returned script, which is
        # free. Asking whisper.cpp what it detected costs a whole second
        # encoder pass (597 ms against a 609 ms transcribe) and is available
        # via KAVACH_DETECT_LANGUAGE=full for the Latin-script languages the
        # script test cannot separate.
        if language is None and os.environ.get(
                "KAVACH_DETECT_LANGUAGE", "").lower() == "full":
            language = detect_language(self._model, audio)

        segments = self._model.transcribe(audio, language=language or "auto")
        text = " ".join(s.text.strip() for s in segments).strip()
        # Whisper emits these for silence or non-speech rather than returning
        # nothing, and they should not reach the router as a user utterance.
        for marker in ("[BLANK_AUDIO]", "(silence)", "[silence]", "[ Silence ]"):
            text = text.replace(marker, "")
        text = text.strip()

        if is_hallucination(text):
            log.info("discarding known silence hallucination: %r", text)
            text = ""

        if language is None:
            language = language_of_script(text)

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
