"""The file tools, as an in-process MCP server the agent can actually reach.

`hands/files.py` had its gates and no way to get to them — nothing in the voice
path called it. Two ways to fix that, and the choice matters more than it looks.

**Regex intents in `MacActions`** is what app control does, and it is the wrong
shape here. `open Notes` is a pattern; "find my tax document from last year" is
not. File requests are precisely the case where language earns its keep, and a
regex would either miss most of them or — worse — claim ones it cannot serve.

**An in-process SDK MCP server** gives the agent real tools, and decisively
they arrive named ``mcp__kavach-files__*``, so they pass through the **same
`PreToolUse` hook** as every other tool call. The kill switch, the §7
confirmation and the action log all apply without a line of new permission
code.

That last part is the whole argument. A second permission path is how one of
them goes stale, and this project has found that exact defect three times —
the startup banner, the Ollama model name, the agent prompt — each time as a
fact written down twice where one copy quietly stopped being true.

**`FileTools` still runs its own gates underneath.** Both firing is defence in
depth, not duplication: the gate governs whether the *call* happens, `files.py`
governs whether the *operation* does, and they answer to the same kill switch.
"""

from __future__ import annotations

import logging

log = logging.getLogger("kavach.hands.file_server")

#: The server name, used in three places that must agree: the tool names the
#: agent sees, `SERVER_DEVICES` in the gate, and the exposed-tools wildcard.
#: Named once here so they cannot drift.
FILE_SERVER_NAME = "kavach-files"


def build_file_server(tools):
    """An `McpSdkServerConfig` wrapping `tools`, or raise.

    `tools` must be a `FileTools` — the thing holding the kill switch and the
    confirmer. **Not optional**: an ungated file server is exactly what §7
    exists to prevent, so it is not constructible rather than merely
    discouraged.
    """
    if tools is None:
        raise ValueError(
            "file tools need a FileTools instance — an ungated file server "
            "would bypass the kill switch and the confirmation entirely."
        )

    from claude_agent_sdk import create_sdk_mcp_server, tool

    @tool("read_file", "Read a text file and return its contents.",
          {"path": str})
    async def read_file(args):
        return _text(tools.read(args["path"]))

    @tool("list_directory", "List the entries in a directory.", {"path": str})
    async def list_directory(args):
        return _text("\n".join(tools.list_dir(args["path"])))

    @tool("search_files",
          "Find files by glob pattern under a directory, e.g. '*.pdf'.",
          {"root": str, "pattern": str})
    async def search_files(args):
        found = tools.search(args["root"], args["pattern"])
        return _text("\n".join(found) if found else "nothing matched")

    @tool("write_file",
          "Write text to a file. Overwrites if it exists. Asks first.",
          {"path": str, "content": str})
    async def write_file(args):
        written = tools.write(args["path"], args["content"])
        return _text(f"wrote {written}")

    @tool("delete_file",
          "Move a file to the Trash. Asks first, and never deletes outright.",
          {"path": str})
    async def delete_file(args):
        tools.delete(args["path"])
        # Says where it went, so the reply can too. "Deleted" and "moved to
        # the Trash" are different promises and only one of them is true.
        return _text(f"moved to the Trash: {tools.last_trashed}")

    return create_sdk_mcp_server(
        name=FILE_SERVER_NAME,
        tools=[read_file, list_directory, search_files, write_file, delete_file],
    )


def _text(body: str) -> dict:
    """MCP content shape.

    Errors are deliberately **not** caught here. `FileTools` raises
    `PermissionError` carrying the Full Disk Access hint, and letting that
    reach the agent is the point — it can then tell the user which grant is
    missing instead of reporting an empty file.
    """
    return {"content": [{"type": "text", "text": str(body)}]}


__all__ = ["build_file_server", "FILE_SERVER_NAME"]
