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
import queue
import re
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher

import numpy as np

log = logging.getLogger("kavach.voice.wakewhisper")

SAMPLE_RATE = 16_000

#: The wake phrase. Two ordinary English words, on purpose.
#:
#: **"Kavach" was abandoned after seven attempts**, and the final
#: measurements say why — none of them about this code::
#:
#:     "Kavach" inside a sentence   dropped 7/7 by whisper
#:     "Kavach" on its own          17–24 of 42, and often ''
#:     whisper's spellings          'cabbage', 'go watch', 'coverage', 'कवच'
#:
#: It is a Sanskrit word whisper has no representation for, so it drops it,
#: transliterates it inconsistently, or writes it in another script. Four
#: trained ONNX models and five fixes downstream were all treating symptoms.
#:
#: "hey there" transcribes identically every time, takes ~0.6s so nothing
#: discards it for being too short, and has no rare token to lose.
WAKE_PHRASE = "hey there"

#: The phrase split into the words that must appear, adjacent and in order.
WAKE_WORDS = tuple(WAKE_PHRASE.split())

#: Alternatives per position — what whisper actually reaches for.
#:
#: Only homophones, and only ones observed or obvious: `their`/`they're` for
#: `there`, `hay` for `hey`. Not a fuzzy net, because unlike "kavach" these
#: words have real English neighbours and every one added is false-wake
#: surface.
WORD_ALTERNATIVES = {
    "hey": ("hey", "hay", "he"),
    "there": ("there", "their", "they're", "theres", "there's"),
}

#: Whole-phrase mishearings, matched as **exact adjacent pairs**.
#:
#: `"Hey there."` spoken **alone** transcribes as `"Heed Elm."` — measured
#: on three of four voices, including an American one, so this is not an
#: accent effect. A ~0.6s two-word burst gives whisper nothing to work with
#: and it guesses; with a command attached it is right every time.
#:
#: A pair rather than two entries in `WORD_ALTERNATIVES`: adding `heed` to
#: the "hey" list and `elm` to the "there" list would also match *"heed
#: their advice"*, which people say. `("heed", "elm")` matches nothing else
#: in English.
PHRASE_ALTERNATIVES = (
    ("heed", "elm"),
)

#: How close a word must be to count, per position.
#:
#: **Higher than the 0.70 used for the old single rare word.** "kavach" had
#: only nonsense neighbours, so a loose threshold was safe. "hey" sits next
#: to "they" (0.86) and "there" next to "where" (0.80), so the same
#: looseness here would wake on ordinary speech. Adjacency does most of the
#: work; this stops the rest.
MATCH_RATIO = 0.85

_WORD_RE = re.compile(r"[a-z']+")


def matches_wake(text: str | None) -> bool:
    """Whether a transcript contains the wake phrase.

    **Adjacent and in order.** "hey" and "there" are both common, and either
    alone — or both scattered through a sentence — must not be enough. That
    adjacency requirement is the entire false-wake defence, so it is checked
    on consecutive word pairs rather than by searching the whole string.

    The command may follow in the same breath: "hey there what time is it"
    wakes on the first two words and the rest is ignored here, because the
    turn that follows re-records it.
    """
    if not text:
        return False

    words = _WORD_RE.findall(text.lower())
    span = len(WAKE_WORDS)
    for start in range(len(words) - span + 1):
        window = words[start:start + span]
        if all(_word_matches(window[offset], WAKE_WORDS[offset])
               for offset in range(span)):
            return True
        # Observed whole-phrase mishearings, matched exactly. Adjacency and
        # order still required, so these cannot spread the way a fuzzy
        # per-word entry would.
        if tuple(window) in PHRASE_ALTERNATIVES:
            return True
    return False


def _word_matches(spoken: str, expected: str) -> bool:
    """One position of the phrase, against what was heard."""
    for candidate in WORD_ALTERNATIVES.get(expected, (expected,)):
        if spoken == candidate:
            return True
        if SequenceMatcher(None, spoken, candidate).ratio() >= MATCH_RATIO:
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

    #: Floor under the adaptive threshold. A fixed level cannot work: this
    #: room measured p20 0.005 but p80 0.025, so a 0.015 cut-off read EVERY
    #: block as speech. The segmenter then never closed on silence and simply
    #: cut at MAX_UTTERANCE_S forever — the live daemon transcribed ambient
    #: noise every 3 seconds, continuously, and never heard the user at all.
    SPEECH_RMS = 0.015
    #: Speech must also stand this far above the room's own running level.
    #: Relative, like `wakerecord.speech_bounds`, and for the same reason: the
    #: same numbers have to work in a quiet room and a noisy one.
    OVER_FLOOR = 2.5
    #: How fast the noise estimate follows the room. Slow, so a long sentence
    #: cannot drag the floor up to meet itself.
    FLOOR_ALPHA = 0.02
    #: A burst has to hold this much *speech* to be worth transcribing.
    #:
    #: **Measured against `_speech` — accumulated loud time — not against the
    #: buffered duration.** At 0.35 a quickly-spoken "Kavach" (~0.3s of loud
    #: audio) was dropped here, before the transcriber, silently. That is why
    #: the user's `kavach-wakecheck` runs showed only the command bursts
    #: ('What time is it?', 0.9–1.0s) and no line at all for the wake word:
    #: it never reached whisper to be printed.
    #:
    #: 0.2 lets a fast single word through. The cost is that a cough or a
    #: chair now sometimes reaches whisper — one transcription, ~270ms, no
    #: wake unless the text matches. Losing the wake word is the worse trade.
    MIN_UTTERANCE_S = 0.2
    #: Longer than this and it is cut and scored anyway, so someone talking on
    #: a call cannot accumulate audio in memory waiting for a pause.
    MAX_UTTERANCE_S = 3.0
    #: Silence this long ends a burst.
    #:
    #: **0.35 → 0.7 → 0.35 → 0.7.** The value has moved twice in each
    #: direction and every move was measured. What changed is the wake
    #: phrase, and the right hang is a property of the phrase:
    #:
    #: * **"Kavach"** — a rare word — is *dropped* when a sentence follows
    #:   it, because whisper has better candidates for that audio. It
    #:   needed **isolation**, so 0.35.
    #: * **"hey there"** — two common words — is *mangled* when it stands
    #:   alone, because 0.6s of speech carries no context. It needs the
    #:   **sentence**, so 0.7.
    #:
    #: Measured, the same phrase with and without a command after it::
    #:
    #:     voice     "Hey there."        "Hey there, open Notes."
    #:     Rishi     'Heed Elm.'    x    'Hey there, open notes.'  ok
    #:     Veena     'Heed Elm.'    x    'Hey there, open notes.'  ok
    #:     Alex      'Heed Elm.'    x    'Hey there, open notes.'  ok
    #:     Daniel    'Hey there.'   ok   'Hey there, open notes.'  ok
    #:
    #: Three of four, including an American voice, so it is not accent.
    #:
    #: `PHRASE_ALTERNATIVES` covers the isolated case as well, so saying the
    #: phrase on its own still works — this makes the common case reliable
    #: rather than making the other one impossible.
    HANG_S = 0.7
    #: Blocks of audio kept from before a burst opens, and prepended to it.
    #:
    #: `push` only retained a block once it was **loud**, so every quiet
    #: block before the first loud one was discarded — and a word starting
    #: with a soft consonant begins below the floor. The burst then started
    #: partway into "kav-ACH", a fragment whisper drops or renders as
    #: 'coverage'.
    #:
    #: `MicStream.preroll(500)` exists and the main turn path already uses it
    #: for exactly this. The wake segmenter had none.
    PREROLL_BLOCKS = 3

    def __init__(self) -> None:
        #: Running estimate of the room, seeded pessimistically and pulled
        #: down by the first quiet blocks.
        self._floor = 0.01
        self._parts: list[np.ndarray] = []
        #: Recent quiet blocks, so a soft word onset is not lost. Bounded, and
        #: cleared by `reset()` — audio not acted on must not linger (§7).
        from collections import deque
        self._preroll: deque = deque(maxlen=self.PREROLL_BLOCKS)
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
        # §7: the pre-roll is microphone audio too, and audio that was not
        # acted on must not linger past the turn that dropped it.
        self._preroll.clear()
        self._silence = 0.0
        self._speech = 0.0
        self._speaking = False

    def push(self, block: np.ndarray) -> np.ndarray | None:
        """Feed one block. Returns a complete utterance, or None."""
        if block is None or len(block) == 0:
            return None

        seconds = len(block) / SAMPLE_RATE
        level = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
        threshold = max(self.SPEECH_RMS, self._floor * self.OVER_FLOOR)
        loud = level >= threshold
        if not loud:
            # Only quiet blocks update the floor, or a long utterance teaches
            # the gate that speech is the new silence.
            self._floor += self.FLOOR_ALPHA * (level - self._floor)
            if not self._speaking:
                self._preroll.append(block.astype(np.float32))

        if loud:
            if not self._speaking:
                # Opening a burst: carry the blocks just before it, which
                # hold the quiet start of the word.
                self._parts.extend(self._preroll)
                self._preroll.clear()
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

    #: A registry name, or anything pywhispercpp accepts.
    #:
    #: **This was `swift`, and `swift` cannot hear the word.** It is a
    #: Hinglish fine-tune of whisper-*base* — 72.6M parameters — and the
    #: reasoning for it (tuned for Sanskrit phonetics, small enough to run
    #: per burst) was sound and wrong. Measured on a clean file, with no
    #: microphone involved::
    #:
    #:     swift           "Kavach, what time is it?" → "Have a nice day.…"
    #:     base.en                                    → "What time is it?"
    #:     small.en                                   → "Kavac, what time…"  ✓
    #:     small                                      → "Kavach, what time…" ✓
    #:     large-v3-turbo                             → "Kavach, what time…" ✓
    #:
    #: A base-sized model has no representation for a rare proper noun, so
    #: the matcher downstream never had anything to match. Across four wake
    #: phrases and four ordinary sentences::
    #:
    #:     small.en          recall 3/4   false 0/4   median  256ms
    #:     small             recall 4/4   false 0/4   median  272ms
    #:     large-v3-turbo    recall 4/4   false 0/4   median 1188ms
    #:
    #: `small` multilingual is large-v3-turbo's accuracy at 4.4x the speed.
    #: `small.en` heard a bare "Kavach." as **"Cabbage."** — English-only is
    #: the wrong choice for this word and for this user's Hinglish.
    #:
    #: The project had already ruled large-v3-turbo out as too slow, which
    #: was right, and went from base straight to large. Nothing in between
    #: was ever tried.
    DEFAULT_MODEL = "small"
    #: Used when the chosen model is not installed, fetched by pywhispercpp
    #: on demand: a missing file must not stop KAVACH listening, the same
    #: rule `stt_models.resolve()` follows.
    #:
    #: **Not `base.en`.** Falling back to a model measured unable to hear the
    #: word is falling back to silence, and silence here is indistinguishable
    #: from a broken microphone.
    FALLBACK_MODEL = "small.en"

    #: Bursts waiting to be scored. Bounded, and dropped rather than queued
    #: when full: falling behind must cost a missed wake word, never a growing
    #: backlog of stale audio.
    QUEUE_DEPTH = 2

    def __init__(self, stt=None, model: str | None = None) -> None:
        #: Injected for the tests, and so the loop can share a model rather
        #: than loading a second copy.
        self.stt = stt
        self.model = model or self.DEFAULT_MODEL
        self._segmenter = Segmenter()
        #: Scoring happens on a worker, never on the caller's thread. Measured
        #: live: inference took 0.7-1.9s per burst, and doing that inside
        #: push() blocked the microphone loop for a third of all wall-clock —
        #: so the words after the wake word were the ones being dropped.
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=self.QUEUE_DEPTH)
        self._heard: "queue.Queue[WakeHeard]" = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

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
        for held in (self._queue, self._heard):
            while True:
                try:
                    held.get_nowait()
                except queue.Empty:
                    break

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

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run_worker, daemon=True,
                                        name="kavach-wake-whisper")
        self._worker.start()

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                utterance = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            heard = self._score(utterance)
            if heard is not None:
                self._heard.put(heard)

    def push(self, block: np.ndarray) -> WakeHeard | None:
        """Feed one block of microphone audio. Returns a wake, or None.

        Returns immediately — the transcription happens on a worker, and a
        wake is reported on whichever later call picks it up. The delay is a
        few hundred ms; blocking the microphone for it cost whole words.
        """
        try:
            utterance = self._segmenter.push(block)
        except Exception:
            log.debug("segmenter failed", exc_info=True)
            self._segmenter.reset()
            utterance = None

        if utterance is not None:
            self._ensure_worker()
            try:
                self._queue.put_nowait(utterance)
            except queue.Full:
                # Behind on scoring. Dropping the burst is right: a backlog
                # would wake KAVACH on something said several seconds ago.
                log.debug("wake scorer is behind — burst dropped")

        try:
            return self._heard.get_nowait()
        except queue.Empty:
            return None

    def _score(self, utterance: np.ndarray) -> WakeHeard | None:
        seconds = len(utterance) / SAMPLE_RATE
        try:
            if self.stt is None:
                self.load()
            # English, not auto. The multilingual model writes this user's
            # wake word in Devanagari and Cyrillic when left to choose, and
            # `matches_wake` reads Latin only — so a correct transcription
            # was being discarded. Measured: 12/42 on auto, 17/42 on en.
            result = self.stt.transcribe(utterance, language="en")
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
