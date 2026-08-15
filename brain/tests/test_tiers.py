"""Phase 30 — autonomy tiers, and the ceiling that is not configurable.

Three tiers: AUTO runs with no approval, PROPOSE queues for batch review,
ALWAYS_ASK interrupts immediately (the Phase 4 behaviour, and the default).

**The hard ceiling is the point of this module.** An action that sends,
deletes, purchases or changes a system setting can never be AUTO — not by
config, not by a future phase, not by pattern-learned trust in Phase 34. It is
enforced in code, so there is no file to edit that removes it.

This is the same shape as the rules that have held all evening: the kill switch
latches rather than auto-recovering, `permission_mode` is not a tunable, and
denial is the default at every branch. A ceiling that can be configured away is
a default wearing a costume.
"""

import pytest

from kavach.autonomy.tiers import CEILINGED, Tier, TierPolicy, is_ceilinged


@pytest.fixture
def policy(tmp_path):
    return TierPolicy(path=tmp_path / "autonomy.json")


# ═══ everything starts at ALWAYS_ASK ═══

def test_an_unknown_action_is_always_ask(policy):
    """A new action type nobody has classified is the case most likely to be
    dangerous, because nobody has thought about it yet."""
    assert policy.tier_for("some.brand.new.action") is Tier.ALWAYS_ASK


@pytest.mark.parametrize("action", ["", None, "   "])
def test_a_nameless_action_is_always_ask(policy, action):
    assert policy.tier_for(action) is Tier.ALWAYS_ASK


def test_nothing_is_auto_by_default(policy):
    for action in ("file.read", "browser.navigate", "app.open"):
        assert policy.tier_for(action) is not Tier.AUTO


# ═══ the ceiling ═══

@pytest.mark.parametrize("action", [
    "mail.send", "message.send", "file.delete", "note.delete",
    "purchase.buy", "store.purchase", "system.setting.change",
    "settings.update", "shell.run",
])
def test_ceilinged_actions_can_never_be_auto(policy, action):
    """Not "should not" — cannot. There is no argument, config value or
    approval history that reaches AUTO from here."""
    with pytest.raises(ValueError) as exc:
        policy.set_tier(action, Tier.AUTO)

    assert "never" in str(exc.value).lower()
    assert policy.tier_for(action) is Tier.ALWAYS_ASK


@pytest.mark.parametrize("action", ["mail.send", "file.delete"])
def test_ceilinged_actions_may_be_proposed(policy, action):
    """PROPOSE is the most they can reach: queued, reviewed, never silent."""
    policy.set_tier(action, Tier.PROPOSE)

    assert policy.tier_for(action) is Tier.PROPOSE


def test_the_ceiling_survives_a_hand_edited_config(tmp_path):
    """The file is the obvious way around a code rule, so the file is not
    trusted. An AUTO written there by hand — or by a future phase, or by a
    prompt-injected agent — is read back as ALWAYS_ASK."""
    import json

    path = tmp_path / "autonomy.json"
    path.write_text(json.dumps({"tiers": {"mail.send": "auto"}}))

    assert TierPolicy(path=path).tier_for("mail.send") is Tier.ALWAYS_ASK


def test_is_ceilinged_matches_on_the_verb_not_the_prefix():
    """`delete` is what matters, not which subsystem it came from — a new
    server's `whatever.delete_thing` is ceilinged the day it appears."""
    assert is_ceilinged("anything.delete_thing")
    assert is_ceilinged("brand.new.server.send_message")
    assert not is_ceilinged("file.read")
    assert not is_ceilinged("browser.navigate")


def test_the_ceiling_list_is_not_empty():
    """A guard that guards nothing passes every test it has."""
    assert CEILINGED


# ═══ ordinary assignment ═══

def test_a_safe_action_can_be_made_auto(policy):
    policy.set_tier("file.read", Tier.AUTO)

    assert policy.tier_for("file.read") is Tier.AUTO


def test_assignments_survive_a_reload(tmp_path):
    path = tmp_path / "autonomy.json"
    TierPolicy(path=path).set_tier("file.read", Tier.AUTO)

    assert TierPolicy(path=path).tier_for("file.read") is Tier.AUTO


def test_making_something_more_restrictive_always_works(policy):
    """Phase 34 promises instant demotion with no confirmation. Nothing may
    stand between the user and tightening a rule."""
    policy.set_tier("file.read", Tier.AUTO)
    policy.set_tier("file.read", Tier.ALWAYS_ASK)

    assert policy.tier_for("file.read") is Tier.ALWAYS_ASK


# ═══ §7: every assignment logged ═══

class Log:
    def __init__(self):
        self.entries = []

    def append(self, event, **fields):
        self.entries.append((event, fields))


def test_every_assignment_is_logged(tmp_path):
    log = Log()
    policy = TierPolicy(path=tmp_path / "a.json", log_=log)

    policy.set_tier("file.read", Tier.AUTO)

    assert log.entries
    event, fields = log.entries[0]
    assert event == "autonomy.tier"
    assert fields["action"] == "file.read"
    assert fields["tier"] == "auto"
    assert fields["previous"] == "always_ask"


def test_a_refused_assignment_is_logged_too(tmp_path):
    """A rejected attempt to widen autonomy is exactly the entry worth
    finding later."""
    log = Log()
    policy = TierPolicy(path=tmp_path / "a.json", log_=log)

    with pytest.raises(ValueError):
        policy.set_tier("mail.send", Tier.AUTO)

    assert any(e == "autonomy.refused" for e, _ in log.entries)
