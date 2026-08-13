"""WebSocket bridge between the Brain and the Presence layer.

The orb was built in Phase 1 against a `KavachSource` interface, with a mock
standing in for this. That mock is not replaced or deleted — this is a second
implementation of the same contract, so the HUD code is untouched.

Binds to **127.0.0.1 only**. This socket can halt the kill switch and reports
what the machine is hearing; it has no business being reachable off-host.

Protocol
--------
server → client   the snapshot, shape-identical to `KavachSnapshot` in
                  apps/orb/lib/kavachState.ts
client → server   {"cmd": "halt" | "rearm" | "interrupt" | "ptt", "pressed": bool}
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection, serve

from ..killswitch.core import KillSwitch
from ..voice.loop import VoiceLoop

log = logging.getLogger("kavach.bridge")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class Bridge:
    def __init__(self, loop: VoiceLoop, kill_switch: KillSwitch):
        self.voice = loop
        self.ks = kill_switch
        self.clients: set[ServerConnection] = set()
        self._async_loop: asyncio.AbstractEventLoop | None = None
        self._latest: dict[str, Any] = {}

    # ——— called from the voice thread ———

    def publish(self, snapshot: dict) -> None:
        """Thread-safe hand-off from the audio thread to the asyncio loop.

        The voice loop runs on its own thread and publishes at animation rate;
        marshalling is required, and dropping frames is fine — only the latest
        snapshot matters.
        """
        self._latest = snapshot
        if self._async_loop is None or not self.clients:
            return
        try:
            self._async_loop.call_soon_threadsafe(self._fanout, snapshot)
        except RuntimeError:
            pass  # loop shutting down

    def _fanout(self, snapshot: dict) -> None:
        payload = json.dumps(snapshot)
        for client in list(self.clients):
            asyncio.create_task(self._send(client, payload))

    async def _send(self, client: ServerConnection, payload: str) -> None:
        try:
            await client.send(payload)
        except websockets.exceptions.ConnectionClosed:
            self.clients.discard(client)

    # ——— connection handling ———

    async def _handle(self, websocket: ServerConnection) -> None:
        self.clients.add(websocket)
        log.info("orb connected (%d client(s))", len(self.clients))
        try:
            if self._latest:
                await websocket.send(json.dumps(self._latest))

            async for message in websocket:
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    continue
                self._command(payload)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            log.info("orb disconnected (%d left)", len(self.clients))

    def _command(self, payload: dict) -> None:
        cmd = str(payload.get("cmd", "")).lower()

        if cmd == "halt":
            # The real latch, not a UI state: this is the same KillSwitch the
            # hotkey and menu bar drive.
            self.ks.trigger(source="orb", reason="halt from Presence layer")
        elif cmd == "rearm":
            self.ks.rearm(source="orb")
        elif cmd == "interrupt":
            self.voice.interrupt()
        elif cmd == "ptt":
            self.voice.push_to_talk(bool(payload.get("pressed")))
        else:
            log.debug("unknown command %r", cmd)

        self.voice.publish()

    async def run(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self._async_loop = asyncio.get_running_loop()
        async with serve(self._handle, host, port):
            log.info("bridge listening on ws://%s:%d", host, port)
            await asyncio.Future()  # run forever
