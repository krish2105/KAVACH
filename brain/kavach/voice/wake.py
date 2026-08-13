"""Wake-word detection.

Porcupine was spec §3's choice, but Picovoice discontinued the free tier on
2026-06-30 and disabled existing free AccessKeys, with no non-commercial
replacement planned. So the wake word is trained rather than licensed —
see `wakeword/kavach.yaml`.

The runtime is `livekit.wakeword`, whose exported model is standard ONNX and
backward-compatible with openWakeWord — so if this package stalls (it is 0.2.x
and ships with several undeclared dependencies), the model still runs
elsewhere and only this file changes.

**§7 applies here more than anywhere else in the codebase:** this is the
always-listening layer. It never writes audio anywhere, and a detection that
is not acted on leaves no trace beyond a counter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("kavach.voice.wake")

DEFAULT_THRESHOLD = 0.5
# Frames the model expects, at 16 kHz.
CHUNK_SAMPLES = 1280  # 80 ms


@dataclass
class WakeEvent:
    name: str
    confidence: float


class WakeWordDetector:
    """Streaming wake-word detector.

    Wraps `WakeWordModel`, which is stateless per call — this class owns the
    buffering so callers can push arbitrary block sizes.
    """

    def __init__(
        self,
        model_path: Path | str | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        refractory_frames: int = 12,
    ):
        self.model_path = Path(model_path) if model_path else None
        self.threshold = threshold
        # After firing, ignore detections for a moment — one spoken wake word
        # spans several frames and would otherwise trigger repeatedly.
        self.refractory_frames = refractory_frames
        self._model = None
        self._buffer = np.zeros(0, dtype=np.float32)
        self._cooldown = 0
        self.detections = 0

    @property
    def available(self) -> bool:
        return self.model_path is not None and self.model_path.exists()

    def load(self) -> None:
        if self._model is not None:
            return
        if not self.available:
            raise FileNotFoundError(
                f"no wake-word model at {self.model_path}. Train one with:\n"
                f"  uv run livekit-wakeword run wakeword/kavach.yaml"
            )
        from livekit.wakeword import WakeWordModel

        log.info("loading wake word model %s", self.model_path)
        self._model = WakeWordModel(models=[str(self.model_path)])
        log.info("wake word ready (threshold %.2f)", self.threshold)

    def push(self, block: np.ndarray) -> WakeEvent | None:
        """Feed audio; returns an event on the frame the wake word fires."""
        if self._model is None:
            self.load()
        assert self._model is not None

        self._buffer = np.concatenate([self._buffer, block.astype(np.float32)])

        event: WakeEvent | None = None
        while len(self._buffer) >= CHUNK_SAMPLES:
            frame = self._buffer[:CHUNK_SAMPLES]
            self._buffer = self._buffer[CHUNK_SAMPLES:]

            if self._cooldown > 0:
                self._cooldown -= 1
                continue

            scores = self._model.predict(frame)
            for name, score in (scores or {}).items():
                if score >= self.threshold:
                    self._cooldown = self.refractory_frames
                    self.detections += 1
                    # Confidence only — never the audio (§7).
                    log.info("wake word %r (%.2f)", name, score)
                    event = WakeEvent(name=name, confidence=float(score))
                    break

        return event

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._cooldown = 0
