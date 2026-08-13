"""Menu-bar status (Phase 17).

WidgetKit was the phase as written and is impossible here: a Widget Extension
must be archived from Xcode, and this machine has Command Line Tools only. The
stated intent — "KAVACH's status without the orb window open" — is met by the
menu bar instead.

Only the title logic is tested. Setting an NSStatusItem's title needs a status
bar and a main run loop; the precedence rules are the part that can be wrong in
an interesting way, so they live in a pure function.
"""

from kavach.presence.controls import GHOST_TITLE, LATCHED_TITLE, status_title


def snap(**fields) -> dict:
    base = {"state": "idle", "ghost": False, "killSwitch": "armed"}
    base.update(fields)
    return base


def test_idle_shows_the_name_in_plain_text():
    """Letters, not an emoji.

    The 🛡 was created — the app logged it — and could not be found on screen.
    An emoji status item depends on font fallback and on the bar having room,
    and when it loses it renders as nothing: an item that exists, takes space,
    and is invisible. Every title here must contain readable characters.
    """
    assert status_title(snap()) == "KAVACH"


def test_each_state_is_distinguishable():
    titles = {status_title(snap(state=s))
              for s in ("idle", "listening", "thinking", "acting", "speaking")}
    assert len(titles) == 5, "two states look identical in the menu bar"


def test_ghost_says_so_in_words():
    """An emoji alone is too easy to misread at menu-bar size, and this is the
    one thing about KAVACH that must never need interpreting."""
    title = status_title(snap(ghost=True))
    assert title == GHOST_TITLE
    assert "GHOST" in title and "KAVACH" in title


def test_ghost_outranks_the_activity():
    """"Listening" while in ghost mode would be a straightforward lie."""
    assert status_title(snap(state="listening", ghost=True)) == GHOST_TITLE


def test_a_latched_switch_outranks_everything():
    assert status_title(snap(state="acting", killSwitch="disarmed")) == LATCHED_TITLE
    assert status_title(
        snap(state="listening", ghost=True, killSwitch="disarmed")
    ) == LATCHED_TITLE


def test_an_unknown_state_falls_back_rather_than_crashing():
    assert status_title(snap(state="wat")) == "KAVACH"
    assert status_title({}) == "KAVACH"


def test_every_title_is_readable_without_emoji_support():
    """A title that is only symbols can render as an empty gap."""
    from kavach.presence.controls import STATUS_TITLES

    for state, title in STATUS_TITLES.items():
        assert "KAVACH" in title, f"{state} has no readable text"
