"""The autonomy phases have to be reachable, or they are five config files.

Every piece built in this project that was "done" and unreachable was found by
asking whether it was *wired*, not whether it *worked*: `browser.py` imported
by nothing, file tools the agent could not call, endpointing fixed in the copy
that does not run. Three in one day. Asked first here.

This is the integration seam: PROPOSE-tier actions must reach the queue instead
of interrupting, approvals must feed the trust ledger, and the ceiling must
survive the whole path.
"""

import pytest

from kavach.autonomy.proposals import ProposalQueue, Status
from kavach.autonomy.tiers import Tier, TierPolicy
from kavach.autonomy.trust import TrustLedger
from kavach.hands.gate import ToolGate
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog

SCRIPT = "mcp__macos-automator__execute_script"
READ = "mcp__kavach-files__read_file"


class Yes:
    def __init__(self):
        self.asked = []

    async def confirm(self, prompt):
        self.asked.append(prompt)
        return True


@pytest.fixture
def ks(tmp_path):
    return KillSwitch(log=ActionLog(tmp_path / "actions.jsonl"))


@pytest.fixture
def stack(tmp_path, ks):
    tiers = TierPolicy(path=tmp_path / "tiers.json")
    queue = ProposalQueue(path=tmp_path / "proposals.json")
    gate = ToolGate(ks, confirmer=Yes(), tiers=tiers, queue=queue,
                    servers={"macos-automator", "kavach-files"})
    return gate, tiers, queue


# ═══ PROPOSE reaches the queue ═══

@pytest.mark.asyncio
async def test_a_propose_tier_action_is_queued_not_run(stack):
    """The point of PROPOSE. It must not execute, and it must not interrupt."""
    gate, tiers, queue = stack
    tiers.set_tier("execute_script", Tier.PROPOSE)

    verdict, reason, _ = await gate._decide(
        SCRIPT, {"script_content": 'tell application "Notes" to delete note 1'})

    assert verdict == "deny", "a proposed action ran instead of queueing"
    assert "queue" in reason.lower()
    assert len(queue.pending()) == 1


@pytest.mark.asyncio
async def test_the_queued_proposal_describes_what_it_would_do(stack):
    """A queue entry the user cannot understand is one they cannot review."""
    gate, tiers, queue = stack
    tiers.set_tier("execute_script", Tier.PROPOSE)

    await gate._decide(
        SCRIPT, {"script_content": 'tell application "Notes" to delete note 1'})

    assert "delete" in queue.pending()[0].description.lower()


@pytest.mark.asyncio
async def test_queueing_does_not_ask_the_user(stack):
    """PROPOSE exists so the user is not interrupted. Queueing AND asking
    would be the worst of both."""
    gate, tiers, queue = stack
    tiers.set_tier("execute_script", Tier.PROPOSE)

    await gate._decide(SCRIPT, {"script_content": 'tell app "Notes" to delete x'})

    assert not gate.confirmer.asked


# ═══ the ceiling survives the whole path ═══

@pytest.mark.asyncio
async def test_a_ceilinged_action_on_auto_still_confirms(stack):
    """Forced past set_tier the way a hand-edited file would. The gate must
    not trust that assignment validated."""
    gate, tiers, queue = stack
    tiers._tiers["execute_script"] = Tier.AUTO

    await gate._decide(
        SCRIPT, {"script_content": 'tell application "Notes" to delete note 1'})

    assert gate.confirmer.asked, "a delete ran unattended via a tier"


@pytest.mark.asyncio
async def test_the_kill_switch_outranks_every_tier(stack):
    gate, tiers, queue = stack
    tiers.set_tier("read_file", Tier.AUTO)
    gate.ks.trigger("test", "latched")

    verdict, reason, _ = await gate._decide(READ, {"path": "/tmp/x"})

    assert verdict == "deny" and "kill switch" in reason.lower()


# ═══ approvals feed the ledger ═══

def test_an_approval_advances_the_streak(tmp_path):
    tiers = TierPolicy(path=tmp_path / "ti.json")
    ledger = TrustLedger(path=tmp_path / "t.json", tiers=tiers)
    queue = ProposalQueue(path=tmp_path / "p.json", trust=ledger)

    item = queue.add("file.read", "read something")
    queue.approve(item.id)

    assert ledger.streak("file.read") == 1


def test_a_rejection_resets_it_through_the_queue(tmp_path):
    tiers = TierPolicy(path=tmp_path / "ti.json")
    ledger = TrustLedger(path=tmp_path / "t.json", tiers=tiers)
    queue = ProposalQueue(path=tmp_path / "p.json", trust=ledger)

    for _ in range(3):
        queue.approve(queue.add("file.read", "x").id)
    queue.reject(queue.add("file.read", "x").id)

    assert ledger.streak("file.read") == 0


def test_an_expiry_teaches_the_ledger_nothing(tmp_path):
    """Nobody looked. That is not a rejection and not an approval, and
    recording it as either would be inventing a decision."""
    tiers = TierPolicy(path=tmp_path / "ti.json")
    ledger = TrustLedger(path=tmp_path / "t.json", tiers=tiers)
    queue = ProposalQueue(path=tmp_path / "p.json", trust=ledger)

    for _ in range(3):
        queue.approve(queue.add("file.read", "x").id)
    item = queue.add("file.read", "x", ttl_seconds=0)
    queue.sweep(now=item.created_at + 1)

    assert ledger.streak("file.read") == 3


# ═══ unconfigured behaves exactly as before ═══

@pytest.mark.asyncio
async def test_without_tiers_or_a_queue_nothing_changes(ks):
    gate = ToolGate(ks, confirmer=Yes(), servers={"macos-automator"})

    await gate._decide(
        SCRIPT, {"script_content": 'tell application "Notes" to delete note 1'})

    assert gate.confirmer.asked
