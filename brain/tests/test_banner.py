"""What the startup banner claims, versus what is actually loaded.

The banner is the only thing most people read before speaking to KAVACH. Three
of its lines were decorative rather than true:

* **the allowlist was a hardcoded string.** `"Safari, Notes, Calendar, Finder"`
  printed literally, while the file behind it had grown to seven apps including
  Chrome. §7 says ask before expanding the allowlist — which is worth very
  little if the running system misreports what it will drive.
* **the reasoning model printed the command-line argument, not the model.**
  Unset, that is `None`, so a correctly-configured KAVACH announced
  `router → None | claude` while happily using llama3.2:3b.
* **an uncalibrated wake word was reported as a missing file**, naming a path
  that exists. Two different problems, one message, and the wrong fix implied.

None of these change behaviour, which is exactly why they lasted: everything
worked, and the report of what was working was wrong.
"""

import json
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[1]
MAIN = BRAIN / "kavach" / "voice" / "__main__.py"
ALLOWLIST = BRAIN.parent / "hands" / "allowlist.json"


def test_the_allowlist_line_is_not_hardcoded():
    """It named four apps in a string literal while the file held seven."""
    source = MAIN.read_text()

    for app in ("Safari, Notes", "Calendar, Finder"):
        assert app not in source, \
            f"the banner prints {app!r} literally instead of reading the file"


def test_the_allowlist_line_comes_from_the_allowlist():
    source = MAIN.read_text()
    start = source.index("allowlist")
    line = source[start - 120:start + 200]

    assert "Allowlist" in line or "allowed_apps" in line or "allowlist." in line, \
        "the banner does not consult the allowlist it is describing"


def test_every_allowed_app_would_be_named():
    """A banner that silently truncates is the same failure in a nicer suit."""
    from kavach.hands.allowlist import Allowlist

    names = Allowlist().app_names()
    on_disk = json.loads(ALLOWLIST.read_text())["devices"]["mac"]["allowed"]

    assert len(names) == len(on_disk), \
        f"banner would name {len(names)} of {len(on_disk)} allowed apps"


def test_chrome_appears_now_that_it_is_allowed():
    """It was added at the user's request and the banner never mentioned it."""
    from kavach.hands.allowlist import Allowlist

    assert any("chrome" in n.lower() for n in Allowlist().app_names())


def test_the_model_line_shows_the_resolved_model():
    """`args.local_model` is None unless overridden, so the banner announced
    `router → None` for a perfectly healthy setup."""
    source = MAIN.read_text()

    assert "router → {args.local_model}" not in source, \
        "the banner prints the argument rather than the model in use"


def test_an_uncalibrated_wake_word_is_not_reported_as_a_missing_file():
    """The model was present at the exact path the message called missing.

    The real cause was calibration, which the line above it already said — so
    the two lines contradicted each other and the actionable one was second.
    """
    loop = (BRAIN / "kavach" / "voice" / "loop.py").read_text()
    start = loop.index("push-to-talk only. Train with")
    around = loop[start - 400:start + 900]

    assert "not self.wake.available" in around, \
        "the missing-model branch does not check whether the model is missing"
    assert "uncalibrated" in around, \
        "an uncalibrated model still reports as a missing file"


def test_doctor_reads_the_log_the_overlay_writes():
    """They drifted, and doctor reported a false failure for it.

    The overlay's log moved to ~/.kavach/logs/overlay.log; doctor kept reading
    brain/wakeword/logs/overlay.log, parsed a stale "page reports" line, and
    declared the panel broken while it was rendering. A health check that fails
    on a healthy system teaches you to ignore failures.
    """
    doctor = (BRAIN / "kavach" / "doctor.py").read_text()
    presence = (BRAIN / "kavach" / "presence" / "__main__.py").read_text()

    assert 'BRAIN / "wakeword" / "logs" / "overlay.log"' not in doctor, \
        "doctor reads the overlay's old log location"
    assert '".kavach" / "logs" / "overlay.log"' in doctor
    assert '".kavach" / "logs" / "overlay.log"' in presence, \
        "the overlay no longer writes where doctor reads"
