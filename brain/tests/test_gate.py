"""Tool gate tests (spec §7, working agreement §C).

This is the file that matters most in the repo. Everything before it was
building toward an agent that can touch the machine; this is what stands
between that agent and your Mail, your Finder, and your system settings.

Written before the implementation. **Every test here encodes a rule from §7.**
If one looks wrong, that is a safety argument to have out loud — not a test to
edit.

The gate is enforced through the Agent SDK's `can_use_tool` callback, which
runs before any MCP tool executes.
"""

import json

import pytest

from kavach.hands.allowlist import Allowlist
from kavach.hands.gate import ToolGate, extract_target_app
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog


class RecordingConfirmer:
    """Stands in for the spoken confirmation flow."""

    def __init__(self, answer: bool):
        self.answer = answer
        self.prompts: list[str] = []

    async def confirm(self, prompt: str) -> bool:
        self.prompts.append(prompt)
        return self.answer


@pytest.fixture
def log(tmp_path):
    return ActionLog(tmp_path / "actions.jsonl")


@pytest.fixture
def ks(log):
    return KillSwitch(log=log)


def make_gate(ks, confirmer=None):
    return ToolGate(kill_switch=ks, allowlist=Allowlist(), confirmer=confirmer)


async def allowed(gate, tool, args) -> bool:
    result = await gate.check(tool, args, context=None)
    return result.behavior == "allow"


# ═══ 1. the kill switch outranks everything ═══

async def test_disarmed_kill_switch_denies_every_tool(ks):
    gate = make_gate(ks, RecordingConfirmer(True))
    ks.trigger(source="test", reason="latched")

    for tool, args in [
        ("mcp__macos-automator__execute_script",
         {"script_content": 'tell application "Safari" to activate'}),
        ("mcp__peekaboo__image", {"app_target": "Safari"}),
        ("mcp__macos-accessibility__Snapshot", {}),
    ]:
        assert not await allowed(gate, tool, args), tool


async def test_kill_switch_is_checked_before_the_allowlist(ks):
    """Ordering is load-bearing: a latched switch must deny even a request
    that would otherwise be perfectly fine."""
    gate = make_gate(ks, RecordingConfirmer(True))
    ks.trigger(source="test")

    result = await gate.check(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Safari" to activate'},
        context=None,
    )
    assert result.behavior == "deny"
    assert "kill switch" in result.message.lower()


# ═══ 2. allowlist, not blocklist ═══

async def test_allowlisted_read_only_action_is_permitted(ks):
    gate = make_gate(ks)
    assert await allowed(
        gate, "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Safari" to return name of front window'},
    )


async def test_unlisted_app_is_denied(ks):
    gate = make_gate(ks, RecordingConfirmer(True))
    assert not await allowed(
        gate, "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Mail" to send outgoing message'},
    )


async def test_unknown_app_is_denied_by_default(ks):
    """The whole point of an allowlist: never-heard-of means no."""
    gate = make_gate(ks, RecordingConfirmer(True))
    for app in ["Terminal", "System Settings", "Messages", "SomeAppNobodyKnows"]:
        assert not await allowed(
            gate, "mcp__macos-automator__execute_script",
            {"script_content": f'tell application "{app}" to activate'},
        ), app


async def test_denial_explains_itself(ks):
    gate = make_gate(ks)
    result = await gate.check(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Mail" to activate'},
        context=None,
    )
    assert result.behavior == "deny"
    assert "Mail" in result.message
    assert "allowlist" in result.message.lower()


# ═══ 3. confirmation for destructive / externally visible (§7) ═══

async def test_destructive_action_requires_confirmation(ks):
    confirmer = RecordingConfirmer(True)
    gate = make_gate(ks, confirmer)

    await gate.check(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Notes" to delete note "Draft"'},
        context=None,
    )
    assert confirmer.prompts, "a delete must be spoken back before it runs"


async def test_declined_confirmation_denies_the_action(ks):
    gate = make_gate(ks, RecordingConfirmer(False))
    assert not await allowed(
        gate, "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Notes" to delete note "Draft"'},
    )


async def test_granted_confirmation_allows_the_action(ks):
    gate = make_gate(ks, RecordingConfirmer(True))
    assert await allowed(
        gate, "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Notes" to delete note "Draft"'},
    )


async def test_missing_confirmer_denies_rather_than_assumes(ks):
    """Fail-safe: with nothing able to ask the user, a destructive action must
    not proceed. Silence is not consent."""
    gate = make_gate(ks, confirmer=None)
    assert not await allowed(
        gate, "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Notes" to delete note "Draft"'},
    )


async def test_read_only_action_is_not_gated_on_confirmation(ks):
    """Confirming everything trains the user to say yes reflexively, which
    destroys the value of asking at all."""
    confirmer = RecordingConfirmer(True)
    gate = make_gate(ks, confirmer)

    await gate.check(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Safari" to return URL of front document'},
        context=None,
    )
    assert not confirmer.prompts


async def test_confirmation_prompt_says_what_will_happen(ks):
    """KAVACH speaks the action back — a prompt that doesn't name the app and
    the tool is not informed consent."""
    confirmer = RecordingConfirmer(True)
    gate = make_gate(ks, confirmer)

    await gate.check(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Notes" to delete note "Draft"'},
        context=None,
    )
    prompt = confirmer.prompts[0]
    assert "Notes" in prompt
    assert "delete" in prompt.lower()


# ═══ 4. unknown servers and malformed names ═══

async def test_tool_from_an_unconfigured_server_is_denied(ks):
    gate = make_gate(ks, RecordingConfirmer(True))
    assert not await allowed(gate, "mcp__some-other-server__run", {"app": "Safari"})


@pytest.mark.parametrize("tool", ["", "Bash", "not_an_mcp_tool", "mcp__broken"])
async def test_malformed_tool_names_are_denied(ks, tool):
    gate = make_gate(ks, RecordingConfirmer(True))
    assert not await allowed(gate, tool, {})


# ═══ 5. the action log (§7: every tool call, every argument) ═══

async def test_every_decision_is_logged(ks, log):
    gate = make_gate(ks, RecordingConfirmer(True))

    await gate.check("mcp__macos-automator__execute_script",
                     {"script_content": 'tell application "Safari" to activate'},
                     context=None)
    await gate.check("mcp__macos-automator__execute_script",
                     {"script_content": 'tell application "Mail" to activate'},
                     context=None)

    entries = [r for r in log.read_all() if r["event"] == "tool.decision"]
    assert len(entries) == 2
    assert {e["verdict"] for e in entries} == {"allow", "deny"}


async def test_log_records_the_arguments_not_just_the_tool(ks, log):
    """§7 says 'every tool call, every argument'. When something goes wrong
    you need to see what it actually tried to do."""
    gate = make_gate(ks, RecordingConfirmer(True))
    await gate.check("mcp__macos-automator__execute_script",
                     {"script_content": 'tell application "Safari" to activate'},
                     context=None)

    entry = [r for r in log.read_all() if r["event"] == "tool.decision"][0]
    assert "Safari" in json.dumps(entry["args"])
    assert entry["tool"] == "mcp__macos-automator__execute_script"


# ═══ 6. working out which app is being targeted ═══

@pytest.mark.parametrize("args,expected", [
    ({"script_content": 'tell application "Safari" to activate'}, "Safari"),
    ({"script_content": 'tell application "Notes"\n  delete note 1\nend tell'}, "Notes"),
    ({"app_target": "Finder"}, "Finder"),
    ({"app": "Calendar"}, "Calendar"),
    ({"name": "Safari"}, "Safari"),
])
def test_target_app_is_extracted(args, expected):
    assert extract_target_app("mcp__macos-automator__execute_script", args) == expected


def test_unidentifiable_target_returns_none():
    assert extract_target_app("mcp__peekaboo__see", {"question": "what is on screen"}) is None


async def test_action_with_no_identifiable_app_is_denied(ks):
    """If we cannot tell what it would touch, we cannot check it against the
    allowlist — so it does not run."""
    gate = make_gate(ks, RecordingConfirmer(True))
    assert not await allowed(
        gate, "mcp__macos-automator__execute_script",
        {"script_content": 'do shell script "rm -rf ~/Documents"'},
    )


# ═══ 7. the agent must not get hands without a gate ═══

def test_agent_without_a_gate_gets_no_tools():
    """An ungated agent with MCP servers attached is precisely what §7 exists
    to prevent, so it gets none rather than unrestricted ones."""
    from kavach.reasoning.agent import ClaudeAgent

    options = ClaudeAgent(gate=None).options()
    assert options.mcp_servers == {}
    assert options.allowed_tools == []
    assert options.can_use_tool is None


def test_agent_with_a_gate_routes_every_tool_through_it(ks):
    from kavach.reasoning.agent import ClaudeAgent

    gate = make_gate(ks)
    options = ClaudeAgent(gate=gate).options()

    assert options.can_use_tool is not None, "tools must not bypass the gate"
    assert options.mcp_servers, "gated agent should have the configured servers"


def test_agent_permission_mode_is_never_auto_approving():
    from kavach.reasoning.agent import PERMISSION_MODE, ClaudeAgent

    forbidden = {"acceptedits", "bypasspermissions", "auto", "yolo"}
    assert PERMISSION_MODE.lower() not in forbidden
    assert ClaudeAgent().options().permission_mode == PERMISSION_MODE


def test_configured_servers_match_the_mcp_config():
    """The gate trusts hands/mcp.config.json; the agent must load the same
    file, or the gate would be validating against a different list."""
    from kavach.hands.gate import load_configured_servers
    from kavach.reasoning.agent import load_mcp_servers

    assert set(load_mcp_servers()) == load_configured_servers()


def test_every_tool_call_reaches_the_gate_via_a_hook(ks):
    """The invariant that matters: the gate must run for EVERY tool call.

    This replaces an earlier pair of tests that asserted allowed_tools == []
    and that no CanUseToolShadowedWarning was raised. Both encoded a premise
    that live testing disproved: `can_use_tool` is never actually reached —
    with a wildcard in allowed_tools the SDK auto-approves first, and with
    allowed_tools empty the CLI's own interactive prompt intercepts and dies
    with "AbortError: Stream closed" in a headless voice loop.

    A PreToolUse hook fires in both cases. Verified live: a Mail AppleScript
    was denied by this hook while the tool was listed in allowed_tools.
    """
    from kavach.reasoning.agent import ClaudeAgent

    gate = make_gate(ks)
    options = ClaudeAgent(gate=gate).options()

    hooks = options.hooks.get("PreToolUse") or []
    assert hooks, "no PreToolUse hook — nothing would gate tool calls"

    callbacks = [cb for matcher in hooks for cb in matcher.hooks]
    assert gate.hook in callbacks, "the hook must be THIS gate's"
    # matcher=None means every tool, not a subset.
    assert any(m.matcher is None for m in hooks), "hook must match all tools"


def test_bypass_tools_are_not_even_offered_to_the_model(ks):
    """Defence in depth: the gate denies them, and they are also excluded
    from what the model can see."""
    from kavach.hands.gate import NEVER_ALLOWED_TOOLS
    from kavach.reasoning.agent import ClaudeAgent

    exposed = ClaudeAgent(gate=make_gate(ks)).options().allowed_tools
    for banned in NEVER_ALLOWED_TOOLS:
        assert not any(t.endswith(f"__{banned}") for t in exposed), banned


# ═══ 8. tools that defeat the allowlist entirely ═══

@pytest.mark.parametrize("tool,server", [
    ("mcp__macos-accessibility__Shell", "macos-accessibility"),
    ("mcp__peekaboo__agent", "peekaboo"),
    ("mcp__macos-accessibility__Desktop", "macos-accessibility"),
])
async def test_bypass_tools_are_never_permitted(ks, tool, server):
    """`Shell` runs arbitrary commands: there is no "app" for the allowlist to
    check, so it is a complete bypass of §7 rather than an edge case of it.
    Denied even for an allowlisted app and even with a confirmer present."""
    gate = make_gate(ks, RecordingConfirmer(True))
    assert not await allowed(gate, tool, {"app": "Safari", "command": "ls"})


async def test_bypass_tools_are_denied_before_confirmation_is_asked(ks):
    """It must not be possible to talk the user into one."""
    confirmer = RecordingConfirmer(True)
    gate = make_gate(ks, confirmer)
    await gate.check("mcp__macos-accessibility__Shell",
                     {"app": "Safari", "command": "rm -rf ~"}, context=None)
    assert not confirmer.prompts


# ═══ 9. schema discovery must work, without opening a hole ═══

async def test_tool_schema_discovery_is_permitted(ks):
    """The CLI defers MCP schemas and loads them via ToolSearch. Denying it
    meant the MCP tools never loaded and the agent could not act at all —
    a gate so strict it broke the thing it was gating."""
    gate = make_gate(ks)
    assert await allowed(gate, "ToolSearch", {"query": "macos automator"})


@pytest.mark.parametrize("tool", [
    "Bash", "Read", "Write", "Edit", "WebFetch", "Task", "NotebookEdit",
])
async def test_other_builtin_tools_stay_denied(ks, tool):
    """Bash in particular: the agent has been observed reaching for it as a
    fallback when the MCP path failed. That is the route around the allowlist
    §7 exists to close."""
    gate = make_gate(ks, RecordingConfirmer(True))
    assert not await allowed(gate, tool, {"command": "whoami"})


async def test_discovery_is_still_blocked_by_the_kill_switch(ks):
    """Even a harmless tool does not run while latched — the switch outranks
    every other rule, including this exemption."""
    gate = make_gate(ks)
    ks.trigger(source="test")
    assert not await allowed(gate, "ToolSearch", {"query": "anything"})


# ═══ 10. whole-screen capture — the honest exception ═══

async def test_whole_screen_capture_asks_before_capturing(ks):
    """A full-screen grab has no single target app BY DESIGN, so the usual
    "unidentifiable target" denial would kill the feature. But it will capture
    apps deliberately kept off the allowlist — mail, password managers — so it
    is asked about rather than silently allowed."""
    confirmer = RecordingConfirmer(True)
    gate = make_gate(ks, confirmer)

    result = await gate.check("mcp__peekaboo__image",
                              {"app_target": "screen:0"}, context=None)
    assert result.behavior == "allow"
    assert confirmer.prompts
    assert "not on the allowlist" in confirmer.prompts[0]


async def test_declining_a_whole_screen_capture_denies_it(ks):
    gate = make_gate(ks, RecordingConfirmer(False))
    assert not await allowed(gate, "mcp__peekaboo__image", {"app_target": "screen:0"})


async def test_whole_screen_capture_without_a_confirmer_is_denied(ks):
    gate = make_gate(ks, confirmer=None)
    assert not await allowed(gate, "mcp__peekaboo__image", {"app_target": "screen:0"})


async def test_capturing_an_allowlisted_app_window_needs_no_confirmation(ks):
    """Scoped to Safari, it is an ordinary read of an approved app."""
    confirmer = RecordingConfirmer(True)
    gate = make_gate(ks, confirmer)
    assert await allowed(gate, "mcp__peekaboo__image", {"app_target": "Safari"})
    assert not confirmer.prompts


async def test_capturing_an_unlisted_app_window_is_still_denied(ks):
    """The exception is for the *whole screen*, not a licence to name any app."""
    gate = make_gate(ks, RecordingConfirmer(True))
    assert not await allowed(gate, "mcp__peekaboo__image", {"app_target": "Mail"})


async def test_non_capture_tools_get_no_whole_screen_exception(ks):
    """A click with no target must not sneak through as a "capture"."""
    gate = make_gate(ks, RecordingConfirmer(True))
    assert not await allowed(gate, "mcp__peekaboo__click", {})
