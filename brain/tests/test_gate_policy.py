"""The gate, after the allowlist stopped deciding.

`ToolGate` used to ask two questions: *is this app on the list* and *is this
verb destructive*. The first is gone (spec §9a) and the second stays. What is
tested here is that removing the first did not quietly remove anything else —
the kill switch still outranks everything, denial is still the default when
there is nobody to ask, and every decision still reaches the action log.

The bug that started this: Chrome had been on the allowlist since 2026-08-13
and KAVACH still refused it, from a hardcoded string in `agent.py` that never
consulted the gate at all.
"""

import pytest

from kavach.hands.gate import NEVER_ALLOWED_TOOLS, ToolGate
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog

SERVERS = {"macos-automator", "macos-accessibility", "peekaboo"}
SCRIPT = "mcp__macos-automator__execute_script"
SHELL = "mcp__macos-accessibility__Shell"


class Yes:
    """A confirmer that always agrees, and records what it was asked."""

    def __init__(self):
        self.asked: list[str] = []

    async def confirm(self, prompt: str) -> bool:
        self.asked.append(prompt)
        return True


class No:
    async def confirm(self, prompt: str) -> bool:
        return False


@pytest.fixture
def log(tmp_path):
    return ActionLog(tmp_path / "actions.jsonl")


@pytest.fixture
def ks(log):
    return KillSwitch(log=log)


@pytest.fixture
def gate(ks):
    return ToolGate(ks, confirmer=Yes(), servers=SERVERS)


# ═══ the thing that was broken ═══

@pytest.mark.asyncio
async def test_an_app_that_was_never_on_the_list_is_allowed(gate):
    verdict, _, _ = await gate._decide(
        SCRIPT, {"script_content": 'tell application "Google Chrome" to activate'})
    assert verdict == "allow"


@pytest.mark.asyncio
@pytest.mark.parametrize("app", ["Terminal", "Xcode", "Discord", "Slack"])
async def test_every_installed_app_is_reachable(gate, app):
    verdict, _, _ = await gate._decide(
        SCRIPT, {"script_content": f'tell application "{app}" to activate'})
    assert verdict == "allow", app


# ═══ what must not have moved ═══

@pytest.mark.asyncio
async def test_the_kill_switch_still_outranks_everything(gate, ks):
    """§C: an ambiguous state stays stopped, and the latch does not
    auto-recover. It is checked before the policy, on every path."""
    ks.trigger("test", "gate test")
    for tool, args in ((SHELL, {"command": "ls"}),
                       (SCRIPT, {"script_content": 'tell application "Notes" to activate'})):
        verdict, reason, _ = await gate._decide(tool, args)
        assert verdict == "deny", tool
        assert "kill switch" in reason.lower()


@pytest.mark.asyncio
async def test_shell_asks_before_it_runs(gate):
    confirmer = gate.confirmer
    verdict, _, _ = await gate._decide(SHELL, {"command": "ls"})
    assert verdict == "allow"
    assert confirmer.asked, "a shell command ran without being read back"
    assert "ls" in confirmer.asked[0], "the user was not shown the command"


@pytest.mark.asyncio
async def test_declining_a_shell_command_stops_it(ks):
    gate = ToolGate(ks, confirmer=No(), servers=SERVERS)
    verdict, _, _ = await gate._decide(SHELL, {"command": "rm -rf /"})
    assert verdict == "deny"


@pytest.mark.asyncio
async def test_shell_with_nobody_to_ask_is_denied(ks):
    """Denial is the default at every step. Consent must be given, never
    merely not-withheld — an unattended daemon is not consent."""
    gate = ToolGate(ks, confirmer=None, servers=SERVERS)
    verdict, reason, _ = await gate._decide(SHELL, {"command": "ls"})
    assert verdict == "deny"
    assert "ask" in reason.lower()


@pytest.mark.asyncio
async def test_a_destructive_action_still_confirms(gate):
    verdict, _, _ = await gate._decide(
        SCRIPT, {"script_content": 'tell application "Notes" to delete note 1'})
    assert verdict == "allow"
    assert gate.confirmer.asked, "a delete ran without confirmation"


@pytest.mark.asyncio
async def test_an_unconfigured_server_is_still_refused(gate):
    """The allowlist went; the server check did not. A tool from a server we
    never configured is not something we can reason about."""
    verdict, _, _ = await gate._decide("mcp__unknown-server__do_thing", {})
    assert verdict == "deny"


@pytest.mark.asyncio
async def test_a_non_mcp_tool_name_is_still_refused(gate):
    """Live 2026-08-14: the agent tried plain `Bash` and was refused for
    exactly this reason."""
    verdict, _, _ = await gate._decide("Bash", {"command": "ls"})
    assert verdict == "deny"


@pytest.mark.asyncio
async def test_every_decision_reaches_the_log(gate, ks):
    """§7: every tool call, every argument, timestamped. Including denials —
    a refused action is exactly the one worth being able to look up."""
    await gate.check(SHELL, {"command": "whoami"})
    await gate.check("mcp__unknown__x", {})
    entries = ks.log.read_all()
    decisions = [e for e in entries if e["event"] == "tool.decision"]
    assert len(decisions) == 2
    assert any(d["verdict"] == "deny" for d in decisions)
    assert any("whoami" in str(d.get("args", "")) for d in decisions)


# ═══ the rule that moved ═══

def test_nothing_is_refused_outright_any_more():
    """`Shell` and `agent` were in NEVER_ALLOWED_TOOLS because a shell command
    names no app and so could not be checked against the allowlist. There is
    no allowlist now, and both are confirmed instead — see Policy."""
    assert NEVER_ALLOWED_TOOLS == frozenset()


@pytest.mark.asyncio
async def test_the_sub_agent_is_confirmed_not_refused(gate):
    """Spec §9b — the user accepted that its inner calls are never logged."""
    verdict, _, _ = await gate._decide("mcp__peekaboo__agent", {"task": "tidy up"})
    assert verdict == "allow"
    assert gate.confirmer.asked
