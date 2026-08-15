"""Resolve a spoken app name to the spelling macOS actually uses.

**This is the injection defence.** The allowlist used to supply it: the string
that reached ``tell application "…"`` was `allowlist.json`'s spelling, never
the transcript, so `open notes` ran ``tell application "Notes"`` and a hostile
transcript matched no entry and reached nothing.

Removing the allowlist (spec §9a) would have deleted that guarantee. Launch
Services replaces it and is strictly stronger:

* it canonicalises **every installed app**, not seven, and
* it returns None for anything not installed, so a mis-transcription resolves
  to nothing rather than to a script.

The guarantee is unchanged in kind: *the transcript never reaches AppleScript*.
What reaches it is Launch Services' own spelling of a real app.

Note the charset below excludes ``"``, ``\\`` and ``;`` — the characters that
could end an AppleScript string literal. That is deliberate and it is the
whole trick: a name containing them is **not an app name**, rather than a name
that needs escaping before it is run. Escaping is a rule you can get wrong;
this is a rule you cannot.
"""

from __future__ import annotations

import functools
import logging
import os
import re

log = logging.getLogger("kavach.hands.appinfo")

#: What macOS would accept as an application name. Bounded, because a
#: transcript is not — whisper will happily return a paragraph.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9 .&'\-]{1,64}$")

#: Nobody says "Google Chrome" out loud; they say "Chrome". Launch Services
#: resolves the full name only, so these are tried before giving up.
_VENDOR_PREFIXES = ("Google ", "Microsoft ", "Adobe ", "Apple ")


def _workspace():
    from AppKit import NSWorkspace

    return NSWorkspace.sharedWorkspace()


def _lookup(name: str) -> str | None:
    """Launch Services' own spelling for `name`, or None."""
    try:
        path = _workspace().fullPathForApplication_(name)
    except Exception:
        log.debug("lookup failed for %r", name, exc_info=True)
        return None
    if not path:
        return None
    base = os.path.basename(str(path))
    return base[:-4] if base.endswith(".app") else base


@functools.lru_cache(maxsize=256)
def canonical_name(spoken: str | None) -> str | None:
    """The real name of an installed app, or None if there isn't one.

    None is the answer for every failure — not installed, not a name, empty,
    hostile. Callers treat None as "do not build a script", so the failure
    modes collapse into the safe one.
    """
    if not spoken:
        return None
    name = spoken.strip()
    if not _SAFE_NAME.match(name):
        # Too long, or contains a character that is not part of any app name.
        return None

    found = _lookup(name)
    if found:
        return found
    for prefix in _VENDOR_PREFIXES:
        found = _lookup(prefix + name)
        if found:
            return found
    return None


@functools.lru_cache(maxsize=256)
def bundle_id(name: str | None) -> str | None:
    """The bundle identifier for an app, or None.

    Routes through :func:`canonical_name` rather than repeating its checks —
    two copies of one rule is how one of them goes stale, which is the exact
    bug this whole change exists to fix.
    """
    real = canonical_name(name)
    if real is None:
        return None
    try:
        from AppKit import NSBundle

        path = _workspace().fullPathForApplication_(real)
        bundle = NSBundle.bundleWithPath_(path) if path else None
        return bundle.bundleIdentifier() if bundle else None
    except Exception:
        log.debug("bundle id lookup failed for %r", real, exc_info=True)
        return None


def is_installed(name: str | None) -> bool:
    """Whether an app exists on this machine, by its spoken name."""
    return canonical_name(name) is not None
