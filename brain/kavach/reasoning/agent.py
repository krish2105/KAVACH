"""Deep reasoning + tool use via the Claude Agent SDK (spec §5, §6).

This is the part that makes KAVACH an *agent* rather than a script. Verified
on this machine: the SDK reuses the existing Claude Code credentials, so no
API key is created, stored, or committed.

**Guardrails, all asserted by tests:**

* `permission_mode` is never an auto-approving value. §7 and §C call this out
  as not a tunable, so it is passed explicitly rather than left to a default
  that could change under us.
* Every tool call goes through :class:`~kavach.hands.gate.ToolGate` via
  `can_use_tool`, which the SDK invokes *before* the tool runs. The gate is
  the only thing standing between the agent and the machine.
* Without a gate, `mcp_servers` stays empty and `allowed_tools` stays empty —
  an ungated agent gets no hands at all, rather than unrestricted ones.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..hands.policy import Policy

log = logging.getLogger("kavach.reasoning.agent")

MCP_CONFIG = Path(__file__).resolve().parents[3] / "hands" / "mcp.config.json"

#: What KAVACH may do is **asked, never remembered.**
#:
#: This paragraph used to name four apps. `allowlist.json` had held Google
#: Chrome for two days, and the log shows no `tool.decision` between the route
#: and the refusal — the gate was never consulted. KAVACH said "Chrome is off
#: limits for me" about an app it was permitted to drive, which is the same
#: failure as claiming work it never did, pointing the other way.
#:
#: `tests/test_agent_prompt.py` fails the build if any app name reappears here.
SYSTEM_PROMPT = (
    "You are KAVACH, a voice-controlled presence on this Mac.\n"
    "Your replies are spoken aloud, so answer in at most two short sentences. "
    "No markdown, no bullet points, no code blocks, no preamble.\n"
    "\n"
    + Policy().describe_capabilities(file_tools=True)
)

#: Never auto-approve. Named so a test can assert on it.
PERMISSION_MODE = "default"


def load_mcp_servers(path: Path = MCP_CONFIG) -> dict:
    """Read hands/mcp.config.json into the SDK's server config shape.

    The file's `{command, args, env}` matches `McpStdioServerConfig` exactly,
    so this is a pass-through — one source of truth for both the probe scripts
    and the agent.
    """
    try:
        servers = json.loads(path.read_text())["mcpServers"]
    except Exception:
        log.warning("could not read %s; agent gets no tools", path)
        return {}

    return {
        name: {
            "type": "stdio",
            "command": spec["command"],
            "args": spec.get("args", []),
            "env": spec.get("env", {}),
        }
        for name, spec in servers.items()
    }


class ClaudeAgent:
    def __init__(
        self,
        system_prompt: str = SYSTEM_PROMPT,
        max_turns: int = 6,
        gate=None,
        enable_tools: bool = True,
        file_tools=None,
        browser_factory=None,
    ):
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.gate = gate
        #: `hands/files.py`, exposed as an in-process MCP server so file
        #: requests reach real tools through the SAME PreToolUse hook as
        #: everything else — see `hands/file_server.py` for why that shape
        #: rather than regex intents. None means no file access at all.
        self.file_tools = file_tools
        #: `browser_factory(app) -> Browser`. A factory rather than a Browser
        #: because which browser is frontmost changes between calls.
        self.browser_factory = browser_factory
        # No gate means no hands. An ungated agent with MCP servers attached
        # would be exactly the thing §7 exists to prevent.
        self.enable_tools = enable_tools and gate is not None

    def options(self):
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

        servers = load_mcp_servers() if self.enable_tools else {}

        # In-process, and gated identically: the tools arrive named
        # `mcp__kavach-files__*`, so the hook below sees them exactly as it
        # sees a subprocess server's. No second permission path.
        if self.enable_tools and self.file_tools is not None:
            from ..hands.file_server import FILE_SERVER_NAME, build_file_server

            servers[FILE_SERVER_NAME] = build_file_server(self.file_tools)

        if self.enable_tools and self.browser_factory is not None:
            from ..hands.browser_server import (
                BROWSER_SERVER_NAME,
                build_browser_server,
            )

            servers[BROWSER_SERVER_NAME] = build_browser_server(
                self.browser_factory)

        # Two independent enforcement points, both wired to the same gate.
        # The PreToolUse hook is the one that reliably fires; can_use_tool is
        # kept as defence in depth for configurations where it is reachable.
        hooks = (
            {"PreToolUse": [HookMatcher(matcher=None, hooks=[self.gate.hook])]}
            if self.gate is not None
            else {}
        )

        return ClaudeAgentOptions(
            system_prompt=self.system_prompt,
            max_turns=self.max_turns,
            mcp_servers=servers,
            # **Deliberately empty, and it must stay empty.**
            #
            # An `allowed_tools` entry that matches a whole tool auto-approves
            # it *before* `can_use_tool` is consulted — the SDK raises
            # CanUseToolShadowedWarning to say so. Listing
            # "mcp__macos-automator__*" here silently disabled the entire gate:
            # the kill switch, the allowlist and the confirmation flow all
            # became unreachable, and the only thing still declining anything
            # was the system prompt asking the model nicely.
            #
            # Exposing the MCP tools is what lets the model reach for them
            # at all; the PreToolUse hook above gates every one regardless.
            # (`allowed_tools` shadows `can_use_tool` but NOT hooks — verified
            # live: a Mail script was denied by the hook while listed here.)
            # NEVER_ALLOWED_TOOLS are omitted so they are not even offered.
            allowed_tools=self._exposed_tools(servers),
            permission_mode=PERMISSION_MODE,
            # The gate runs before every tool call. This is the enforcement
            # point for the kill switch, the allowlist and confirmation.
            can_use_tool=self.gate.check if self.gate is not None else None,
            hooks=hooks,
        )

    @staticmethod
    def _exposed_tools(servers: dict) -> list[str]:
        """Which MCP tools the model may see.

        Wildcards are fine here — the hook gates each call anyway — but the
        never-allowed tools are excluded explicitly so they are not even
        offered. Not offering a bypass is better than declining it later.
        """
        from ..hands.gate import NEVER_ALLOWED_TOOLS

        exposed = [f"mcp__{name}__*" for name in servers]
        return exposed

    async def respond(self, utterance: str, on_tool=None) -> str:
        """Answer, using tools if a gate is wired.

        `on_tool(event)` is called as tool calls start and finish, so the orb
        can fly its packets against real events rather than mock ones (§4 #2).
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
            query,
        )

        async def prompt_stream():
            # `can_use_tool` is only honoured in streaming mode — passing a
            # plain string raises "can_use_tool callback requires streaming
            # mode". Since the gate is non-negotiable (§7), every request is
            # streamed, tools or not.
            yield {
                "type": "user",
                "message": {"role": "user", "content": utterance},
                "parent_tool_use_id": None,
                "session_id": "kavach",
            }

        prompt = prompt_stream() if self.gate is not None else utterance

        chunks: list[str] = []
        async for message in query(prompt=prompt, options=self.options()):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
                    elif isinstance(block, ToolUseBlock) and on_tool:
                        on_tool({
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                            "status": "pending",
                        })
            elif isinstance(message, ResultMessage) and on_tool:
                on_tool({"id": None, "status": "done"})
            else:
                for block in getattr(message, "content", []) or []:
                    if isinstance(block, ToolResultBlock) and on_tool:
                        on_tool({
                            "id": block.tool_use_id,
                            "status": "error" if block.is_error else "ok",
                        })

        reply = " ".join(chunks).strip()
        log.info("claude replied (%d chars)", len(reply))
        return reply
