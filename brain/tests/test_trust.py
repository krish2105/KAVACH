"""Phase 34 — trust learned from approvals, and what it can never learn.

After N consistent approvals of one action type, KAVACH **offers** to promote
it to a lower tier. Offers — never does it silently. The user is the one who
decides that they have stopped wanting to be asked.

Three rules, each with a failure it prevents:

* **The Phase 30 ceiling holds regardless of history.** A hundred approved
  deletes still cannot make `file.delete` AUTO. If approval history could
  reach AUTO, the ceiling would be a speed bump — you would only need to
  approve enough times, which is exactly what a user in a hurry does.
* **Demotion is instant and needs no confirmation.** Nothing may stand between
  the user and making a rule *more* restrictive.
* **A rejection resets the streak.** "Yes, yes, yes, no, yes" is not four
  approvals; it is someone who does not consistently want this.

Depends on Phase 30 and Phase 33, both confirmed present before building.
"""

import pytest

from kavach.autonomy.tiers import Tier, TierPolicy
from kavach.autonomy.trust import TrustLedger


@pytest.fixture
def ledger(tmp_path):
    return TrustLedger(path=tmp_path / "trust.json",
                       tiers=TierPolicy(path=tmp_path / "tiers.json"))


# ═══ it offers, it never promotes ═══

def test_a_promotion_is_offered_not_applied(ledger):
    """The user decides they have stopped wanting to be asked. Silently
    lowering a gate because someone was agreeable five times is how a system
    ends up with permissions nobody chose."""
    for _ in range(5):
        ledger.record("file.read", approved=True)

    offer = ledger.offer_for("file.read")

    assert offer is not None
    assert ledger.tiers.tier_for("file.read") is Tier.ALWAYS_ASK


def test_accepting_an_offer_applies_it(ledger):
    for _ in range(5):
        ledger.record("file.read", approved=True)

    ledger.accept("file.read")

    assert ledger.tiers.tier_for("file.read") is Tier.AUTO


def test_nothing_is_offered_before_the_threshold(ledger):
    for _ in range(4):
        ledger.record("file.read", approved=True)

    assert ledger.offer_for("file.read") is None


# ═══ the ceiling, regardless of history ═══

def test_a_hundred_approvals_cannot_reach_auto(ledger):
    """If history could reach AUTO the ceiling would be a speed bump — you
    would only need to approve enough times, which is what someone in a hurry
    does."""
    for _ in range(100):
        ledger.record("file.delete", approved=True)

    offer = ledger.offer_for("file.delete")

    assert offer is None or offer.tier is not Tier.AUTO
    assert ledger.tiers.tier_for("file.delete") is not Tier.AUTO


def test_a_ceilinged_action_may_be_offered_propose(ledger):
    """PROPOSE is the most it can reach, and reaching it is legitimate."""
    for _ in range(5):
        ledger.record("mail.send", approved=True)

    offer = ledger.offer_for("mail.send")

    assert offer is not None
    assert offer.tier is Tier.PROPOSE


def test_accepting_a_ceilinged_offer_still_cannot_produce_auto(ledger):
    for _ in range(50):
        ledger.record("file.delete", approved=True)
    ledger.accept("file.delete")

    assert ledger.tiers.tier_for("file.delete") is Tier.PROPOSE


# ═══ streaks ═══

def test_a_rejection_resets_the_streak(ledger):
    """"Yes, yes, yes, no, yes" is not four approvals. It is someone who does
    not consistently want this."""
    for _ in range(4):
        ledger.record("file.read", approved=True)
    ledger.record("file.read", approved=False)
    ledger.record("file.read", approved=True)

    assert ledger.offer_for("file.read") is None


def test_streaks_are_per_action_type(ledger):
    for _ in range(5):
        ledger.record("file.read", approved=True)
    ledger.record("file.delete", approved=True)

    assert ledger.offer_for("file.read") is not None
    assert ledger.offer_for("file.delete") is None


# ═══ demotion ═══

def test_demotion_is_instant_and_unconditional(ledger):
    """Nothing may stand between the user and a more restrictive rule."""
    for _ in range(5):
        ledger.record("file.read", approved=True)
    ledger.accept("file.read")

    ledger.demote("file.read")

    assert ledger.tiers.tier_for("file.read") is Tier.ALWAYS_ASK


def test_demotion_clears_the_streak_too(ledger):
    """Otherwise the next approval re-offers immediately, and demoting would
    mean nothing."""
    for _ in range(5):
        ledger.record("file.read", approved=True)
    ledger.accept("file.read")
    ledger.demote("file.read")

    assert ledger.offer_for("file.read") is None


def test_demoting_something_never_promoted_is_fine(ledger):
    ledger.demote("never.seen")

    assert ledger.tiers.tier_for("never.seen") is Tier.ALWAYS_ASK


# ═══ §7 ═══

class Log:
    def __init__(self):
        self.entries = []

    def append(self, event, **fields):
        self.entries.append((event, fields))


def test_promotions_and_demotions_are_logged(tmp_path):
    log = Log()
    ledger = TrustLedger(path=tmp_path / "t.json",
                         tiers=TierPolicy(path=tmp_path / "ti.json", log_=log),
                         log_=log)
    for _ in range(5):
        ledger.record("file.read", approved=True)
    ledger.accept("file.read")
    ledger.demote("file.read")

    events = [e for e, _ in log.entries]
    assert "trust.promoted" in events
    assert "trust.demoted" in events


def test_the_ledger_survives_a_reload(tmp_path):
    tiers = tmp_path / "ti.json"
    first = TrustLedger(path=tmp_path / "t.json", tiers=TierPolicy(path=tiers))
    for _ in range(5):
        first.record("file.read", approved=True)

    second = TrustLedger(path=tmp_path / "t.json", tiers=TierPolicy(path=tiers))
    assert second.offer_for("file.read") is not None
