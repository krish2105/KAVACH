"""The .app wrapper that lets macOS grant the camera.

Gestures were dead for a reason that looked like broken hardware: macOS will
not show a camera prompt to a process with no app bundle, so `request_camera()`
was refused in ~100 ms and `camera_status()` stayed "not determined" forever.
`permission.py` was correct the whole time.

These check the bundle has the parts macOS actually requires. A bundle missing
`NSCameraUsageDescription` does not fail loudly — it silently reproduces the
original bug, which is why it is asserted rather than assumed.
"""

import plistlib

import pytest

from kavach.presence import appbundle


@pytest.fixture
def app(tmp_path):
    return appbundle.build(tmp_path)


def test_the_bundle_has_a_camera_usage_description(app):
    """The missing piece. Without it macOS has nothing to put in the dialog
    and declines before showing one."""
    info = appbundle.describe(app)

    assert info.get("NSCameraUsageDescription")
    assert len(info["NSCameraUsageDescription"]) > 40, \
        "the reason is shown to a human deciding; it should say something"


def test_the_bundle_has_an_identifier(app):
    """TCC attributes permission to a bundle id. Without one there is nothing
    to attribute it to, and the grant cannot persist."""
    assert appbundle.describe(app)["CFBundleIdentifier"] == appbundle.BUNDLE_ID


def test_the_microphone_reason_is_there_too(app):
    info = appbundle.describe(app)
    assert info.get("NSMicrophoneUsageDescription")


def test_the_reasons_say_what_happens_to_the_data():
    """Both dialogs are the only place a person is told. "Would like to access
    the camera" with no reason is how people learn to click Deny."""
    for reason in (appbundle.CAMERA_REASON, appbundle.MICROPHONE_REASON):
        assert "never saved" in reason.lower()


def test_the_interpreter_is_copied_not_symlinked(app):
    """macOS resolves symlinks and the bundle association is lost with them."""
    python = app / "Contents" / "MacOS" / "python3"

    assert python.exists()
    assert not python.is_symlink(), "a symlinked interpreter loses the bundle"


def test_the_launcher_runs_the_overlay_not_a_repl(app):
    """`open KAVACH.app` passes no arguments. With the interpreter as the
    bundle executable that lands in an interactive Python and nothing starts."""
    launcher = app / "Contents" / "MacOS" / appbundle.APP_NAME
    script = launcher.read_text()

    assert "kavach.presence" in script
    assert launcher.stat().st_mode & 0o111, "the launcher is not executable"


def test_nothing_in_the_bundle_points_outside_it(app):
    """The fix, and the thing that must never come back.

    The first version linked Contents/lib at the virtualenv. It worked, and
    the camera stayed refused, because macOS rejects a bundle whose symlinks
    escape it — "invalid destination for symbolic link in bundle" — the
    signature then fails to validate, and TCC declines an unverifiable bundle
    without ever showing a prompt. Which looks exactly like broken hardware.
    """
    escaping = [p for p in app.rglob("*")
                if p.is_symlink() and not str(p.resolve()).startswith(str(app))]

    assert not escaping, f"these break the signature seal: {escaping}"


def test_the_venv_is_reachable_by_environment_not_by_link(app):
    """An environment variable is not part of the code signature, so the
    packages resolve without breaking the seal."""
    script = (app / "Contents" / "MacOS" / appbundle.APP_NAME).read_text()

    assert "PYTHONPATH" in script
    assert "site-packages" in script


def test_the_bundle_actually_verifies(app):
    """TCC refuses a bundle that does not verify, silently. Asserted with the
    real codesign rather than trusted."""
    import subprocess

    done = subprocess.run(["codesign", "--verify", "--strict", str(app)],
                          capture_output=True, text=True)

    assert done.returncode == 0, done.stderr


def test_it_is_an_agent_not_a_dock_app(app):
    """The orb is a floating panel. A Dock icon for it is noise."""
    assert appbundle.describe(app)["LSUIElement"] is True


def test_rebuilding_replaces_cleanly(tmp_path):
    first = appbundle.build(tmp_path)
    (first / "Contents" / "stale.txt").write_text("left over")

    second = appbundle.build(tmp_path)

    assert not (second / "Contents" / "stale.txt").exists()
