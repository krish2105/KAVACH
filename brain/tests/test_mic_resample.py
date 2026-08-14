"""The mic's 48 kHz → 16 kHz step, and the aliasing it used to add.

`_decimate()` took every third sample, justified as "speech energy above 8 kHz
is negligible". Aliased energy does not stay where it was: content above 8 kHz
folds back into the speech band, on top of the formants both Whisper and the
wake word read.

**Written while chasing the wake word, and it was not the cause.** On the same
utterance the old slice and the filtered resampler score identically — 0.857
against 0.857 — so nothing measurable was being lost. These tests stand on
their own terms: they pin the anti-aliasing property, which is correct
regardless of what it did or did not fix.
"""

import numpy as np

from kavach.voice.mic import TARGET_RATE, Resampler

NATIVE = 48000


def tone(freq: float, seconds: float = 0.25, rate: int = NATIVE) -> np.ndarray:
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def energy_at(signal: np.ndarray, freq: float, rate: int = TARGET_RATE) -> float:
    """How much of `signal` sits at `freq`, by a single-bin DFT."""
    n = len(signal)
    k = np.exp(-2j * np.pi * freq * np.arange(n) / rate)
    return float(abs(np.dot(signal, k)) / n)


# ═══ the fold-back ═══

def test_a_tone_above_nyquist_does_not_reappear_as_speech():
    """12 kHz sampled at 16 kHz folds to 4 kHz — right in the formant range.

    Naive decimation produced exactly this: a 4 kHz component nobody spoke,
    printed on top of the band both models read.
    """
    r = Resampler(NATIVE, TARGET_RATE)

    out = r.process(tone(12000))

    assert energy_at(out, 4000) < 0.02, \
        "12 kHz folded back to 4 kHz — the resampler is not filtering"


def test_ultrasonic_content_is_removed_not_folded():
    r = Resampler(NATIVE, TARGET_RATE)

    out = r.process(tone(20000))

    assert float(np.sqrt(np.mean(out**2))) < 0.05, \
        "20 kHz survived the downsample as in-band noise"


def test_speech_frequencies_survive():
    """The filter must not take the voice with it. 1 kHz is mid-formant."""
    r = Resampler(NATIVE, TARGET_RATE)

    out = r.process(tone(1000))

    assert energy_at(out, 1000) > 0.15, "the passband is being attenuated"


def test_the_rate_is_actually_converted():
    r = Resampler(NATIVE, TARGET_RATE)

    out = r.process(tone(1000, seconds=1.0))

    assert abs(len(out) - TARGET_RATE) <= 2, f"got {len(out)} samples, want ~16000"


# ═══ block-by-block, because the mic arrives in 32 ms pieces ═══

def test_filtering_is_continuous_across_blocks():
    """A filter restarted per block rings at every boundary.

    The mic delivers 32 ms blocks, so a stateless filter would stamp a
    discontinuity 31 times a second into whatever the model hears.
    """
    whole = tone(1000, seconds=0.96)
    block = len(whole) // 30

    one_shot = Resampler(NATIVE, TARGET_RATE).process(whole)

    r = Resampler(NATIVE, TARGET_RATE)
    streamed = np.concatenate([r.process(whole[i:i + block])
                               for i in range(0, len(whole), block)])

    n = min(len(one_shot), len(streamed))
    # Allow the first few samples to differ while the filter primes.
    assert np.allclose(one_shot[80:n], streamed[80:n], atol=2e-3), \
        "per-block filtering does not match filtering the whole signal"


def test_a_block_shorter_than_the_factor_is_handled():
    r = Resampler(NATIVE, TARGET_RATE)

    out = r.process(np.zeros(2, dtype=np.float32))

    assert len(out) <= 1


def test_it_returns_float32():
    """Whisper and the wake word both want float32; float64 doubles the copy
    cost on every block."""
    r = Resampler(NATIVE, TARGET_RATE)

    assert r.process(tone(440)).dtype == np.float32


def test_a_rate_that_is_not_a_multiple_still_works():
    """44.1 kHz is not 3x16 kHz, and a slice-based decimator could not do it at
    all — the old code raised instead."""
    r = Resampler(44100, TARGET_RATE)

    out = r.process(tone(1000, seconds=1.0, rate=44100))

    assert abs(len(out) - TARGET_RATE) <= 50


# ═══ the comment that justified it ═══

def test_the_naive_decimation_is_gone():
    source = (__import__("pathlib").Path(__file__).resolve().parents[1]
              / "kavach" / "voice" / "mic.py").read_text()

    assert "block[::factor]" not in source, \
        "the aliasing decimator is back"
