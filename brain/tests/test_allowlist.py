"""Allowlist tests (spec §7).

The property that matters is the *default*: an app nobody has thought about
must be denied. A blocklist that permits the unknown is not a guardrail.
"""

import pytest

from kavach.hands.allowlist import Allowlist, AppNotAllowed


@pytest.fixture
def allowlist():
    return Allowlist()  # the real hands/allowlist.json, not a fixture copy


def test_ships_with_exactly_the_spec_four(allowlist):
    assert {e["name"] for e in allowlist.entries} == {
        "Safari", "Notes", "Calendar", "Finder"
    }


def test_allows_by_display_name(allowlist):
    for name in ("Safari", "Notes", "Calendar", "Finder"):
        assert allowlist.is_allowed(name)
        allowlist.check(name)  # must not raise


def test_allows_by_bundle_id(allowlist):
    assert allowlist.is_allowed("com.apple.Safari")
    # Calendar's bundle id is com.apple.iCal — read off the machine, not guessed.
    assert allowlist.is_allowed("com.apple.iCal")


def test_matching_is_case_insensitive(allowlist):
    assert allowlist.is_allowed("safari")
    assert allowlist.is_allowed("COM.APPLE.FINDER")


def test_denies_an_unlisted_app(allowlist):
    assert not allowlist.is_allowed("Mail")
    with pytest.raises(AppNotAllowed):
        allowlist.check("Mail")


def test_denies_the_unknown_by_default(allowlist):
    """The point of an allowlist: never-heard-of means no."""
    for app in ("Terminal", "System Settings", "com.evil.malware", "Messages"):
        assert not allowlist.is_allowed(app)


def test_denies_empty_and_whitespace(allowlist):
    for app in ("", "   ", None):
        assert not allowlist.is_allowed(app)


def test_does_not_match_on_substrings(allowlist):
    """'Safari Technology Preview' is a different app from 'Safari'."""
    assert not allowlist.is_allowed("Safari Technology Preview")
    assert not allowlist.is_allowed("com.apple.SafariTechnologyPreview")


def test_flags_destructive_actions_for_confirmation(allowlist):
    for action in ("send email to team", "delete the file",
                   "purchase the item", "submit form", "change system_setting"):
        assert allowlist.needs_confirmation(action), action


def test_does_not_flag_read_only_actions(allowlist):
    for action in ("open Safari", "read the note", "what time is it"):
        assert not allowlist.needs_confirmation(action), action
