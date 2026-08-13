"""Deep reasoning via the Claude Agent SDK (spec §5).

This is the part that makes KAVACH an *agent* rather than a script. Verified
on this machine: the SDK reuses the existing Claude Code credentials, so no
API key is created, stored, or committed.

**`permission_mode` is never set to an auto-approving value.** §7 and the
project's §C call this out as not a tunable, so it is passed explicitly here
rather than left to a default that might change under us — and there is a test
asserting it.

Phase 3 gives the agent **no tools at all**: `allowed_tools=[]`. The MCP
servers exist and are permission-granted (Phase 0) but wiring them to a voice
loop is Phase 4's job, behind the allowlist and the confirmation flow. An
agent that can reason but not yet act is exactly the right intermediate state.
"""

from __future__ import annotations

import logging

log = logging.getLogger("kavach.reasoning.agent")

SYSTEM_PROMPT = (
    "You are KAVACH, a voice-controlled presence on this Mac.\n"
    "Your replies are spoken aloud, so answer in at most two short sentences. "
    "No markdown, no bullet points, no code blocks, no preamble.\n"
    "If a request would need a tool you do not have, say plainly what you "
    "would need to do it. Never claim to have done something you have not."
)

#: Never auto-approve. Kept as a named constant so a test can assert on it.
PERMISSION_MODE = "default"


class ClaudeAgent:
    def __init__(self, system_prompt: str = SYSTEM_PROMPT, max_turns: int = 1):
        self.system_prompt = system_prompt
        self.max_turns = max_turns

    def options(self):
        from claude_agent_sdk import ClaudeAgentOptions

        return ClaudeAgentOptions(
            system_prompt=self.system_prompt,
            max_turns=self.max_turns,
            # Phase 4 adds mcp_servers + the allowlist here. Not before.
            allowed_tools=[],
            permission_mode=PERMISSION_MODE,
        )

    async def respond(self, utterance: str) -> str:
        from claude_agent_sdk import AssistantMessage, TextBlock, query

        chunks: list[str] = []
        async for message in query(prompt=utterance, options=self.options()):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)

        reply = " ".join(chunks).strip()
        log.info("claude replied (%d chars)", len(reply))
        return reply
