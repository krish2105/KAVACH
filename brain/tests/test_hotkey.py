"""Hotkey chord-matching logic.

The listener itself needs a real keypress and a granted Input Monitoring
permission, so it can't be asserted here. The *matching* is pure logic and is
worth pinning down: a mask bug would either make the kill switch fire on
unrelated keys or — far worse — never fire at all.
"""

import Cocoa
import pytest

from kavach.killswitch.hotkey import (
    DEFAULT_KEY,
    DEFAULT_MODIFIERS,
    HotkeyListener,
    describe,
)

CTRL = Cocoa.NSEventModifierFlagControl
OPT = Cocoa.NSEventModifierFlagOption
CMD = Cocoa.NSEventModifierFlagCommand
SHIFT = Cocoa.NSEventModifierFlagShift
CAPSLOCK = Cocoa.NSEventModifierFlagCapsLock


class FakeEvent:
    """Stands in for NSEvent — only the two accessors the matcher uses."""

    def __init__(self, modifiers: int, chars: str):
        self._modifiers = modifiers
        self._chars = chars

    def modifierFlags(self) -> int:
        return self._modifiers

    def charactersIgnoringModifiers(self) -> str:
        return self._chars


@pytest.fixture
def listener():
    return HotkeyListener(on_trigger=lambda: None)


def test_matches_the_default_chord(listener):
    assert listener._matches(FakeEvent(CTRL | OPT | CMD, "k"))


def test_matches_uppercase_k(listener):
    """Shift is not in the chord, but the key still arrives as 'K' on some
    layouts — the comparison is case-insensitive."""
    assert listener._matches(FakeEvent(CTRL | OPT | CMD, "K"))


def test_ignores_caps_lock_and_other_incidental_flags(listener):
    """Caps lock / numeric-pad bits ride along on real events. If the matcher
    compared raw flags, the kill switch would silently stop working whenever
    caps lock was on."""
    assert listener._matches(FakeEvent(CTRL | OPT | CMD | CAPSLOCK, "k"))


def test_rejects_a_subset_of_the_modifiers(listener):
    assert not listener._matches(FakeEvent(CMD, "k"))
    assert not listener._matches(FakeEvent(CTRL | CMD, "k"))


def test_rejects_extra_meaningful_modifier(listener):
    assert not listener._matches(FakeEvent(CTRL | OPT | CMD | SHIFT, "k"))


def test_rejects_the_wrong_key(listener):
    assert not listener._matches(FakeEvent(CTRL | OPT | CMD, "j"))


def test_survives_an_event_with_no_characters(listener):
    """Dead keys and modifier-only events return empty/None here."""
    assert not listener._matches(FakeEvent(CTRL | OPT | CMD, ""))


def test_describe_renders_the_chord():
    assert describe(DEFAULT_MODIFIERS, DEFAULT_KEY) == "⌃⌥⌘K"
