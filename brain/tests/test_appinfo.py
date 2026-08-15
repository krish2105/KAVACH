"""Resolving a spoken app name — the injection defence, after the allowlist.

Until now the guarantee was: *the transcript never reaches AppleScript*. The
string that landed in ``tell application "…"`` was `allowlist.json`'s spelling,
looked up by `Allowlist.canonical_name()`, so `open notes` ran
``tell application "Notes"`` and a hostile transcript resolved to nothing.

Removing the allowlist would have deleted that defence outright. This replaces
it with Launch Services, which is strictly stronger: it canonicalises every
installed app rather than seven, and still answers None for anything that is
not installed — so a mis-transcription resolves to nothing rather than to a
script.

The property under test is *construction*, not escaping. A name containing a
quote is not a name to be escaped and run; it is not an app.
"""

import pytest

from kavach.hands.appinfo import bundle_id, canonical_name


# ═══ the spelling macOS actually uses ═══

@pytest.mark.parametrize("spoken,expected", [
    ("notes", "Notes"),
    ("Notes", "Notes"),
    ("NOTES", "Notes"),
    ("safari", "Safari"),
    ("google chrome", "Google Chrome"),
    ("Google Chrome", "Google Chrome"),
])
def test_an_installed_app_resolves_to_its_real_name(spoken, expected):
    """Whisper returns lowercase; AppleScript needs the real spelling."""
    assert canonical_name(spoken) == expected


def test_a_bare_vendor_less_name_still_resolves():
    """Measured: fullPathForApplication_('Chrome') is None, but
    ('Google Chrome') resolves. Nobody says the brand name out loud, so the
    common vendor prefixes are tried before giving up."""
    assert canonical_name("Chrome") == "Google Chrome"


def test_an_app_this_machine_does_not_have_is_none():
    assert canonical_name("NotARealApplication") is None


# ═══ what must never reach a script ═══

@pytest.mark.parametrize("hostile", [
    'Notes"; do shell script "rm -rf ~',      # close the string, run a command
    'Notes" & (do shell script "id") & "',
    "Notes\\",
    "Notes;",
    'Notes"',
    "tell application \"Terminal\" to do script \"whoami\"",
])
def test_a_hostile_transcript_is_not_a_name(hostile):
    """Injection is ruled out by construction. The charset excludes the three
    characters that could end an AppleScript string, so these are *not app
    names* rather than names needing escaping."""
    assert canonical_name(hostile) is None


@pytest.mark.parametrize("empty", [None, "", "   ", "\t\n"])
def test_nothing_resolves_to_nothing(empty):
    assert canonical_name(empty) is None


def test_an_absurdly_long_name_is_refused():
    """A transcript is not length-bounded; an app name is."""
    assert canonical_name("A" * 500) is None


# ═══ bundle ids ═══

def test_bundle_id_of_a_real_app():
    assert bundle_id("notes") == "com.apple.Notes"


def test_bundle_id_of_something_uninstalled_is_none():
    assert bundle_id("NotARealApplication") is None


def test_bundle_id_refuses_the_same_hostile_input():
    """It routes through canonical_name, so the defence is not duplicated —
    two copies of one rule is how one of them goes stale."""
    assert bundle_id('Notes"; do shell script "id') is None
