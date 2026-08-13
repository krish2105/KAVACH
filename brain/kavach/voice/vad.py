"""Did a human actually speak?

An energy threshold answers "was it loud enough", which is a different
question. The room here measures rms 0.0365 against a 0.006 gate, so ordinary
room tone reached Whisper — and Whisper does not return empty for non-speech,
it confabulates. Real turns recorded from an empty room:

    'The problem was that the case was not coming.'
    "Will you other person I hope or don't like..."
    'Legend and legend do it.'

That is a correctness problem, not a tidiness one. From Phase 4 those strings
reach a router that can act on them, and §7 exists precisely to stop KAVACH
doing things nobody asked for.

`webrtcvad` is a GMM trained on speech, so it separates *voiced* audio from
loud audio — which an amplitude threshold cannot do at any setting.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("kavach.voice.vad")

#: webrtcvad accepts only 10, 20 or 30 ms frames. 20 ms is the usual
#: compromise: fine enough to catch a short word, coarse enough to be stable.
FRAME_MS = 20

#: 0 = permissive, 3 = most aggressive about calling audio non-speech.
#: 3 by design: a missed command costs one repeat, an invented one could act.
DEFAULT_AGGRESSIVENESS = 3

#: ~160 ms of voiced audio. Shorter than any real command, longer than the
#: transients (a door, a keyboard, a chair) that trip the detector for a frame
#: or two.
DEFAULT_MIN_VOICED_FRAMES = 8

#: Stop consonants leave short unvoiced gaps mid-word, so a run is allowed to
#: survive a brief break. Any longer and it is two separate noises.
DEFAULT_MAX_GAP_FRAMES = 2


def frames_of(audio: np.ndarray, sample_rate: int) -> Iterator[np.ndarray]:
    """Split into fixed frames, dropping any partial tail.

    The tail is dropped rather than zero-padded: padding invents non-speech at
    the end of every clip, which biases the very thing being measured.
    """
    size = int(sample_rate * FRAME_MS / 1000)
    if size <= 0:
        return
    for start in range(0, len(audio) - size + 1, size):
        yield audio[start : start + size]


@dataclass
class SpeechGate:
    """Turns a sequence of per-frame verdicts into one decision.

    Kept free of webrtcvad so the policy — how much evidence counts as speech —
    is testable without audio.
    """

    min_voiced_frames: int = DEFAULT_MIN_VOICED_FRAMES
    max_gap_frames: int = DEFAULT_MAX_GAP_FRAMES
    #: Frames that must be voiced back to back, with no gap at all, somewhere
    #: inside the run. Tolerating gaps is necessary for stop consonants, but
    #: gap tolerance alone lets alternating voiced/unvoiced noise accumulate
    #: into an arbitrarily long "run" — 50% duty-cycle noise is not speech.
    #: This demands a solid core as well as overall length.
    min_consecutive_frames: int = 4

    def verdict(self, voiced: list[bool]) -> bool:
        """True if the frames contain a long enough, solid enough run.

        Two conditions, because either alone is foolable:

        * enough voiced frames within one run — speech has duration;
        * an unbroken core inside it — speech is continuous, while noise that
          trips the detector does so intermittently.
        """
        best_total = best_streak = 0
        run_total = streak = gap = 0

        for is_voiced in voiced:
            if is_voiced:
                run_total += 1
                streak += 1
                gap = 0
                best_total = max(best_total, run_total)
                best_streak = max(best_streak, streak)
            else:
                streak = 0
                gap += 1
                if gap > self.max_gap_frames:
                    run_total = 0

        return (
            best_total >= self.min_voiced_frames
            and best_streak >= self.min_consecutive_frames
        )


def voiced_frames(
    audio: np.ndarray,
    sample_rate: int,
    aggressiveness: int = DEFAULT_AGGRESSIVENESS,
) -> list[bool]:
    """Per-frame voiced/unvoiced verdicts from webrtcvad."""
    import webrtcvad

    if sample_rate not in (8000, 16000, 32000, 48000):
        raise ValueError(f"webrtcvad cannot handle {sample_rate} Hz")

    vad = webrtcvad.Vad(aggressiveness)
    out: list[bool] = []
    for frame in frames_of(audio, sample_rate):
        # float32 [-1, 1] → 16-bit PCM, which is the only format it accepts.
        pcm = np.clip(frame * 32767.0, -32768, 32767).astype(np.int16).tobytes()
        try:
            out.append(vad.is_speech(pcm, sample_rate))
        except Exception:  # malformed frame — treat as not speech
            out.append(False)
    return out


#: Above this, the spectrum is flat enough to be noise rather than voice.
#: Measured here: white noise sits at ~0.5-0.9, speech and tones well below.
MAX_SPECTRAL_FLATNESS = 0.30


def spectral_flatness(audio: np.ndarray) -> float:
    """Geometric mean over arithmetic mean of the power spectrum.

    ~1.0 for white noise, low for anything with harmonic structure. This is
    the gap webrtcvad leaves: measured on this machine it calls loud broadband
    noise 100% voiced at *every* aggressiveness, because it was trained to
    separate speech from quiet, not speech from noise. Without this, a noisy
    room still reaches Whisper and Whisper still invents sentences.
    """
    if len(audio) < 512:
        return 0.0
    spectrum = np.abs(np.fft.rfft(audio.astype(np.float64) * np.hanning(len(audio))))
    power = spectrum**2
    power = power[power > 1e-12]
    if len(power) < 8:
        return 0.0
    geometric = np.exp(np.mean(np.log(power)))
    arithmetic = np.mean(power)
    return float(geometric / arithmetic) if arithmetic > 0 else 0.0


def has_speech(
    audio: np.ndarray,
    sample_rate: int,
    aggressiveness: int = DEFAULT_AGGRESSIVENESS,
    gate: SpeechGate | None = None,
    max_flatness: float = MAX_SPECTRAL_FLATNESS,
) -> bool:
    """True if the clip plausibly contains someone talking.

    Two independent checks, because each covers the other's blind spot:
    webrtcvad catches quiet and tonal non-speech, spectral flatness catches
    loud broadband noise that webrtcvad confidently mislabels as voiced.
    """
    if not len(audio):
        return False

    if spectral_flatness(audio) > max_flatness:
        return False

    frames = voiced_frames(audio, sample_rate, aggressiveness)
    return (gate or SpeechGate()).verdict(frames)


def describe(audio: np.ndarray, sample_rate: int) -> str:
    """A one-line summary for the log when a turn is rejected."""
    if not len(audio):
        return "empty clip"
    frames = voiced_frames(audio, sample_rate)
    rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
    return (
        f"{sum(frames)}/{len(frames)} voiced frames, rms {rms:.4f}, "
        f"{len(audio) / sample_rate:.1f}s"
    )
