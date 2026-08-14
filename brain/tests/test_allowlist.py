"""Allowlist tests (spec §7).

The property that matters is the *default*: an app nobody has thought about
must be denied. A blocklist that permits the unknown is not a guardrail.
"""

import pytest

from kavach.hands.allowlist import Allowlist, AppNotAllowed


@pytest.fixture
def allowlist():
    return Allowlist()  # the real hands/allowlist.json, not a fixture copy


#: Every app the user has explicitly approved, and why.
#:
#: §C says the allowlist grows only by asking. This set is the record of those
#: answers, so an app appearing here without a line in this dict fails the
#: test below — which is the point: the danger is not a considered addition,
#: it is one that arrives unnoticed.
APPROVED = {
    "Safari": "spec §7 starting four",
    "Notes": "spec §7 starting four",
    "Calendar": "spec §7 starting four",
    "Finder": "spec §7 starting four",
    "Music": "user asked for spoken music control, 2026-08-13",
    "Spotify": "user asked for spoken music control, 2026-08-13",
    # Asked for explicitly on 2026-08-13, to scroll and zoom it by hand.
    #
    # Worth recording that this list gates BOTH paths: adding Chrome here also
    # lets KAVACH's MCP tools act on it, not only your hand. That is the
    # consequence of one list rather than two, and it was chosen deliberately —
    # two lists is how one of them goes stale.
    "Google Chrome": "user asked, 2026-08-13, for hand scroll/zoom — also "
                     "grants MCP tools access to Chrome",
}


def test_the_spec_four_are_always_present(allowlist):
    """These four are the floor. Removing one is as much a change as adding."""
    assert {"Safari", "Notes", "Calendar", "Finder"} <= {
        e["name"] for e in allowlist.entries
    }


def test_nothing_is_allowed_that_was_not_approved(allowlist):
    """The test that actually protects §7.

    It used to assert the list was exactly the spec four, which meant any
    approved addition broke it and the fix was to loosen the assertion. Keying
    on a recorded approval instead keeps it strict: an app added without a
    reason still fails.
    """
    unapproved = {e["name"] for e in allowlist.entries} - set(APPROVED)
    assert not unapproved, f"not approved by anyone: {unapproved}"


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


def test_canonical_name_returns_the_file_spelling(allowlist):
    """What goes into an AppleScript is the approved spelling, never the
    transcript. "open notes" is matched case-insensitively and executed as
    `tell application "Notes"` — so a name that is not already on the list
    cannot reach a script at all."""
    assert allowlist.canonical_name("notes") == "Notes"
    assert allowlist.canonical_name("com.apple.Safari") == "Safari"
    assert allowlist.canonical_name("Mail") is None


# ═══ growing the list ═══
#
# §C says the allowlist grows only by asking. `add` is what "yes" does — it is
# reached from one place, behind a spoken confirmation *and* speaker
# verification (see test_actions.py). These tests are about the write itself:
# that it records why, that it cannot be done silently, and that it cannot
# damage the rest of the file.

import json  # noqa: E402  — used only by the write tests below


@pytest.fixture
def writable(tmp_path):
    """A copy of the real file, so these tests never edit the repo's."""
    path = tmp_path / "allowlist.json"
    path.write_text(Allowlist().path.read_text())
    return Allowlist(path)


def test_add_persists_the_app(writable):
    writable.add("Mail", "com.apple.mail", reason="added by voice, 2026-08-14")

    assert writable.is_allowed("Mail")
    assert Allowlist(writable.path).is_allowed("com.apple.mail"), \
        "the addition did not survive a reload — it was never written"


def test_add_records_why(writable):
    writable.add("Mail", "com.apple.mail", reason="added by voice, 2026-08-14")

    entry = next(e for e in Allowlist(writable.path).entries
                 if e["name"] == "Mail")
    assert entry["reason"] == "added by voice, 2026-08-14"


def test_add_refuses_an_entry_with_no_reason(writable):
    """An app arriving with no recorded reason is exactly the failure
    `test_nothing_is_allowed_that_was_not_approved` exists to catch."""
    for reason in ("", "   ", None):
        with pytest.raises(ValueError):
            writable.add("Mail", "com.apple.mail", reason=reason)
    assert not writable.is_allowed("Mail")


def test_add_refuses_an_incomplete_entry(writable):
    for name, bundle_id in (("", "com.apple.mail"), ("Mail", ""),
                            (None, None)):
        with pytest.raises(ValueError):
            writable.add(name, bundle_id, reason="because")


def test_adding_twice_does_not_duplicate(writable):
    writable.add("Mail", "com.apple.mail", reason="first")
    writable.add("Mail", "com.apple.mail", reason="second")

    names = [e["name"] for e in Allowlist(writable.path).entries]
    assert names.count("Mail") == 1


def test_add_leaves_the_rest_of_the_file_intact(writable):
    """The iPhone is governed by tool, not by app, and its section has nothing
    to do with adding a Mac app. A rewrite that dropped it would silently widen
    the phone's grants."""
    before = json.loads(writable.path.read_text())
    writable.add("Mail", "com.apple.mail", reason="added by voice")
    after = json.loads(writable.path.read_text())

    assert after["devices"]["iphone"] == before["devices"]["iphone"]
    assert after["confirm_always"] == before["confirm_always"]
    assert after["version"] == before["version"]


def test_flags_destructive_actions_for_confirmation(allowlist):
    for action in ("send email to team", "delete the file",
                   "purchase the item", "submit form", "change system_setting"):
        assert allowlist.needs_confirmation(action), action


def test_does_not_flag_read_only_actions(allowlist):
    for action in ("open Safari", "read the note", "what time is it"):
        assert not allowlist.needs_confirmation(action), action
