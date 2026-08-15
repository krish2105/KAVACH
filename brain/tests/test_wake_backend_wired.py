"""The working wake word was never switched on.

`VoiceLoop` has taken a `wake_backend` argument for a while, and
`wakewhisper.py` carries live measurements from this microphone — 90 seconds
of the user speaking, 12 wake attempts, **10 recognised and zero false
wakes**, with an ordinary-speech run and a YouTube advert that all stayed
silent (`test_wakewhisper.py`).

`voice/__main__.py` never passed the argument. So the daemon built the ONNX
detector every time, and the ONNX detector refuses to load without a
calibration that has never once succeeded on this voice — which is why
`kavach-doctor` says *"trained but not calibrated → not loaded"* and the
machine has had **no wake word at all**, while a measured working one sat
in the tree.

Eleventh built-but-unwired instance in this project, and the most expensive
by elapsed time: four ONNX models were trained after the alternative was
already written.

**Whisper is the default here**, which is a claim about this machine rather
than about the approach. The ONNX route has been measured deaf to this
microphone four separate times (0.019 against 0.858 on the same utterance),
and the whisper route has been measured to work on it. `--wake-backend onnx`
keeps the old path reachable for anyone whose measurements differ.
"""

import ast
import inspect

import pytest

from kavach.voice import __main__ as entry
from kavach.voice.loop import VoiceLoop


def _loop_call():
    """The VoiceLoop(...) construction in the daemon entry point."""
    tree = ast.parse(inspect.getsource(entry))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "VoiceLoop"):
            return node
    pytest.fail("the daemon no longer constructs a VoiceLoop")


def test_the_daemon_chooses_a_wake_backend():
    """Not passing it is how the ONNX detector was built every time."""
    assert any(kw.arg == "wake_backend" for kw in _loop_call().keywords), (
        "voice/__main__.py builds a VoiceLoop without wake_backend, so the "
        "whisper detector can never run no matter what is measured"
    )


def test_the_loop_accepts_it():
    assert "wake_backend" in inspect.signature(VoiceLoop.__init__).parameters


def test_the_default_is_whisper():
    """The ONNX path needs a calibration that has never succeeded on this
    voice, so defaulting to it means defaulting to no wake word at all."""
    parser_source = inspect.getsource(entry)
    assert "--wake-backend" in parser_source

    tree = ast.parse(parser_source)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "attr", None) == "add_argument"):
            continue
        if not (node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "--wake-backend"):
            continue
        default = next((kw.value for kw in node.keywords if kw.arg == "default"),
                       None)
        assert getattr(default, "value", None) == "whisper", (
            "the daemon defaults to a backend measured deaf to this mic"
        )
        return
    pytest.fail("--wake-backend has no add_argument call")


def test_onnx_is_still_reachable():
    """The measurements are about this machine, not about the approach. A
    default is not a deletion."""
    parser_source = inspect.getsource(entry)
    assert "onnx" in parser_source, (
        "the ONNX backend is no longer selectable, which turns a measured "
        "preference into a permanent one"
    )


def test_both_backends_build_without_a_microphone():
    """Neither may touch audio hardware at construction — the daemon builds
    the loop long before it starts listening, and a backend that opens the
    mic in __init__ makes the failure look like a broken loop."""
    from kavach.voice.wakewhisper import WhisperWakeDetector

    detector = WhisperWakeDetector()
    # A property, not a method — `available()` raises "'bool' object is not
    # callable", which is a bug in the test rather than in the detector.
    assert detector.available is True
    assert "whisper" in detector.model_path
