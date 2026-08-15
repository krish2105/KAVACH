"""Phase 34's offers, where you will actually see them.

The spec says a promotion is "always something you ask me about, never
something that happens silently". It is not silent — it is in `kavach-autonomy
status`. But an offer you only see if you happen to type a command is one you
will never see, which is silence with extra steps.

So offers ride the snapshot beside the proposals. **Read-only, like the
proposals panel**: accepting from the orb would mean a click widens a
permission, and a click has no speaker verification behind it.
"""

import pytest

from kavach.autonomy.tiers import Tier, TierPolicy
from kavach.autonomy.trust import TrustLedger
from kavach.voice.loop import VoiceState


@pytest.fixture
def ledger(tmp_path):
    return TrustLedger(path=tmp_path / "t.json",
                       tiers=TierPolicy(path=tmp_path / "ti.json"))


def test_the_snapshot_carries_offers():
    assert "trustOffers" in VoiceState().as_dict()


def test_no_offers_is_an_empty_list_not_null():
    assert VoiceState().as_dict()["trustOffers"] == []


def test_an_earned_offer_appears(ledger):
    for _ in range(5):
        ledger.record("read_file", approved=True)

    offers = [o.as_dict() for o in ledger.pending_offers()]

    assert offers and offers[0]["action"] == "read_file"
    assert offers[0]["tier"] == "auto"


def test_a_ceilinged_offer_never_says_auto(ledger):
    """The one thing that must never appear on screen is KAVACH suggesting
    it stop asking about deletes."""
    for _ in range(50):
        ledger.record("file.delete", approved=True)

    offers = [o.as_dict() for o in ledger.pending_offers()]

    assert offers and offers[0]["tier"] == "propose"
    assert all(o["tier"] != "auto" for o in offers)


def test_nothing_is_offered_below_the_streak(ledger):
    for _ in range(4):
        ledger.record("read_file", approved=True)

    assert ledger.pending_offers() == []


def test_offers_say_how_many_approvals_earned_them(ledger):
    """"KAVACH wants to stop asking" is a claim. "You approved this 5 times"
    is the evidence for it, and the user should see the evidence."""
    for _ in range(6):
        ledger.record("read_file", approved=True)

    assert ledger.pending_offers()[0].as_dict()["streak"] == 6
