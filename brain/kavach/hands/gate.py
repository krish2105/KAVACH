"""The tool gate — every MCP call passes through here (spec §7, §C).

Wired into the Claude Agent SDK as `can_use_tool`, which the SDK invokes
*before* a tool executes. Nothing reaches an MCP server without a verdict from
this module.

Checks run cheapest-and-most-absolute first, and each one can only ever deny:

1. **Kill switch.** Latched means nothing runs, full stop. Checked before
   anything else so a halt cannot be out-raced by an otherwise-valid request.
2. **Known server.** The tool must belong to a server in
   ``hands/mcp.config.json``.
3. **Identifiable target.** If we cannot tell which app a call would touch,
   we cannot check it against the allowlist — so it does not run. Refusing the
   unreadable is the only safe reading of an allowlist.
4. **Allowlist.** Safari, Notes, Calendar, Finder. Unknown means no.
5. **Confirmation.** Destructive or externally visible actions are spoken back
   and wait. With no confirmer wired, they are denied: silence is not consent.

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
from ..reasoning.router import looks_destructive
from .allowlist import Allowlist, AppNotAllowed

log = logging.getLogger("kavach.hands.gate")

MCP_CONFIG = Path(__file__).resolve().parents[3] / "hands" / "mcp.config.json"

#: mcp__<server>__<tool>
_TOOL_NAME_RE = re.compile(r"^mcp__(?P<server>[a-z0-9_-]+)__(?P<tool>[A-Za-z0-9_-]+)$")

#: AppleScript/JXA: tell application "Safari"
_TELL_APP_RE = re.compile(r'tell\s+application\s+"([^"]+)"', re.I)

#: Tools that defeat the allowlist by design, so they are never exposed and
#: never permitted — even for an allowlisted app, even with confirmation.
#: A shell command has no "app" for this gate to check, which makes it a
#: complete bypass of §7 rather than an edge case of it.
NEVER_ALLOWED_TOOLS = frozenset({
    "Shell",     # macos-accessibility: arbitrary commands + AppleScript
    "agent",     # peekaboo: drives its own sub-agent, ungated by us
    "Desktop",   # macos-accessibility: creates/switches virtual desktops
})

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

#: Which physical device each MCP server reaches. A server that is not listed
#: here is denied: unmapped means ungovernable.
SERVER_DEVICES = {
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
    try:
        return set(json.loads(path.read_text())["mcpServers"])
    except Exception:
        log.warning("could not read %s; no MCP server is trusted", path)
        return set()


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
    ):
        self.ks = kill_switch
        self.allowlist = allowlist or Allowlist()
        self.confirmer = confirmer
        self.servers = servers if servers is not None else load_configured_servers()

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
            return ("deny", f"{tool!r} is not a recognised MCP tool name.", {})

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

        # 3 — if we can't tell what it touches, we can't clear it.
        app = extract_target_app(tool, args)
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

            return (
                "deny",
                "Cannot determine which app this would affect, so it is "
                "refused. Actions must name their target to be checked "
                "against the allowlist.",
                {"server": server},
            )

        # 4 — allowlist.
        try:
            self.allowlist.check(app)
        except AppNotAllowed as exc:
            return ("deny", str(exc), {"server": server, "app": app})

        # 5 — destructive or externally visible → speak it back and wait.
        action_text = " ".join(
            v for v in args.values() if isinstance(v, str)
        ) or match.group("tool")

        if looks_destructive(action_text) or self.allowlist.needs_confirmation(action_text):
            if self.confirmer is None:
                return (
                    "deny",
                    f"This would {self._describe(app, match.group('tool'), action_text)}, "
                    f"and there is no way to ask you right now. Refusing.",
                    {"server": server, "app": app, "destructive": True},
                )

            prompt = (
                f"This will {self._describe(app, match.group('tool'), action_text)}. "
                f"Should I?"
            )
            granted = await self.confirmer.confirm(prompt)
            if not granted:
                return (
                    "deny", "You declined.",
                    {"server": server, "app": app, "destructive": True},
                )
            return (
                "allow", "confirmed by user",
                {"server": server, "app": app, "destructive": True,
                 "confirmed": True},
            )

        return ("allow", "allowlisted, read-only", {"server": server, "app": app})

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
    def _describe(app: str, tool: str, action_text: str) -> str:
        """A short spoken paraphrase. Names the app and what will happen —
        a prompt that says only "allow tool?" is not informed consent."""
        verb = "act on"
        for candidate in ("delete", "send", "remove", "trash", "buy", "purchase",
                          "submit", "post", "share", "install", "restart",
                          "shut down", "reply", "forward"):
            if re.search(rf"\b{candidate}\b", action_text, re.I):
                verb = candidate
                break
        return f"{verb} something in {app} (via {tool})"
