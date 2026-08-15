"""The tool gate — every MCP call passes through here (spec §7, §C).

Wired into the Claude Agent SDK as `can_use_tool`, which the SDK invokes
*before* a tool executes. Nothing reaches an MCP server without a verdict from
this module.

Checks run cheapest-and-most-absolute first, and each one can only ever deny:

1. **Kill switch.** Latched means nothing runs, full stop. Checked before
   anything else so a halt cannot be out-raced by an otherwise-valid request.
2. **Known server.** The tool must belong to a server in
   ``hands/mcp.config.json``.
3. **Identifiable target.** Extracted for the log and the spoken prompt, but
   no longer a gate. It used to be fatal — an action whose app could not be
   identified was refused, because it could not be checked against the
   allowlist. There is no allowlist now.
4. **Policy** (`hands/policy.py`). Every installed app is allowed; the
   question is which *verb*. Shell — including a shell reached through
   AppleScript's ``do shell script`` — always confirms.
5. **Confirmation.** Destructive or externally visible actions are read back
   and wait. With no confirmer wired, they are denied: silence is not consent.

**This list is prose and prose goes stale.** It said "Safari, Notes, Calendar,
Finder" for two days after Chrome was permitted, which is the same drift that
made `agent.py` refuse an app it was allowed to drive. `policy.py` is the
authority; if this paragraph disagrees with it, this paragraph is wrong.

Every decision is written to the JSONL action log with its arguments, because
§7 asks for "every tool call, every argument" and because when this goes wrong
you need to see what it *tried* to do, not what it was allowed to do.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Protocol

from ..killswitch.core import KillSwitch
from .allowlist import Allowlist
from .policy import Policy, Verdict, reaches_shell

log = logging.getLogger("kavach.hands.gate")

MCP_CONFIG = Path(__file__).resolve().parents[3] / "hands" / "mcp.config.json"

#: mcp__<server>__<tool>
_TOOL_NAME_RE = re.compile(r"^mcp__(?P<server>[a-z0-9_-]+)__(?P<tool>[A-Za-z0-9_-]+)$")

#: AppleScript/JXA: tell application "Safari"
_TELL_APP_RE = re.compile(r'tell\s+application\s+"([^"]+)"', re.I)

#: Empty by decision (spec §9), and kept as a name so the reason survives.
#:
#: `Shell` and `agent` were refused outright because a shell command names no
#: app, so the allowlist had nothing to check and the tool was a complete
#: bypass of §7 rather than an edge case of it. **There is no app allowlist
#: now.** Both are handled by `Policy.ALWAYS_CONFIRM_TOOLS` instead: every
#: invocation is read back to the user before it runs, which is a stronger
#: check than the allowlist ever applied to them and a weaker one than
#: refusal. `Desktop` (virtual desktops) is simply allowed.
#:
#: Left as an empty frozenset rather than deleted because `_decide` still
#: consults it: a future tool that genuinely cannot be gated belongs here,
#: and the branch should already exist when that happens.
NEVER_ALLOWED_TOOLS = frozenset()

#: Non-MCP tools that are permitted because they cannot touch the machine.
#:
#: The CLI defers MCP tool schemas and loads them on demand via `ToolSearch`.
#: Denying it — which the original name check did, since it isn't an
#: `mcp__server__tool` — meant the MCP tools were never loaded at all and the
#: agent could not act on anything, gate or no gate.
#:
#: Deliberately a fixed set of one. `Bash`, `Read`, `Write` and friends stay
#: denied: those are exactly the routes around the allowlist that §7 cares
#: about, and the agent has already been observed reaching for `Bash` as a
#: fallback when the MCP path failed.
SAFE_META_TOOLS = frozenset({"ToolSearch"})

#: Built-ins the model reaches for by habit when it wants the filesystem.
#:
#: All still denied — each bypasses `FileTools`' kill switch, its confirmation
#: and the §7 log — but denied with a pointer to the tools that do the same job
#: through the gate. Listed rather than pattern-matched so adding one is a
#: deliberate act.
_BUILTIN_FILE_TOOLS = frozenset({
    "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep", "LS",
})

#: Which physical device each MCP server reaches. A server that is not listed
#: here is denied: unmapped means ungovernable.
SERVER_DEVICES = {
    # In-process, but mapped like any other: `device_for_server` returns None
    # for anything unlisted and the gate denies that outright. A new server
    # that forgets this line is refused, not waved through.
    "kavach-files": "mac",
    "kavach-browser": "mac",
    "macos-automator": "mac",
    "macos-accessibility": "mac",
    "peekaboo": "mac",
    "mirroir": "iphone",
}


def device_for_server(server: str) -> str | None:
    return SERVER_DEVICES.get(server)

#: Argument keys that name an app across the three servers.
_APP_ARG_KEYS = ("app_target", "app", "application", "appName", "bundle_id", "name")


class Confirmer(Protocol):
    """Asks the user, out loud, and waits."""

    async def confirm(self, prompt: str) -> bool: ...


def load_configured_servers(path: Path = MCP_CONFIG) -> set[str]:
    """Every server whose tools this gate will consider.

    `kavach-files` runs in-process and has no command to launch, so it is not
    in `hands/mcp.config.json` and this used to omit it — the gate then
    refused its tools as coming from an unconfigured server. It is configured;
    it is just configured in Python.
    """
    from .browser_server import BROWSER_SERVER_NAME
    from .file_server import FILE_SERVER_NAME

    try:
        configured = set(json.loads(path.read_text())["mcpServers"])
    except Exception:
        log.warning("could not read %s; no subprocess MCP server is trusted",
                    path)
        configured = set()
    return configured | {FILE_SERVER_NAME, BROWSER_SERVER_NAME}


#: Peekaboo capture tools, and the pseudo-targets that mean "the whole
#: screen" rather than a specific app.
_CAPTURE_TOOLS = frozenset({"image", "capture", "see", "screenshot"})
_WHOLE_SCREEN_TARGETS = ("screen:", "screen", "display:", "frontmost")


def is_whole_screen_capture(tool: str, args: dict[str, Any]) -> bool:
    """True for a capture of the display rather than a named app window."""
    if tool not in _CAPTURE_TOOLS:
        return False
    target = str(args.get("app_target") or args.get("target") or "").strip().lower()
    if not target:
        return True  # no target at all means the whole screen
    return any(target.startswith(prefix) for prefix in _WHOLE_SCREEN_TARGETS)


def extract_target_app(tool: str, args: dict[str, Any]) -> str | None:
    """Work out which app a call would touch, or None if it can't be told.

    None is a *denial* upstream, not a shrug: an action whose target cannot be
    identified cannot be checked against the allowlist.
    """
    for key in _APP_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            # Peekaboo accepts "screen:0" and similar pseudo-targets.
            if ":" in value and not value.startswith("com."):
                continue
            return value.strip()

    for value in args.values():
        if isinstance(value, str):
            match = _TELL_APP_RE.search(value)
            if match:
                return match.group(1).strip()

    return None


class ToolGate:
    def __init__(
        self,
        kill_switch: KillSwitch,
        allowlist: Allowlist | None = None,
        confirmer: Confirmer | None = None,
        servers: set[str] | None = None,
        tiers=None,
        queue=None,
    ):
        self.ks = kill_switch
        #: Retained for `confirm_always` and the iPhone's per-tool policy.
        #: Its mac `allowed` array no longer gates anything (spec §9a).
        self.allowlist = allowlist or Allowlist()
        self.confirmer = confirmer
        self.servers = servers if servers is not None else load_configured_servers()
        #: What may run. The app question became a verb question.
        self.policy = Policy(confirm_tokens=frozenset(self.allowlist.confirm_always))
        #: Phase 30. None means every action is ALWAYS_ASK, which is what
        #: shipped before tiers existed — so an unconfigured system behaves
        #: exactly as it did.
        self.tiers = tiers
        #: Phase 33. Where PROPOSE-tier actions go instead of interrupting.
        #: Without it, PROPOSE degrades to ALWAYS_ASK rather than to AUTO — a
        #: missing queue must never make something quieter.
        self.queue = queue

    # ——— the Agent SDK entry point ———

    async def check(self, tool: str, args: dict[str, Any], context: Any = None):
        """`can_use_tool`. Returns PermissionResultAllow / PermissionResultDeny."""
        verdict, message, extra = await self._decide(tool, args)

        self.ks.log.append(
            "tool.decision",
            tool=tool,
            args=args,          # §7: every argument, not just the tool name
            verdict=verdict,
            reason=message,
            **extra,
        )
        log.info("tool %s → %s (%s)", tool, verdict, message)

        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

        if verdict == "allow":
            return PermissionResultAllow()
        return PermissionResultDeny(message=message)

    async def hook(self, payload: dict, tool_use_id: str | None, context: Any = None):
        """`PreToolUse` hook — the enforcement point that actually fires.

        `can_use_tool` turned out not to be reachable in this configuration:
        with a wildcard in `allowed_tools` the SDK auto-approves before the
        callback (CanUseToolShadowedWarning), and with `allowed_tools` empty
        the CLI's own interactive prompt intercepts first — which in a
        headless voice loop fails with "AbortError: Stream closed" rather than
        consulting us.

        A PreToolUse hook runs for every call in both cases, which is what §7
        requires. Same decision logic as :meth:`check`; only the wire format
        differs.
        """
        tool = payload.get("tool_name", "")
        args = payload.get("tool_input", {}) or {}

        verdict, message, extra = await self._decide(tool, args)

        self.ks.log.append(
            "tool.decision",
            tool=tool,
            args=args,
            verdict=verdict,
            reason=message,
            via="PreToolUse",
            **extra,
        )
        log.info("tool %s → %s (%s)", tool, verdict, message)

        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if verdict == "allow" else "deny",
                "permissionDecisionReason": message,
            }
        }

    async def _decide(self, tool: str, args: dict[str, Any]) -> tuple[str, str, dict]:
        # 1 — the kill switch outranks everything.
        if not self.ks.is_armed:
            return ("deny", "Kill switch is latched; no action may run.", {})

        # 2 — schema discovery is allowed; it cannot reach the machine.
        if tool in SAFE_META_TOOLS:
            return ("allow", f"{tool} loads tool schemas only", {"meta": True})

        # 3 — must be a tool on a server we configured.
        match = _TOOL_NAME_RE.match(tool or "")
        if not match:
            # A denial the model cannot recover from is a dead end wearing a
            # reason. Measured live, twice: asked to read a file, the agent
            # reached for the built-in `Read`, was told only that it "is not a
            # recognised MCP tool name", and gave up — asking the user to
            # repeat themselves without ever trying the file tools it had.
            #
            # The refusal is right: a built-in filesystem tool bypasses
            # FileTools' kill switch, its confirmation and the §7 log. What
            # was missing is the second half of the sentence.
            hint = ""
            if (tool or "") in _BUILTIN_FILE_TOOLS:
                hint = (" Use the kavach-files tools instead — they are gated "
                        "and logged; find them with ToolSearch.")
            return ("deny", f"{tool!r} is not a recognised MCP tool name.{hint}", {})

        server = match.group("server")
        if server not in self.servers:
            return (
                "deny",
                f"{server!r} is not a configured MCP server.",
                {"server": server},
            )

        # 2b — some tools escape the whole model. `macos-accessibility` ships a
        # `Shell` tool (arbitrary commands + AppleScript) and Peekaboo ships an
        # `agent` tool that drives its own sub-agent. Either would sidestep the
        # app allowlist entirely: a shell command targets no "app" this gate
        # can check. Refused outright rather than gated.
        if match.group("tool") in NEVER_ALLOWED_TOOLS:
            return (
                "deny",
                f"{match.group('tool')!r} bypasses the app allowlist "
                f"(arbitrary execution) and is never permitted.",
                {"server": server, "blocked_tool": match.group("tool")},
            )

        # 2c — device-scoped servers take a different path.
        #
        # mirroir-mcp's tools are not app-scoped: screenshot, describe_screen
        # and start_recording all act on whatever is on the iPhone screen and
        # none of them name an app. So the iPhone is governed per-TOOL rather
        # than per-app, and a per-app iPhone allowlist would be theatre.
        device = device_for_server(server)
        if device is None:
            return ("deny", f"{server!r} is not mapped to a known device.",
                    {"server": server})

        if device != "mac":
            return await self._decide_device_scoped(
                device, server, match.group("tool"), args
            )

        # 3 — what does it touch? Still extracted, but no longer a gate.
        #
        # This used to be fatal: an action whose target app could not be
        # identified was refused, because an unidentifiable app cannot be
        # checked against the allowlist. There is no allowlist now, so an
        # unknown app is no longer a reason to refuse — the verb is what
        # decides. The name is still pulled out because it makes the log and
        # the spoken confirmation legible.
        app = extract_target_app(tool, args)

        # The whole-screen capture is the one case where a missing app still
        # matters, and it is not about permission — it is that the capture
        # will include whatever is on screen, which may be a password manager
        # or a private message. That deserves asking about regardless of how
        # wide the app policy is.
        if app is None:
            # A whole-screen capture is the one honest exception. It has no
            # single target app *by design* — and that is exactly why it needs
            # asking about rather than either silently allowing or blanket
            # refusing: it will capture whatever is on screen, including apps
            # that are deliberately NOT on the allowlist (mail, password
            # managers, private messages).
            if is_whole_screen_capture(match.group("tool"), args):
                if self.confirmer is None:
                    return (
                        "deny",
                        "A whole-screen capture would include apps that are not "
                        "on the allowlist, and there is no way to ask you. "
                        "Refusing.",
                        {"server": server, "whole_screen": True},
                    )
                granted = await self.confirmer.confirm(
                    "This captures your entire screen, including anything from "
                    "apps that are not on the allowlist. Should I?"
                )
                if not granted:
                    return ("deny", "You declined.",
                            {"server": server, "whole_screen": True})
                return ("allow", "whole-screen capture confirmed by user",
                        {"server": server, "whole_screen": True,
                         "confirmed": True})

        # 4 — the policy decides: which verb, not which app.
        verdict, reason = self.policy.decide(tool, args)
        extra = {"server": server, "app": app, "policy": reason}

        if verdict is Verdict.ALLOW:
            return ("allow", reason, extra)

        if verdict is Verdict.DENY:
            return ("deny", reason, extra)

        # 4b — Phase 30: an action the user deliberately put on AUTO does
        # not keep asking. The ceiling is re-checked HERE rather than trusted
        # from assignment: the config file is the obvious way around a code
        # rule, and a gate that relies on someone else having validated is a
        # gate with a second source of truth.
        if verdict is Verdict.CONFIRM and self.tiers is not None:
            from ..autonomy.tiers import Tier, is_ceilinged

            bare = self.policy.bare_name(tool)
            payload = self.policy.action_text(tool, args)
            # The ceiling is checked against the ARGUMENTS as well as the
            # name. `execute_script` is not itself a ceilinged word, and its
            # payload can be `delete note 1` — so checking the name alone
            # would let an AUTO tier wave a delete through. Exactly the hole
            # `Policy.action_text` had when it read the name only as a
            # fallback, one layer up.
            if (self.tiers.tier_for(bare) is Tier.AUTO
                    and not is_ceilinged(bare)
                    and not is_ceilinged(payload)
                    and not reaches_shell(tool, payload)):
                return ("allow", f"{reason} — but {bare} is on the AUTO tier",
                        {**extra, "tier": "auto"})

        # 4c — Phase 33: PROPOSE goes to the queue instead of interrupting.
        # Denied HERE and now: queueing is a promise to ask later, not a
        # licence to run. Nothing in the queue executes until the user acts.
        if (verdict is Verdict.CONFIRM and self.tiers is not None
                and self.queue is not None):
            from ..autonomy.tiers import Tier

            bare = self.policy.bare_name(tool)
            if self.tiers.tier_for(bare) is Tier.PROPOSE:
                what = self._describe(app, match.group("tool"),
                                      self.policy.action_text(tool, args))
                item = self.queue.add(bare, what)
                return ("deny",
                        f"queued for your review rather than run now: {what}",
                        {**extra, "tier": "propose", "proposal": item.id})

        # 5 — CONFIRM: read it back and wait.
        action_text = self.policy.action_text(tool, args)
        what = self._describe(app, match.group("tool"), action_text)

        if self.confirmer is None:
            # Denial is the default. An unattended daemon is not consent, and
            # "nobody objected" is not the same as "someone agreed".
            return (
                "deny",
                f"This would {what}, and there is no way to ask you right "
                f"now. Refusing.",
                {**extra, "needed_confirmation": True},
            )

        granted = await self.confirmer.confirm(f"This will {what}. Should I?")
        if not granted:
            return ("deny", "You declined.",
                    {**extra, "needed_confirmation": True})
        return ("allow", f"confirmed by user — {reason}",
                {**extra, "needed_confirmation": True, "confirmed": True})

    async def _decide_device_scoped(
        self, device: str, server: str, tool: str, args: dict
    ) -> tuple[str, str, dict]:
        """Gate a device whose tools are not app-scoped (the iPhone).

        The unit of permission is the tool: reading the screen is allowed,
        recording it is not without asking.
        """
        extra = {"server": server, "device": device, "tool_name": tool}

        if not self.allowlist.device_enabled(device):
            return ("deny", f"the {device} is not enabled in the allowlist.", extra)

        policy = self.allowlist.device_tool_policy(device, tool)

        if policy == "deny":
            return (
                "deny",
                f"{tool!r} is not an approved {device} tool. Approved tools are "
                f"listed per device in hands/allowlist.json.",
                extra,
            )

        if policy == "confirm":
            if self.confirmer is None:
                return ("deny",
                        f"{tool!r} on the {device} needs confirmation and there "
                        f"is no way to ask you. Refusing.",
                        {**extra, "destructive": True})
            granted = await self.confirmer.confirm(
                f"This will {tool.replace('_', ' ')} on your {device}. Should I?"
            )
            if not granted:
                return ("deny", "You declined.", {**extra, "destructive": True})
            return ("allow", "confirmed by user",
                    {**extra, "destructive": True, "confirmed": True})

        return ("allow", f"approved read-only {device} tool", extra)

    @staticmethod
    def _describe(app: str | None, tool: str, action_text: str) -> str:
        """A short spoken paraphrase. Names what will happen — a prompt that
        says only "allow tool?" is not informed consent.

        A shell command is quoted **verbatim**, never paraphrased. Everything
        else here is a summary, but approving `rm -rf ~` because the prompt
        said "act on something" is not consent, it is a trap. If it is too
        long to say, it is truncated at the end rather than the start: the
        command name is the part that matters most.
        """
        if reaches_shell(tool, action_text):
            command = (action_text or "").strip()
            if len(command) > 160:
                command = command[:157] + "..."
            return f"run the shell command: {command}"

        verb = "act on"
        for candidate in ("delete", "send", "remove", "trash", "buy", "purchase",
                          "submit", "post", "share", "install", "restart",
                          "shut down", "reply", "forward"):
            if re.search(rf"\b{candidate}\b", action_text, re.I):
                verb = candidate
                break
        where = f" in {app}" if app else ""
        return f"{verb} something{where} (via {tool})"
