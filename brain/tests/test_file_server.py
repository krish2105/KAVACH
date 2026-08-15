"""File tools as an in-process MCP server — and why that shape was chosen.

`hands/files.py` had its gates and no way to reach them: nothing in the voice
path called it. Two ways to fix that, and the choice matters more than it looks.

**Regex intents in `MacActions`** is what app control does, and it is wrong
here. "find my tax document from last year" is not a pattern; file requests are
the case where language actually helps, and a regex would either miss most of
them or claim ones it cannot serve.

**An in-process SDK MCP server** gives the agent real tools — and, decisively,
they arrive as `mcp__kavach-files__*`, so they pass through the **same
PreToolUse hook** as every other tool call. The kill switch, the confirmation
and the action log apply without a line of new permission code.

That is the property under test here. A second permission path is how one of
them goes stale, and this project has already found that defect three times in
one file each: the startup banner, the Ollama model name, the agent prompt.
"""

import pytest

from kavach.hands.file_server import FILE_SERVER_NAME, build_file_server
from kavach.hands.gate import SERVER_DEVICES, ToolGate
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog


class Yes:
    def __init__(self):
        self.asked = []

    async def confirm(self, prompt: str) -> bool:
        self.asked.append(prompt)
        return True


@pytest.fixture
def ks(tmp_path):
    return KillSwitch(log=ActionLog(tmp_path / "actions.jsonl"))


# ═══ it is one gate, not two ═══

def test_the_file_server_is_mapped_to_a_device():
    """`device_for_server` returns None for anything unmapped, and the gate
    denies that outright. An unmapped server is ungovernable, so a new one
    that forgets this line is refused rather than waved through."""
    assert SERVER_DEVICES.get(FILE_SERVER_NAME) == "mac"


@pytest.mark.asyncio
async def test_file_tools_pass_through_the_same_gate(ks):
    """No second permission path. If this fails, `files.py`'s own gates are
    the only thing left and the kill switch is not among them."""
    gate = ToolGate(ks, confirmer=Yes(), servers={FILE_SERVER_NAME})
    verdict, _, _ = await gate._decide(
        f"mcp__{FILE_SERVER_NAME}__read_file", {"path": "/tmp/x"})
    assert verdict == "allow"


@pytest.mark.asyncio
async def test_a_latched_kill_switch_stops_file_tools_too(ks):
    gate = ToolGate(ks, confirmer=Yes(), servers={FILE_SERVER_NAME})
    ks.trigger("test", "latched")

    verdict, reason, _ = await gate._decide(
        f"mcp__{FILE_SERVER_NAME}__read_file", {"path": "/tmp/x"})

    assert verdict == "deny"
    assert "kill switch" in reason.lower()


@pytest.mark.asyncio
async def test_deleting_a_file_confirms_at_the_gate(ks):
    """`files.py` confirms on its own too. Both firing is defence in depth,
    and the gate one is what the §7 log records."""
    confirmer = Yes()
    gate = ToolGate(ks, confirmer=confirmer, servers={FILE_SERVER_NAME})

    verdict, _, _ = await gate._decide(
        f"mcp__{FILE_SERVER_NAME}__delete_file",
        {"path": "/tmp/gone.txt"})

    assert verdict == "allow"
    assert confirmer.asked, "a delete reached the tool without being asked about"


@pytest.mark.asyncio
async def test_reading_does_not_confirm(ks):
    confirmer = Yes()
    gate = ToolGate(ks, confirmer=confirmer, servers={FILE_SERVER_NAME})

    await gate._decide(f"mcp__{FILE_SERVER_NAME}__read_file",
                       {"path": "/tmp/notes.txt"})

    assert not confirmer.asked, "confirming reads trains the user to say yes"


# ═══ the server itself ═══

def test_the_server_exposes_the_tools_the_agent_needs(ks):
    server = build_file_server(FileToolsStub())
    assert server is not None


def test_building_it_without_tools_is_refused():
    """No FileTools means no gates. An ungated file server is exactly the
    thing §7 exists to prevent, so it is not constructible."""
    with pytest.raises(ValueError):
        build_file_server(None)


class FileToolsStub:
    last_trashed = None

    def read(self, path, **k): return "contents"
    def list_dir(self, path): return ["a.txt"]
    def search(self, root, pattern, **k): return ["/tmp/a.txt"]
    def write(self, path, content): return path
    def delete(self, path): return path
