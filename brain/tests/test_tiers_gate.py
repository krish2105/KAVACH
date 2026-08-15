"""The tier framework has to reach the gate, or it is a config file.

Every piece built today that was "done" and unreachable — `browser.py`
imported by nothing, file tools the agent could not call, endpointing fixed in
the copy that does not run — was found by asking whether the code was wired,
not whether it worked. Asked first this time.
"""

import pytest

from kavach.autonomy.tiers import Tier, TierPolicy
from kavach.hands.gate import ToolGate
from kavach.hands.policy import Verdict
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog

SCRIPT = "mcp__macos-automator__execute_script"
#: A genuinely safe action for the AUTO cases. The first draft of this file
#: used `execute_script`, which runs arbitrary AppleScript — a shell in a
#: costume, and now ceilinged. Using it as the example of a safe action was
#: the mistake the ceiling exists to catch.
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


@pytest.mark.asyncio
async def test_auto_tier_skips_the_confirmation(ks, tmp_path):
    """An action the user deliberately put on AUTO should not keep asking —
    that is what AUTO means."""
    tiers = TierPolicy(path=tmp_path / "a.json")
    tiers.set_tier("read_file", Tier.AUTO)
    gate = ToolGate(ks, confirmer=Yes(), servers={"kavach-files"}, tiers=tiers)

    verdict, _, _ = await gate._decide(READ, {"path": "/tmp/x"})

    assert verdict == "allow"


@pytest.mark.asyncio
async def test_auto_tier_cannot_skip_a_ceilinged_action(ks, tmp_path):
    """The ceiling is the whole point. Even if something contrived its way to
    AUTO, a delete still confirms."""
    tiers = TierPolicy(path=tmp_path / "a.json")
    # Force it past set_tier's guard, the way a hand-edited file or a future
    # bug would. The gate must not rely on assignment having been checked.
    # Forced past set_tier's guard the way a hand-edited file or a future bug
    # would. `execute_script` is itself ceilinged now, so this uses a name
    # that is not — the payload is what must stop it.
    tiers._tiers["execute_script"] = Tier.AUTO

    confirmer = Yes()
    gate = ToolGate(ks, confirmer=confirmer, servers={"macos-automator"},
                    tiers=tiers)

    await gate._decide(
        SCRIPT, {"script_content": 'tell application "Notes" to delete note 1'})

    assert confirmer.asked, "a delete ran unattended because of a tier setting"


@pytest.mark.asyncio
async def test_the_kill_switch_still_outranks_a_tier(ks, tmp_path):
    tiers = TierPolicy(path=tmp_path / "a.json")
    tiers.set_tier("read_file", Tier.AUTO)
    gate = ToolGate(ks, confirmer=Yes(), servers={"kavach-files"}, tiers=tiers)
    ks.trigger("test", "latched")

    verdict, reason, _ = await gate._decide(READ, {"path": "/tmp/x"})

    assert verdict == "deny"
    assert "kill switch" in reason.lower()


@pytest.mark.asyncio
async def test_without_a_tier_policy_nothing_changes(ks):
    """Phase 30 must not alter behaviour for anyone who has not classified
    anything — ALWAYS_ASK is the default and the default is what shipped."""
    confirmer = Yes()
    gate = ToolGate(ks, confirmer=confirmer, servers={"macos-automator"})

    await gate._decide(
        SCRIPT, {"script_content": 'tell application "Notes" to delete note 1'})

    assert confirmer.asked
