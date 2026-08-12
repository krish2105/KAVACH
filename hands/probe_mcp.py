#!/usr/bin/env python3
"""Probe a stdio MCP server: handshake, list its tools, exit.

Phase 0 needs evidence each server in ``mcp.config.json`` actually starts and
speaks MCP on this machine — not that it exists on a registry. This does the
minimum real client handshake with no SDK dependency.

    python3 hands/probe_mcp.py                 # probe all servers
    python3 hands/probe_mcp.py peekaboo        # probe one

Exit code is the number of servers that failed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

CONFIG = Path(__file__).parent / "mcp.config.json"
PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 120  # first run downloads the package via npx/uvx


def _reader(pipe, sink: list[str]) -> None:
    for line in iter(pipe.readline, ""):
        sink.append(line)


def probe(name: str, spec: dict) -> tuple[bool, str]:
    env = {**os.environ, **spec.get("env", {})}
    cmd = [spec["command"], *spec.get("args", [])]

    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
            start_new_session=True,
        )
    except FileNotFoundError:
        return False, f"command not found: {spec['command']}"

    stderr_lines: list[str] = []
    threading.Thread(target=_reader, args=(proc.stderr, stderr_lines),
                     daemon=True).start()

    def send(payload: dict) -> None:
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    def read_response(want_id: int) -> dict | None:
        """Read lines until the response with the wanted id appears.
        Servers interleave log lines and notifications on stdout."""
        while True:
            line = proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # a log line, not JSON-RPC
            if message.get("id") == want_id:
                return message

    result_note = ""
    try:
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "kavach-probe", "version": "0.1.0"},
            },
        })

        init = read_response(1)
        if init is None:
            tail = "".join(stderr_lines[-6:]).strip()
            return False, f"no response to initialize.\n      stderr: {tail}"
        if "error" in init:
            return False, f"initialize failed: {init['error']}"

        info = init.get("result", {}).get("serverInfo", {})
        result_note = (f"{info.get('name', '?')} v{info.get('version', '?')}"
                       f" (protocol {init.get('result', {}).get('protocolVersion', '?')})")

        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})

        listed = read_response(2)
        if listed is None or "error" in listed:
            return False, f"{result_note}\n      tools/list failed: {listed}"

        tools = [t["name"] for t in listed.get("result", {}).get("tools", [])]
        return True, f"{result_note}\n      {len(tools)} tools: {', '.join(tools)}"

    except (BrokenPipeError, OSError) as exc:
        tail = "".join(stderr_lines[-6:]).strip()
        return False, f"transport error: {exc}\n      stderr: {tail}"
    finally:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def main(argv: list[str]) -> int:
    servers = json.loads(CONFIG.read_text())["mcpServers"]
    wanted = argv or list(servers)

    failures = 0
    for name in wanted:
        if name not in servers:
            print(f"  ? {name}: not in {CONFIG.name}")
            failures += 1
            continue

        print(f"  probing {name} …", flush=True)
        ok, note = probe(name, servers[name])
        print(f"  {'✓' if ok else '✗'} {name}: {note}\n", flush=True)
        failures += 0 if ok else 1

    print(f"{len(wanted) - failures}/{len(wanted)} servers responded to MCP.")
    return failures


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
