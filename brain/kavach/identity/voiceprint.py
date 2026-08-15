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
#: something went wrong with the measurement, and clamping is safer than
#: trusting it.
#:
#: **The floor was 0.55 and it locked the user out.** Their real speech was
#: measured at 0.361–0.781 across a normal evening, so the floor alone would
#: have rejected several genuine turns even if calibration had been perfect.
#: A floor is meant to catch a broken measurement, not to overrule a correct
#: one — 0.30 is below anything this microphone has produced for the enrolled
#: speaker and still far above what a stranger scores.
MIN_THRESHOLD = 0.30
MAX_THRESHOLD = 0.92

#: How many samples make a distribution. Two is the minimum that can show any
#: spread at all; the original bug was drawing conclusions from clips that had
#: none because they were recorded back to back.
MIN_SAMPLES = 2

#: **Below this, speaker verification is not possible — not merely unreliable.**
#:
#: Measured 2026-08-15. The same audio from the same speaker, scored at
#: increasing durations::
#:
#:     0.8s → 0.423     7.2s → 0.774    27.5s → 0.807
#:     2.7s → 0.579    13.8s → 0.816
#:
#: Resemblyzer cannot embed a one-second clip stably. The control that makes
#: this decisive is scoring 400 clips of *other* speakers the same way: they
#: plateau at ~0.53 while the enrolled speaker climbs::
#:
#:     duration    you     strangers (max)   margin
#:         1s     0.581        0.543         +0.038   ← noise
#:         3s     0.698        0.561         +0.138
#:         7s     0.774        0.540         +0.234
#:        14s     0.811        0.552         +0.258
#:
#: So the voiceprint is fine and the threshold was never the real fault. A
#: voice command — "open Notes" — is about a second, where the enrolled user
#: and a total stranger are 0.038 apart. **No threshold separates those.** It
#: is not a tuning problem; the embedding has nothing to work with.
#:
#: 3.0s is where a usable margin first appears. Below it the honest answer is
#: "I cannot tell", and `tests/test_voiceprint_duration.py` exists so that
#: answer cannot quietly become "yes".
MIN_VERIFY_SECONDS = 3.0


def is_long_enough_to_verify(wav, sample_rate: int) -> bool:
    """Whether there is enough audio for the answer to mean anything."""
    if wav is None or sample_rate <= 0:
        return False
    return (len(wav) / float(sample_rate)) >= MIN_VERIFY_SECONDS


def choose_threshold(
    genuine: "list[float]", others: "list[float]",
) -> "tuple[float | None, str]":
    """A threshold from the measured gap, or `(None, why)` if there isn't one.

    `genuine` are similarity scores for the enrolled speaker — ideally from a
    *different* session than enrolment. `others` are scores for anyone else.

    **Why this replaced the old calibration.** `_calibrate` used
    ``sims.mean() - 3*sims.std() - 0.05`` over the enrolment clips, which are
    recorded back to back in one sitting. They cluster tightly, `std` is tiny,
    and the threshold lands just under the mean — 0.803, against real speech
    of 0.361–0.781. It measured self-similarity *within one session* and used
    it as a proxy for self-similarity *across sessions*, and the first tells
    you almost nothing about the second.

    **Refusing to save is a result, not a failure.** `waketune` arrived at the
    same rule after the wake word wrote a threshold that could not work: a
    number that does not separate is worse than no number, because it fails
    silently and hours later. The reason names the side that failed, because
    being told the wrong half is broken sends you re-recording the wrong
    thing.
    """
    genuine = sorted(float(s) for s in (genuine or []))
    others = sorted(float(s) for s in (others or []))

    if len(genuine) < MIN_SAMPLES:
        return (None, (
            f"only {len(genuine)} sample(s) of your voice — need at least "
            f"{MIN_SAMPLES}, and more is better. One clip cannot show spread, "
            f"which is the mistake that produced 0.803."
        ))
    if not others:
        return (None, (
            "no negative samples, so there is nothing to separate from. "
            "A threshold that only ever saw one speaker accepts everyone "
            "below it and rejects everyone above, for no measured reason."
        ))

    worst_genuine, best_other = genuine[0], others[-1]
    if worst_genuine <= best_other:
        return (None, (
            f"overlap: your quietest take scored {worst_genuine:.3f} and "
            f"another voice reached {best_other:.3f}. No threshold can accept "
            f"you and refuse them, so none was saved."
        ))

    # Halfway through the gap: the most forgiving place it can sit without
    # accepting the nearest impostor. Hugging the genuine edge fails the first
    # time the user has a cold or sits further back.
    threshold = (worst_genuine + best_other) / 2.0
    clamped = float(min(max(threshold, MIN_THRESHOLD), MAX_THRESHOLD))
    return (clamped, (
        f"separated by {worst_genuine - best_other:.3f} — your worst take "
        f"{worst_genuine:.3f}, best other {best_other:.3f}, threshold "
        f"{clamped:.3f} from {len(genuine)} genuine and {len(others)} other "
        f"samples."
    ))


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
        #: Enrolled means gated. See `gating`.
        self.enabled = True
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
            # Enrolled means on. A profile written before this existed has no
            # `enabled` key, and defaulting it to False would silently drop
            # the §7 speaker gate for anyone who upgrades.
            self.enabled = bool(data.get("enabled", True))
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
            enabled=self.enabled,
        )
        self.path.chmod(0o600)  # biometric data

    @property
    def is_enrolled(self) -> bool:
        return self._mean is not None

    @property
    def gating(self) -> bool:
        """Whether a turn will actually be checked against this voice.

        The one question the voice loop should ask. It used to ask
        `is_enrolled`, which conflated "we know your voice" with "we are
        checking it" — and left `forget()` as the only way to stop checking,
        so turning the gate off for five minutes meant re-recording your
        voiceprint afterwards.
        """
        return self.is_enrolled and self.enabled

    def enable(self, log=None) -> None:
        """Resume gating on the enrolled voice."""
        self._set_enabled(True, log)

    def disable(self, log=None) -> None:
        """Stop gating, keeping the enrolment.

        Logged, because this is the moment KAVACH stops caring who is
        speaking — a security state change, not a preference.
        """
        self._set_enabled(False, log)

    def _set_enabled(self, enabled: bool, action_log=None) -> None:
        self.enabled = enabled
        if self.is_enrolled:
            self._save()
        log_msg = "voiceprint.enabled" if enabled else "voiceprint.disabled"
        logger_line = ("speaker verification ON" if enabled
                       else "speaker verification OFF — any voice will be acted on")
        log.warning(logger_line)
        if action_log is not None:
            action_log.append(log_msg, enabled=enabled,
                              enrolled=self.is_enrolled)

    def forget(self) -> None:
        """Delete the profile. Biometric data should be easy to revoke."""
        self._mean = None
        self.calibrated = False
        self.enabled = True     # so a fresh enrolment starts gated
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

        # **This is the number that locked the user out, and the reason is
        # the sampling, not the arithmetic.**
        #
        # Enrolment clips are recorded back to back — one seat, one distance,
        # one minute — so they cluster tightly and `std` is tiny. Sitting
        # three of those standard deviations below the mean therefore lands
        # just under the mean itself: 0.803, measured 2026-08-14.
        #
        # Scored against 42 real recordings of the same person from a
        # different session, that threshold rejected **42 of 42** — median
        # 0.498, best take 0.803. Not "too tight": non-functional.
        #
        # Within-session spread is not evidence about across-session spread.
        # So this is now a *provisional* number, and it is deliberately
        # generous: the margin is widened to reflect that the clips it was
        # measured from cannot show the variation that matters.
        # `choose_threshold()` is the real answer, and it needs samples from
        # a second session plus at least one other voice.
        spread = max(float(sims.std()), 0.08)
        threshold = float(sims.mean() - 3.0 * spread - 0.05)
        self.calibrated = False        # provisional until choose_threshold runs
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
