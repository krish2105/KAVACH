"""Microphone capture with silence-based endpointing.

Two constraints shape this module.

**Spec §7:** *never log or transmit wake-word audio that wasn't acted on.*
Audio lives in a bounded in-memory ring buffer and is overwritten as it ages.
Nothing here writes audio to disk, and `Recorder` deliberately exposes no
"save" path — adding one should be a conscious decision, not a convenience.

**Latency:** Whisper wants 16 kHz mono float32, and the built-in mic runs at
48 kHz. Resampling by integer decimation (48000/16000 = exactly 3) avoids
pulling scipy in for a resample we can do with a slice.
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

    silence_ms: int = 700
    max_utterance_ms: int = 15_000
    min_utterance_ms: int = 350
    # RMS below this counts as silence. Tuned by ear on the built-in mic; the
    # calibrate() helper measures a real room rather than trusting it.
    silence_rms: float = 0.012


def list_input_devices() -> list[dict]:
    return [d for d in sd.query_devices() if d["max_input_channels"] > 0]


def _decimate(block: np.ndarray, factor: int) -> np.ndarray:
    """Cheap integer-factor downsample.

    Naive decimation aliases, but speech energy above 8 kHz is negligible for
    both Whisper and the wake-word features, and a proper polyphase filter
    would mean a scipy dependency for no audible gain here.
    """
    if factor == 1:
        return block
    return block[::factor]


class MicStream:
    """Continuous 16 kHz mono float32 capture.

    Blocks arrive on a queue rather than in a callback, so nothing expensive
    ever runs on PortAudio's realtime thread — doing so causes dropouts.
    """

    def __init__(self, device: int | None = None, ring_seconds: float = 8.0):
        self.device = device
        info = sd.query_devices(device if device is not None else sd.default.device[0])
        self.native_rate = int(info["default_samplerate"])

        if self.native_rate % TARGET_RATE != 0:
            raise RuntimeError(
                f"device runs at {self.native_rate} Hz, which is not an integer "
                f"multiple of {TARGET_RATE} Hz; a resampler would be required"
            )
        self.decimation = self.native_rate // TARGET_RATE
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
        block = _decimate(indata[:, 0].copy(), self.decimation)
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
        silence_started: float | None = None

        while True:
            block = self.mic.read(timeout=1.0)
            if block is None:
                if (time.monotonic() - started) * 1000 > cfg.max_utterance_ms:
                    break
                continue

            chunks.append(block)
            elapsed_ms = (time.monotonic() - started) * 1000
            rms = float(np.sqrt(np.mean(block**2)))

            if rms < cfg.silence_rms:
                if silence_started is None:
                    silence_started = time.monotonic()
                elif (
                    (time.monotonic() - silence_started) * 1000 >= cfg.silence_ms
                    and elapsed_ms >= cfg.min_utterance_ms
                ):
                    break
            else:
                silence_started = None

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
