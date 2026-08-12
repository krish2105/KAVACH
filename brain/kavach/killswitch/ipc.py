"""Unix-socket control surface for the kill switch.

This is what lets a kill arrive from *outside* the agent process — the `kavach
kill` CLI, a shell alias, a Stream Deck button, anything that can write a line
to a socket. It matters because the surface most likely to fail is the one
inside the process that has gone wrong.

Protocol: one JSON object per line, request and response.

    -> {"cmd": "kill", "reason": "user panic", "source": "cli"}
    <- {"ok": true, "record": {...}, "status": {...}}

The socket lives at ``~/.kavach/kill.sock`` with mode 0600 inside a 0700
directory. That permission is load-bearing: anything that can write to this
socket can also *re-arm* the switch.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .core import KillSwitch

DEFAULT_SOCKET_PATH = Path.home() / ".kavach" / "kill.sock"

VALID_COMMANDS = {"kill", "rearm", "status", "ping"}

# sockaddr_un.sun_path is 104 bytes on macOS (108 on Linux). Exceeding it fails
# with a bare "AF_UNIX path too long" from deep inside asyncio, which is a
# miserable thing to debug at the moment you need the kill switch to work.
SUN_PATH_MAX = 104


def _check_socket_path(sock_path: Path) -> None:
    encoded = len(str(sock_path).encode("utf-8"))
    if encoded >= SUN_PATH_MAX:
        raise ValueError(
            f"socket path is {encoded} bytes, limit is {SUN_PATH_MAX - 1}: "
            f"{sock_path}\nUse a shorter path (the default "
            f"{DEFAULT_SOCKET_PATH} is well inside the limit)."
        )


def _handle_command(ks: KillSwitch, payload: dict) -> dict:
    cmd = str(payload.get("cmd", "")).lower()
    source = str(payload.get("source", "socket"))
    reason = str(payload.get("reason", ""))

    if cmd not in VALID_COMMANDS:
        return {"ok": False, "error": f"unknown command {cmd!r}",
                "valid": sorted(VALID_COMMANDS)}

    if cmd == "kill":
        record = ks.trigger(source=source, reason=reason)
        return {"ok": True, "record": record, "status": ks.status()}

    if cmd == "rearm":
        record = ks.rearm(source=source, reason=reason)
        return {"ok": True, "record": record, "status": ks.status()}

    # status / ping
    return {"ok": True, "status": ks.status()}


async def serve(ks: KillSwitch, path: Path | str | None = None) -> asyncio.Server:
    """Start the control socket. Returns the server; caller owns its lifetime."""
    sock_path = Path(path) if path is not None else DEFAULT_SOCKET_PATH
    _check_socket_path(sock_path)
    sock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # A stale socket file from a crashed run would make bind() fail.
    if sock_path.exists():
        sock_path.unlink()

    async def on_client(reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not line:
                return
            try:
                payload = json.loads(line.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("payload must be a JSON object")
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
                response = {"ok": False, "error": f"malformed request: {exc}"}
            else:
                response = _handle_command(ks, payload)

            writer.write((json.dumps(response, default=str) + "\n").encode("utf-8"))
            await writer.drain()
        except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()

    # Create with a restrictive umask so the socket is never briefly world
    # writable; chmod afterwards too, since umask handling for AF_UNIX is
    # platform-dependent.
    old_umask = os.umask(0o177)
    try:
        server = await asyncio.start_unix_server(on_client, path=str(sock_path))
    finally:
        os.umask(old_umask)
    os.chmod(sock_path, 0o600)

    return server


async def send_command(
    cmd: str,
    path: Path | str | None = None,
    reason: str = "",
    source: str = "cli",
    timeout: float = 5.0,
) -> dict:
    """Send one command to a running kill-switch daemon and return its reply."""
    sock_path = Path(path) if path is not None else DEFAULT_SOCKET_PATH

    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(sock_path)), timeout=timeout
    )
    try:
        request = json.dumps({"cmd": cmd, "reason": reason, "source": source})
        writer.write((request + "\n").encode("utf-8"))
        await writer.drain()

        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not line:
            return {"ok": False, "error": "daemon closed the connection"}
        return json.loads(line.decode("utf-8"))
    finally:
        writer.close()
