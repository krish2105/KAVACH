#!/usr/bin/env python3
"""Call a single tool on one configured MCP server and print the result.

The companion to ``probe_mcp.py``: that proves a server *speaks* MCP, this
proves it can actually *do* something on this machine — which is what forces
the macOS permission dialogs to appear.

    python3 hands/call_mcp.py macos-automator execute_script '{"scriptContent":"..."}'

Deliberately not wired into anything. Phase 4 replaces it with the real
dispatch path behind ``KillSwitch.guard()`` and the allowlist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CONFIG = Path(__file__).parent / "mcp.config.json"
PROTOCOL_VERSION = "2025-06-18"


def call(server: str, tool: str, arguments: dict, timeout: int = 120) -> dict:
    spec = json.loads(CONFIG.read_text())["mcpServers"][server]
    proc = subprocess.Popen(
        [spec["command"], *spec.get("args", [])],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, env={**os.environ, **spec.get("env", {})},
        start_new_session=True,
    )

    def send(payload: dict) -> None:
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    def wait_for(want_id: int) -> dict | None:
        while True:
            line = proc.stdout.readline()
            if not line:
                return None
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == want_id:
                return message

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "kavach-probe", "version": "0.1.0"}}})
        if wait_for(1) is None:
            return {"error": "server did not complete the handshake"}

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": tool, "arguments": arguments}})

        response = wait_for(2)
        if response is None:
            return {"error": "no response", "stderr": proc.stderr.read()[-800:]}
        return response
    finally:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def render(response: dict) -> str:
    if "error" in response:
        return f"ERROR: {json.dumps(response['error'], default=str)}"
    result = response.get("result", {})
    chunks = [c.get("text", json.dumps(c)[:200])
              for c in result.get("content", [])]
    body = "\n".join(chunks) if chunks else json.dumps(result, default=str)[:800]
    return ("TOOL REPORTED AN ERROR:\n" + body) if result.get("isError") else body


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        raise SystemExit(2)
    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    raise SystemExit(0 if print(render(call(sys.argv[1], sys.argv[2], args))) is None else 0)
