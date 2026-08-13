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

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("kavach.voice.wake")

#: The threshold training measured (0.18) is calibrated on the training
#: distribution, where negatives score ~0.004. Measured against audio
#: outside that distribution it is far too permissive, so the runtime
#: default is deliberately stricter. Calibrate against your own voice with
#: `kavach-waketune` — a threshold tuned on synthetic speech is a guess.
DEFAULT_THRESHOLD = 0.7

# Frames the model expects, at 16 kHz.
#: The model is stateless and scores a whole window at once. Its docstring:
#: "~2 seconds of 16 kHz audio is recommended (yields exactly 16 embeddings
#: for the classifier). Shorter chunks that lack enough data return zero
#: scores." Feeding it 80 ms frames silently yields 0.0 forever.
WINDOW_SAMPLES = 32000  # 2.0 s — one complete scoring window
HOP_SAMPLES = 1280      # 80 ms between evaluations


def trained_threshold(model_path: Path | str, fallback: float = DEFAULT_THRESHOLD) -> float:
    """Read the optimal threshold the training run measured.

    livekit-wakeword writes `<model_name>_metrics.json` next to the model: a
    list of validation snapshots whose last entry is tagged
    `optimal_threshold`. Using it means the sensitivity in production is the
    one the metrics were reported at.
    """
    metrics = Path(model_path).with_name(f"{Path(model_path).stem}_metrics.json")
    try:
        entries = json.loads(metrics.read_text())
        for entry in reversed(entries if isinstance(entries, list) else [entries]):
            if "threshold" in entry:
                return float(entry["threshold"])
    except Exception:
        log.debug("no trained threshold at %s; using %.2f", metrics, fallback)
    return fallback


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
        threshold: float | None = None,
        refractory_hops: int = 12,
        min_rms: float = 0.008,
    ):
        self.model_path = Path(model_path) if model_path else None
        # None means "use whatever training measured" rather than a guess.
        calibrated = None
        if threshold is None:
            # A threshold measured against this user's actual voice beats both
            # the training optimum and any floor we picked from synthetic audio.
            from .waketune import load_calibration

            calibrated = load_calibration()

        if threshold is not None:
            self.threshold = threshold
        elif calibrated is not None:
            self.threshold = calibrated
        elif self.model_path:
            # Never go below the runtime floor. The measured optimum is real,
            # but it is optimal *against the training negatives*; live audio
            # contains things training never saw.
            self.threshold = max(trained_threshold(self.model_path), DEFAULT_THRESHOLD)
        else:
            self.threshold = DEFAULT_THRESHOLD
        # After firing, ignore detections for a moment — one spoken wake word
        # spans several frames and would otherwise trigger repeatedly.
        self.refractory_hops = refractory_hops
        #: Windows quieter than this are never scored. See push().
        self.min_rms = min_rms
        self._model = None
        self._buffer = np.zeros(0, dtype=np.float32)
        self._cooldown = 0
        self._since_eval = 0
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
        """Feed audio; returns an event on the hop where the wake word fires.

        The model is **stateless**: each call must receive a complete ~2 s
        window, not the next slice of a stream. Feeding it consecutive 80 ms
        frames — the obvious streaming shape, and what this did originally —
        makes it return 0.0 for every frame forever, so the wake word never
        fires and nothing looks broken. So we keep a rolling window and
        re-score the whole of it every hop.
        """
        if self._model is None:
            self.load()
        assert self._model is not None

        self._buffer = np.concatenate([self._buffer, block.astype(np.float32)])
        # Keep only the trailing window; this is a sliding view, not a queue.
        if len(self._buffer) > WINDOW_SAMPLES:
            self._buffer = self._buffer[-WINDOW_SAMPLES:]

        self._since_eval += len(block)
        event: WakeEvent | None = None

        while self._since_eval >= HOP_SAMPLES:
            self._since_eval -= HOP_SAMPLES

            # Until we have a full window there is nothing meaningful to score.
            if len(self._buffer) < WINDOW_SAMPLES:
                continue

            if self._cooldown > 0:
                self._cooldown -= 1
                continue

            # Energy gate. The training negatives were real speech and real
            # backgrounds — the model never saw digital silence, which is
            # therefore out of distribution and scores 0.705 here. Measured,
            # not assumed. Scoring silence is both wrong and wasted work on an
            # always-on listener, so skip quiet windows outright.
            if float(np.sqrt(np.mean(self._buffer**2))) < self.min_rms:
                continue

            scores = self._model.predict(self._buffer)
            for name, score in (scores or {}).items():
                if score >= self.threshold:
                    self._cooldown = self.refractory_hops
                    self.detections += 1
                    # Confidence only — never the audio (§7).
                    log.info("wake word %r (%.2f)", name, score)
                    event = WakeEvent(name=name, confidence=float(score))
                    break
            if event:
                break

        return event

    def score_window(self, audio: np.ndarray) -> float:
        """Best score for one complete window. For tests and tuning."""
        if self._model is None:
            self.load()
        assert self._model is not None
        a = audio.astype(np.float32)
        if len(a) < WINDOW_SAMPLES:
            a = np.concatenate([np.zeros(WINDOW_SAMPLES - len(a), np.float32), a])
        return max((self._model.predict(a[-WINDOW_SAMPLES:]) or {}).values(), default=0.0)

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._cooldown = 0
        self._since_eval = 0
