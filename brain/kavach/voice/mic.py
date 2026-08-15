"""Microphone capture with silence-based endpointing.

Two constraints shape this module.

**Spec §7:** *never log or transmit wake-word audio that wasn't acted on.*
Audio lives in a bounded in-memory ring buffer and is overwritten as it ages.
Nothing here writes audio to disk, and `Recorder` deliberately exposes no
"save" path — adding one should be a conscious decision, not a convenience.

**Latency:** Whisper wants 16 kHz mono float32, and the built-in mic runs at
48 kHz, so every block is resampled on the way in.

That step used to be a slice — `block[::3]` — justified in a comment as
"speech energy above 8 kHz is negligible". Aliased energy does not stay where
it was: content above 8 kHz folds back into the speech band and lands on top of
the formants, and a real room has plenty of it (sibilance, keyboards, fans).

**This was investigated as the cause of the wake word failing and ruled out.**
Measured on the same utterance, the old decimator and this resampler are
indistinguishable:

    reference 16 kHz (polyphase)     0.858
    OLD naive decimation block[::3]  0.857
    NEW Resampler, 32 ms blocks      0.857

So the slice was not costing anything measurable on that signal, and replacing
it fixed nothing that was reported. It is kept as a correctness fix — the
premise it rested on is wrong even where the consequence happened to be small —
and not because it repaired the wake word. The real cause is recorded in
CLAUDE.md: the model does not survive a real microphone at all.

`Resampler` low-passes before decimating and **keeps its filter state across
blocks**, because the mic arrives in 32 ms pieces and a filter restarted per
block would ring 31 times a second.
"""

from __future__ import annotations

import collections
import logging
import queue
import time
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

log = logging.getLogger("kavach.voice.mic")

TARGET_RATE = 16_000  # what both Whisper and the wake-word model expect
BLOCK_MS = 32
CHANNELS = 1


@dataclass
class EndpointConfig:
    """When to decide the speaker has finished."""

    #: Quiet this long **after you have spoken** ends the turn. Prompt on
    #: purpose: waiting longer makes every reply feel slow.
    silence_ms: int = 700
    #: Quiet this long **before you have spoken** ends it instead.
    #:
    #: These are separate numbers because they answer opposite questions, and
    #: collapsing them into one is what broke push-to-talk. Measured live
    #: 2026-08-15, twice: the hotkey fired, the mic opened, and the turn closed
    #: after ~1.05s — `silence_ms` plus `min_utterance_ms` — while the user was
    #: still drawing breath. Nothing reached the router, the action log stayed
    #: empty, and it looked exactly like the speaker gate had failed.
    #:
    #: CLAUDE.md had recorded the symptom in August as advice about how to use
    #: it ("clicking TALK and *then* gathering your thoughts closes it before
    #: you speak"). It was a fact about this constant.
    lead_in_ms: int = 6_000
    max_utterance_ms: int = 15_000
    min_utterance_ms: int = 350
    # RMS below this counts as silence. Tuned by ear on the built-in mic; the
    # calibrate() helper measures a real room rather than trusting it.
    silence_rms: float = 0.012


def list_input_devices() -> list[dict]:
    return [d for d in sd.query_devices() if d["max_input_channels"] > 0]


class Resampler:
    """Native rate → 16 kHz, anti-aliased, and continuous across blocks.

    Two things the slice it replaced did not do:

    * **Filter first.** Content above the new Nyquist folds back into the
      speech band rather than disappearing. A 12 kHz component lands at 4 kHz,
      squarely among the formants.
    * **Remember.** `lfilter` restarted on each 32 ms block produces an edge
      artifact at every boundary. The filter state is carried instead, so a
      stream of blocks gives the same result as filtering the whole signal.

    Falls back to `resample_poly` for rates that are not integer multiples —
    44.1 kHz is a real device rate and the old decimator raised on it.
    """

    def __init__(self, native_rate: int, target_rate: int = TARGET_RATE):
        from scipy.signal import butter, lfilter_zi

        self.native_rate = int(native_rate)
        self.target_rate = int(target_rate)
        self.factor = self.native_rate // self.target_rate \
            if self.native_rate % self.target_rate == 0 else 0

        # 7.6 kHz, just under the 8 kHz Nyquist: high enough to leave sibilance
        # alone, low enough that the stopband is doing real work by 8 kHz.
        self._b, self._a = butter(8, 7600, fs=self.native_rate, btype="low")
        self._zi = lfilter_zi(self._b, self._a) * 0.0
        #: Index of the next sample to keep, counted from the start of the next
        #: block. Carrying leftover *samples* instead drifts the phase whenever
        #: a block length is not a multiple of the factor — the tests caught it
        #: as a tail that diverged from filtering the signal in one piece.
        self._offset = 0

    def process(self, block: np.ndarray) -> np.ndarray:
        from scipy.signal import lfilter, resample_poly

        block = np.asarray(block, dtype=np.float32)
        if self.native_rate == self.target_rate:
            return block
        if len(block) == 0:
            return block

        if self.factor == 0:
            # Non-integer ratio: polyphase handles the filtering itself.
            return resample_poly(block, self.target_rate,
                                 self.native_rate).astype(np.float32)

        filtered, self._zi = lfilter(self._b, self._a, block, zi=self._zi)
        filtered = filtered.astype(np.float32)

        out = filtered[self._offset::self.factor]
        if len(out):
            last = self._offset + (len(out) - 1) * self.factor
            self._offset = last + self.factor - len(filtered)
        else:
            self._offset -= len(filtered)
        return out


class MicStream:
    """Continuous 16 kHz mono float32 capture.

    Blocks arrive on a queue rather than in a callback, so nothing expensive
    ever runs on PortAudio's realtime thread — doing so causes dropouts.
    """

    def __init__(self, device: int | None = None, ring_seconds: float = 8.0):
        self.device = device
        info = sd.query_devices(device if device is not None else sd.default.device[0])
        self.native_rate = int(info["default_samplerate"])

        # No longer a hard requirement: Resampler falls back to polyphase for
        # rates like 44.1 kHz, which the old slice-based decimator could not do
        # and refused to start on.
        self.decimation = (self.native_rate // TARGET_RATE
                           if self.native_rate % TARGET_RATE == 0 else 0)
        self._resampler = Resampler(self.native_rate, TARGET_RATE)
        self.blocksize = int(self.native_rate * BLOCK_MS / 1000)

        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None
        # Bounded: old audio falls off the end and is unrecoverable (§7).
        self._ring: collections.deque[np.ndarray] = collections.deque(
            maxlen=int(ring_seconds * 1000 / BLOCK_MS)
        )

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("mic status: %s", status)
        block = self._resampler.process(indata[:, 0].copy())
        self._ring.append(block)
        self._queue.put(block)

    def start(self) -> "MicStream":
        self._stream = sd.InputStream(
            device=self.device,
            channels=CHANNELS,
            samplerate=self.native_rate,
            blocksize=self.blocksize,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()
        log.info(
            "mic open: %d Hz → %d Hz (÷%d), %d ms blocks",
            self.native_rate, TARGET_RATE, self.decimation, BLOCK_MS,
        )
        return self

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self.forget()

    def forget(self) -> None:
        """Drop all buffered audio. Called after every turn — audio that was
        not acted on must not linger in memory either."""
        self._ring.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def preroll(self, ms: int = 500) -> np.ndarray:
        """The audio just before now.

        The wake word is only recognised *after* it has been spoken, and people
        run straight on into the command. Without preroll the first syllable is
        already gone by the time recording starts.
        """
        want = int(TARGET_RATE * ms / 1000)
        if not self._ring:
            return np.zeros(0, dtype=np.float32)
        joined = np.concatenate(list(self._ring))
        return joined[-want:] if len(joined) > want else joined

    def calibrate(self, seconds: float = 1.0) -> float:
        """Measure the room's noise floor and suggest a silence threshold."""
        deadline = time.monotonic() + seconds
        levels: list[float] = []
        while time.monotonic() < deadline:
            block = self.read(timeout=0.5)
            if block is not None and len(block):
                levels.append(float(np.sqrt(np.mean(block**2))))
        if not levels:
            return EndpointConfig.silence_rms
        floor = float(np.median(levels))
        # Sit clearly above the floor but below speech, with a sane minimum for
        # a very quiet room.
        return max(0.006, floor * 3.0)


class Recorder:
    """Records one utterance, stopping when the speaker does."""

    def __init__(self, mic: MicStream, config: EndpointConfig | None = None):
        self.mic = mic
        self.config = config or EndpointConfig()

    def record_utterance(self, preroll_ms: int = 500) -> np.ndarray:
        cfg = self.config
        chunks: list[np.ndarray] = []

        pre = self.mic.preroll(preroll_ms)
        if len(pre):
            chunks.append(pre)

        started = time.monotonic()
        #: Measured from the audio itself, not the clock.
        #:
        #: Wall-clock timing asks "how long have I been waiting", which
        #: depends on how fast PortAudio delivers blocks. Sample counting asks
        #: "how much audio is there", which is the actual question and is the
        #: same answer whether blocks arrive smoothly or in a burst after a
        #: scheduling hiccup.
        elapsed_ms = 0.0
        silent_ms = 0.0
        heard_speech = False

        while True:
            block = self.mic.read(timeout=1.0)
            if block is None:
                # No audio at all: fall back to the clock, because a stalled
                # stream produces no samples to count and must still time out.
                if (time.monotonic() - started) * 1000 > cfg.max_utterance_ms:
                    break
                continue

            chunks.append(block)
            block_ms = len(block) / TARGET_RATE * 1000
            elapsed_ms += block_ms
            rms = float(np.sqrt(np.mean(block**2)))

            if rms < cfg.silence_rms:
                silent_ms += block_ms
                if heard_speech:
                    # You have spoken and stopped. End promptly.
                    if (silent_ms >= cfg.silence_ms
                            and elapsed_ms >= cfg.min_utterance_ms):
                        break
                elif silent_ms >= cfg.lead_in_ms:
                    # You never started. Give up — patience is not forever,
                    # and a key pressed by accident must not hold the
                    # microphone open indefinitely.
                    log.info("nothing said within %d ms — closing the turn",
                             cfg.lead_in_ms)
                    break
            else:
                heard_speech = True
                silent_ms = 0.0

            if elapsed_ms >= cfg.max_utterance_ms:
                log.info("hit max utterance length (%d ms)", cfg.max_utterance_ms)
                break

        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def rms_to_amplitude(block: np.ndarray) -> float:
    """Map a block's RMS onto the 0–1 the orb expects.

    Speech RMS lives around 0.02–0.25, so a linear map would leave the orb
    barely moving. sqrt opens up the quiet end where normal speech sits.
    """
    if not len(block):
        return 0.0
    rms = float(np.sqrt(np.mean(block**2)))
    return float(min(1.0, np.sqrt(rms / 0.25)))
