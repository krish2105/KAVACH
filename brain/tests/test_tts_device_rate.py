"""Kokoro speaks at 24 kHz. The speakers run at 48 kHz. Something must convert.

Reported by the user as replies "breaking the speaker very badly". The
instrumentation added on 2026-08-14 for the same complaint ("fuzzy") had
been reporting **clean** the whole time:

    playback: 2.43s audio at 24000 Hz, peak 0.714, took 2.52s, status=clean

Peak 0.714 is nowhere near clipping, elapsed matches duration, and PortAudio
raised no underflow. Every one of those numbers is about the samples we
generated and none of them is about what the device did with them.

Measured instead — the hardware's own rate while a 24 kHz stream plays:

    during 24k play:  Current SampleRate: 48000

**The device never left 48 kHz.** `sd.play(audio, 24000)` does not make the
speakers run at 24 kHz; it makes PortAudio's CoreAudio backend resample, and
that converter is cheap. Nothing fails, so nothing is reported — which is
exactly why the diagnostic came back clean for two days.

`sd.check_output_settings(samplerate=24000)` also passes, and means only
"I can accept this", not "the hardware will run at it".

The fix is to resample where quality is a choice we make: `resample_poly`,
24000 → 48000, an exact 2× polyphase upsample with a proper anti-imaging
filter. This is the same correction the microphone path already received,
where a naive `block[::3]` decimator was replaced on the same grounds.
"""

import numpy as np
import pytest

from kavach.voice.tts import resample_for_device


def _tone(freq, seconds, rate):
    t = np.arange(int(seconds * rate)) / rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _dominant_freq(audio, rate):
    spectrum = np.abs(np.fft.rfft(audio.astype(np.float64)))
    return float(np.fft.rfftfreq(len(audio), 1 / rate)[int(np.argmax(spectrum))])


def test_the_rate_is_changed_to_the_device_rate():
    audio, rate = resample_for_device(_tone(440, 1.0, 24_000), 24_000, 48_000)

    assert rate == 48_000
    assert len(audio) == pytest.approx(48_000, rel=0.01), (
        "a 1s clip must still be 1s — a length change is a pitch change"
    )


def test_the_pitch_survives():
    """The whole point. A resampler that shifts pitch has turned a quality
    problem into a comedy one."""
    audio, rate = resample_for_device(_tone(440, 1.0, 24_000), 24_000, 48_000)

    assert _dominant_freq(audio, rate) == pytest.approx(440, abs=3)


def test_no_new_harmonics_are_introduced():
    """What "breaking the speaker" sounds like: energy at frequencies the
    source never contained. A pure tone must stay pure."""
    audio, rate = resample_for_device(_tone(1_000, 1.0, 24_000), 24_000, 48_000)

    spectrum = np.abs(np.fft.rfft(audio.astype(np.float64)))
    freqs = np.fft.rfftfreq(len(audio), 1 / rate)
    fundamental = spectrum[np.argmin(np.abs(freqs - 1_000))]
    spurious = spectrum[(freqs > 1_100) | (freqs < 900)].max()

    assert spurious < fundamental * 0.01, (
        f"spurious energy at {spurious / fundamental:.1%} of the fundamental"
    )


def test_it_does_not_clip():
    """Polyphase interpolation overshoots. Overshoot past 1.0 is the literal
    speaker-destroying case, and it must be scaled rather than wrapped."""
    audio, _ = resample_for_device(_tone(440, 0.5, 24_000) * 1.9, 24_000, 48_000)

    assert np.max(np.abs(audio)) <= 1.0, f"peak {np.max(np.abs(audio)):.3f}"


def test_a_matching_rate_is_left_alone():
    """No resample, no filter, no chance to make it worse."""
    original = _tone(440, 0.5, 48_000)

    audio, rate = resample_for_device(original, 48_000, 48_000)

    assert rate == 48_000
    assert np.array_equal(audio, original)


def test_an_unknown_device_rate_plays_rather_than_raising():
    """A headless machine, a disconnected device, a PortAudio that will not
    answer. Silence is a worse failure than an imperfect resample."""
    audio, rate = resample_for_device(_tone(440, 0.5, 24_000), 24_000, None)

    assert rate == 24_000
    assert len(audio) == 12_000


def test_empty_audio_is_survivable():
    audio, rate = resample_for_device(np.zeros(0, dtype=np.float32), 24_000, 48_000)
    assert len(audio) == 0


@pytest.mark.parametrize("target", [44_100, 48_000, 96_000])
def test_common_device_rates(target):
    """Bluetooth headphones and external interfaces are not all 48k. 24000 →
    44100 is 147/80, which resample_poly reduces itself."""
    audio, rate = resample_for_device(_tone(440, 1.0, 24_000), 24_000, target)

    assert rate == target
    assert _dominant_freq(audio, rate) == pytest.approx(440, abs=3)


# ═══ the call site ═══

def test_play_sends_the_device_rate_to_sounddevice(monkeypatch):
    """Asserted on the call, because the bug was entirely in what rate was
    handed over — the samples were always fine."""
    import kavach.voice.tts as tts

    sent = {}

    class FakeSD:
        default = type("d", (), {"samplerate": None})()

        @staticmethod
        def play(audio, rate, **kw):
            sent["rate"], sent["len"] = rate, len(audio)

        @staticmethod
        def wait():
            pass

        @staticmethod
        def query_devices(kind=None):
            return {"default_samplerate": 48_000.0, "name": "Fake"}

    monkeypatch.setitem(__import__("sys").modules, "sounddevice", FakeSD)
    tts.play(tts.Speech(audio=_tone(440, 1.0, 24_000), sample_rate=24_000,
                        voice="af_heart"))

    assert sent["rate"] == 48_000, (
        f"handed {sent['rate']} Hz to a 48000 Hz device — PortAudio converts "
        f"it silently and badly, which is the bug"
    )
    assert sent["len"] == pytest.approx(48_000, rel=0.01)
