"""The health check for the speaker gate must actually exercise the gate.

`kavach-doctor` reported:

    ✓ voiceprint rejects another voice   — similarity 0.000 < 0.383

It was not rejecting anything. The check synthesised *"Delete the draft in
Notes."* — **1.73 seconds** — and `verify()` refuses anything under
`MIN_VERIFY_SECONDS` (3.0) before it ever looks at the audio, returning
`accepted=False, similarity=0.0`. The check passed because the clip was
short, and would have passed identically for the **user's own voice**.

That is the third false-green in two days, all the same shape: a test whose
pass condition is met by a path that never reaches the thing under test.
The others were a grep for `"read_text("` against a token stream with no
parens in it, and a wiring check that split source on `")"` and stopped at
an inner paren.

The line is long enough to clear the duration gate now, and the tests below
assert *why* the check passed — not just that it did. A check that cannot
tell "rejected you" from "declined to look" is not a check.
"""

import pytest

pytest.importorskip("scipy")

from kavach.identity.voiceprint import MIN_VERIFY_SECONDS


def _doctor_clip():
    """Exactly what the doctor feeds its verifier."""
    import numpy as np
    import scipy.signal as ss

    from kavach.doctor import OTHER_VOICE_LINE
    from kavach.voice.loop import DEFAULT_MODELS_DIR
    from kavach.voice.tts import TextToSpeech

    tts = TextToSpeech(DEFAULT_MODELS_DIR)
    tts.load()
    speech = tts.synthesize(OTHER_VOICE_LINE, voice="am_michael")
    return ss.resample_poly(speech.audio, 16_000,
                            speech.sample_rate).astype(np.float32)


@pytest.mark.slow
def test_the_other_voice_clip_is_long_enough_to_be_judged():
    """The bug, as a test. Below the duration floor the answer is "I cannot
    tell", and a health check must not read that as "rejected"."""
    audio = _doctor_clip()
    seconds = len(audio) / 16_000

    assert seconds >= MIN_VERIFY_SECONDS, (
        f"the doctor's other-voice clip is {seconds:.2f}s, under the "
        f"{MIN_VERIFY_SECONDS}s floor — verify() refuses it without looking, "
        f"so the check passes for any voice including the enrolled one"
    )


@pytest.mark.slow
def test_the_rejection_is_a_real_one(tmp_path):
    """It must be refused *on the voice*, not on the clip length."""
    import numpy as np

    from kavach.identity.voiceprint import Voiceprint

    vp = Voiceprint(path=tmp_path / "voiceprint.npz")
    if not vp.is_enrolled:
        # Enrol on a synthetic voice so the test does not depend on the
        # machine's real profile — what is asserted is the *reason*, which
        # holds regardless of who is enrolled.
        rng = np.random.default_rng(0)
        clips = [rng.normal(0, 0.05, 16_000 * 4).astype(np.float32)
                 for _ in range(3)]
        vp.enrol(clips, sample_rate=16_000)

    result = vp.verify(_doctor_clip(), sample_rate=16_000)

    assert "too short" not in result.reason, (
        f"declined to look rather than judged: {result.reason!r}"
    )


def test_the_line_is_defined_once():
    """It was a literal inside the check. Kept as a module constant so the
    test above measures the same audio the doctor does — a duration assertion
    against a *different* string proves nothing, and this project has now
    found one-fact-in-two-places nine times."""
    import inspect

    from kavach import doctor

    assert hasattr(doctor, "OTHER_VOICE_LINE")
    source = inspect.getsource(doctor.check_voice_gates)
    assert "OTHER_VOICE_LINE" in source
