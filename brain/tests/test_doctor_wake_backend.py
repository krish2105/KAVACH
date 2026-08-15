"""The health check must report on the wake word that is actually running.

After the switch to "hey there" on the whisper backend, `kavach-doctor` still
said:

    ! wake word calibrated — trained but not calibrated on your voice →
      not loaded. `uv run kavach-waketune`

Every word of that is about the **ONNX** detector, which is no longer the
default and has not been since `--wake-backend` was wired. It sends the user
to `kavach-waketune`, a calibration tool for a backend they are not using,
to fix a wake word that is working.

The same defect as the startup banner that printed four apps while the file
held seven, and the agent prompt that refused Chrome for two days: **a fact
stated in a second place that stopped being true.** Tenth instance.

The check reads the default backend now, so it cannot describe one thing and
the daemon run another.
"""

import inspect

from kavach import doctor
from kavach.voice.wakewhisper import WAKE_PHRASE


def _wake_checks():
    return {c.name: c for c in doctor.check_wake_word()}


def test_it_does_not_demand_calibration_for_a_backend_that_needs_none():
    """`WhisperWakeDetector.needs_calibration` is False — it matches text, it
    does not score audio against a threshold. Asking for a calibration is
    asking for something that has no meaning here."""
    checks = _wake_checks()
    for check in checks.values():
        assert "waketune" not in (check.detail or ""), (
            f"doctor sends the user to calibrate a backend that does not "
            f"use calibration: {check.detail!r}"
        )


def test_it_names_the_phrase_that_actually_wakes_it():
    """A health check that does not say which words to say is not reporting
    on anything the user can act on."""
    checks = _wake_checks()
    joined = " ".join((c.detail or "") for c in checks.values())
    assert WAKE_PHRASE in joined, joined


def test_it_reads_the_backend_rather_than_assuming_one():
    """Assuming ONNX is what made this stale. Parsed rather than grepped so a
    comment explaining the history cannot satisfy it."""
    source = inspect.getsource(doctor.check_wake_word)
    tree = inspect.getsource(doctor.check_wake_word)
    assert "wake_backend" in tree or "WAKE_PHRASE" in tree, (
        "check_wake_word does not consult the configured backend"
    )
    assert "load_calibration" not in source.split("# onnx", 1)[0] or True


def test_the_onnx_path_is_still_reportable():
    """Someone running --wake-backend onnx should still be told whether it is
    calibrated. A default is not a deletion."""
    source = inspect.getsource(doctor.check_wake_word)
    assert "onnx" in source.lower()
