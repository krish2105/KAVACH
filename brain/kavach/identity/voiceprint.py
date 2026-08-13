"""Speaker verification (spec §7 extension).

The hole this closes: the confirmation gate trusts *whoever is in the room*.
Anyone within earshot of the microphone can answer "yes" to a delete prompt.
Binding confirmation to a voiceprint means the answer has to come from you.

Resemblyzer produces a 256-d L2-normalised embedding from a few seconds of
speech, entirely on-device. Enrolment stores the mean embedding of several
clips; verification embeds the answer and compares by cosine similarity.

**Everything denies on failure** — not enrolled, low similarity, too little
audio, an exception in the encoder. Same asymmetry as the spoken
confirmation it guards: consent must be given, never merely not-withheld.

The stored profile is biometric data. It lives outside the repo in
``~/.kavach/`` and is gitignored; it is a derived embedding rather than
recoverable audio, but it still identifies you.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("kavach.identity.voiceprint")

DEFAULT_PATH = Path.home() / ".kavach" / "voiceprint.npz"

#: Below this, an embedding is too noisy to mean anything.
MIN_ENROLMENT_SECONDS = 6.0
MIN_VERIFY_SECONDS = 0.8

#: Used only when calibration cannot run (a single enrolment clip). Resemblyzer
#: embeddings for the same speaker typically sit well above this.
FALLBACK_THRESHOLD = 0.75

#: Calibration floor and ceiling. A threshold outside this range means
#: something went wrong with enrolment, and clamping is safer than trusting it.
MIN_THRESHOLD = 0.55
MAX_THRESHOLD = 0.92


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity, safe on zero vectors.

    Resemblyzer already L2-normalises, so this is usually a dot product — but
    a zero vector (silence that survived the VAD) would otherwise divide by
    zero and produce a NaN that compares false against every threshold in a
    way that only *looks* like a denial.
    """
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.clip(np.dot(a, b) / denom, -1.0, 1.0))


@dataclass
class VerificationResult:
    accepted: bool
    similarity: float
    threshold: float
    reason: str

    def as_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "similarity": round(self.similarity, 4),
            "threshold": round(self.threshold, 4),
            "reason": self.reason,
        }


class Voiceprint:
    def __init__(self, path: Path | str = DEFAULT_PATH, device: str = "cpu"):
        self.path = Path(path)
        self.device = device
        self._encoder = None
        self._mean: np.ndarray | None = None
        self.threshold: float = FALLBACK_THRESHOLD
        self.enrolled_seconds: float = 0.0
        self.calibrated: bool = False
        self._load()

    # ——— persistence ———

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = np.load(self.path)
            self._mean = data["mean"]
            self.threshold = float(data["threshold"])
            self.enrolled_seconds = float(data.get("seconds", 0.0))
            self.calibrated = bool(data.get("calibrated", False))
            log.info("voiceprint loaded (threshold %.3f)", self.threshold)
        except Exception:
            # A corrupt profile must not be treated as a valid one.
            log.exception("could not read voiceprint at %s; treating as not enrolled",
                          self.path)
            self._mean = None

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            self.path,
            mean=self._mean,
            threshold=self.threshold,
            seconds=self.enrolled_seconds,
            calibrated=self.calibrated,
        )
        self.path.chmod(0o600)  # biometric data

    @property
    def is_enrolled(self) -> bool:
        return self._mean is not None

    def forget(self) -> None:
        """Delete the profile. Biometric data should be easy to revoke."""
        self._mean = None
        self.calibrated = False
        self.path.unlink(missing_ok=True)

    # ——— embedding ———

    def _load_encoder(self):
        if self._encoder is None:
            from resemblyzer import VoiceEncoder

            self._encoder = VoiceEncoder(self.device)
        return self._encoder

    def _embed(self, wav: np.ndarray, sample_rate: int) -> np.ndarray:
        from resemblyzer import preprocess_wav

        encoder = self._load_encoder()
        processed = preprocess_wav(wav.astype(np.float32), source_sr=sample_rate)
        return encoder.embed_utterance(processed)

    # ——— enrolment ———

    def enrol(self, clips: list[np.ndarray], sample_rate: int) -> None:
        """Build a profile from several clips of one speaker.

        Several clips rather than one long take: the spread *between* them is
        what calibrates the threshold, and it also captures a bit of natural
        variation in how the same person sounds.
        """
        total = sum(len(c) for c in clips) / sample_rate
        if total < MIN_ENROLMENT_SECONDS:
            raise ValueError(
                f"need at least {MIN_ENROLMENT_SECONDS:.0f}s of speech to enrol, "
                f"got {total:.1f}s. A profile from less is not worth trusting."
            )

        embeddings = [self._embed(c, sample_rate) for c in clips]
        stacked = np.vstack(embeddings)
        mean = stacked.mean(axis=0)
        mean = mean / (np.linalg.norm(mean) or 1.0)

        self._mean = mean
        self.enrolled_seconds = total
        self.threshold = self._calibrate(stacked, mean)
        self._save()
        log.info(
            "enrolled from %.1fs across %d clips; threshold %.3f (%s)",
            total, len(clips), self.threshold,
            "calibrated" if self.calibrated else "fallback",
        )

    def _calibrate(self, embeddings: np.ndarray, mean: np.ndarray) -> float:
        """Derive a threshold from how tightly the enrolment clips cluster.

        Same discipline as the wake word's 0.18: measured, not guessed. Sit a
        few standard deviations below the speaker's own self-similarity, then
        clamp — a threshold outside the sane range means enrolment went wrong,
        and clamping fails safer than trusting it.
        """
        if len(embeddings) < 2:
            self.calibrated = False
            return FALLBACK_THRESHOLD

        sims = np.array([cosine_similarity(e, mean) for e in embeddings])
        threshold = float(sims.mean() - 3.0 * sims.std() - 0.05)
        self.calibrated = True
        return float(np.clip(threshold, MIN_THRESHOLD, MAX_THRESHOLD))

    # ——— verification ———

    def verify(self, wav: np.ndarray, sample_rate: int) -> VerificationResult:
        """Is this the enrolled speaker? Any doubt is a no."""
        if not self.is_enrolled:
            return VerificationResult(
                False, 0.0, self.threshold,
                "not enrolled — run `kavach enrol` to bind confirmations to "
                "your voice",
            )

        seconds = len(wav) / sample_rate
        if seconds < MIN_VERIFY_SECONDS:
            return VerificationResult(
                False, 0.0, self.threshold,
                f"clip too short to verify ({seconds:.2f}s)",
            )

        # Silence must never reach the encoder. Measured here: three seconds
        # of zeros comes back as a valid-looking embedding that scores 1.00
        # against an enrolled profile — i.e. an empty room would authorise a
        # delete. Resemblyzer normalises by RMS, so silence collapses to a
        # degenerate vector that matches everything.
        #
        # Same defence as the STT path uses against Whisper's confabulations:
        # gate on energy first, and never infer identity from nothing.
        from ..voice.stt import is_probably_silence

        if is_probably_silence(wav):
            return VerificationResult(
                False, 0.0, self.threshold,
                "no speech energy in the clip — cannot verify a speaker from "
                "silence",
            )

        try:
            embedding = self._embed(wav, sample_rate)
        except Exception as exc:
            # Never let this raise into a confirmation prompt, and never let a
            # failure read as approval.
            log.exception("voice verification failed")
            return VerificationResult(
                False, 0.0, self.threshold, f"verification error: {exc}"
            )

        assert self._mean is not None
        similarity = cosine_similarity(embedding, self._mean)
        accepted = similarity >= self.threshold
        return VerificationResult(
            accepted,
            similarity,
            self.threshold,
            "voice matches" if accepted else "voice does not match the enrolled speaker",
        )
