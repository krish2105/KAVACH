"""The local API (Phase 6).

Everything KAVACH can do was previously reachable only from this Mac, through
the mic or the orb. This is the surface the iPhone, the Watch and remote
access all talk to — so it is small, authenticated from the first commit, and
every guardrail the voice path has applies here too.

Three rules it inherits rather than reinvents:

* **The kill switch outranks it.** A latched switch refuses commands here as
  it does everywhere else.
* **The allowlist and the gate still apply**, because commands go through
  `VoiceLoop.respond()` — the same path a spoken one takes. There is no second
  route to the tools.
* **Destructive actions need a human**, and over HTTP that means a second
  call rather than a spoken yes. See `confirm.py`.

Bound to 127.0.0.1. Phase 9 reaches it over Tailscale, which does not require
binding wider.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..killswitch.core import KillSwitch, KillSwitchDisarmed
from .confirm import PendingRegistry
from .models import (
    CommandRequest,
    KillRequest,
    KillResponse,
    CommandResponse,
    ConfirmRequest,
    ConfirmResponse,
    LogResponse,
    PendingResponse,
    StatusResponse,
)

log = logging.getLogger("kavach.api")

#: Bounded so a caller cannot ask the server to read an arbitrarily large log
#: file into memory.
MAX_LOG_LIMIT = 500


def create_app(loop, kill_switch: KillSwitch, token: str,
               registry: PendingRegistry | None = None) -> FastAPI:
    """Build the app.

    Takes its dependencies rather than importing them, so tests drive it with
    a fake loop and a temporary log instead of the real voice stack.
    """
    registry = registry if registry is not None else PendingRegistry()
    security = HTTPBearer(auto_error=False)

    app = FastAPI(
        title="KAVACH",
        summary="Local control surface. Localhost only; every route authenticated.",
        version="1.0.0",
    )
    app.state.registry = registry

    # ——— auth ———

    def require_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
    ) -> None:
        """Reject anything without the right bearer token.

        compare_digest rather than `==`: a plain comparison returns as soon as
        two bytes differ, and that timing difference leaks the token prefix to
        anything that can call this in a loop.
        """
        supplied = credentials.credentials if credentials else ""
        if not hmac.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="Bad or missing token.")

    guard = [Depends(require_token)]

    # ——— reading ———

    @app.get("/status", response_model=StatusResponse, dependencies=guard)
    def status() -> StatusResponse:
        snapshot = loop.state.as_dict()
        return StatusResponse(
            state=snapshot.get("state", "unknown"),
            transcript=snapshot.get("transcript", ""),
            partial=snapshot.get("partial", ""),
            route=snapshot.get("route"),
            confidence=snapshot.get("confidence", 1.0),
            kill_switch="armed" if kill_switch.is_armed else "disarmed",
            wake_word=_wake_word_state(loop),
            voiceprint=_voiceprint_state(loop),
            pending=len(registry.list()),
        )

    @app.get("/log", response_model=LogResponse, dependencies=guard)
    def action_log(limit: int = Query(50, ge=1, le=MAX_LOG_LIMIT)) -> LogResponse:
        """Recent action-log entries, newest last.

        Authenticated like everything else, and worth saying why: this file
        records every tool call and argument KAVACH has made. It is the most
        sensitive thing the API exposes.
        """
        entries = kill_switch.log.read_all()
        return LogResponse(entries=entries[-limit:])

    @app.get("/pending", response_model=PendingResponse, dependencies=guard)
    def pending() -> PendingResponse:
        return PendingResponse(pending=[p.as_dict() for p in registry.list()])

    # ——— acting ———

    @app.post("/command", response_model=CommandResponse, dependencies=guard)
    async def command(request: CommandRequest) -> CommandResponse:
        text = request.cleaned()
        if not text:
            raise HTTPException(status_code=422, detail="Empty command.")

        try:
            kill_switch.guard("api.command")
        except KillSwitchDisarmed:
            raise HTTPException(
                status_code=409,
                detail="Kill switch is latched. Re-arm before sending commands.",
            )

        kill_switch.log.append("api.command", text=text)

        # The same path a spoken command takes — router, handlers, search,
        # music, then a model. There is deliberately no second route to the
        # tools that skips the gate.
        reply = await asyncio.to_thread(loop.respond, text)

        waiting = registry.list()
        if waiting:
            # The gate asked for confirmation and is blocked on it. Nothing has
            # happened yet; the caller has to answer before it does.
            item = waiting[0]
            return CommandResponse(status="pending", id=item.id,
                                   prompt=item.prompt, reply=reply)

        return CommandResponse(status="done", reply=reply,
                               route=loop.state.as_dict().get("route"))

    @app.post("/confirm", response_model=ConfirmResponse, dependencies=guard)
    async def confirm(request: ConfirmRequest) -> ConfirmResponse:
        """Answer a waiting confirmation.

        Two kinds of item end up here and they resolve differently:

        * **Raised by the gate** — an `ApiConfirmer.confirm()` call is blocked
          inside a tool invocation. Answering the registry unblocks it and the
          gate carries on. Nothing else to do.
        * **Raised by the router** — the turn short-circuited before any tool
          ran, so there is nothing blocked and nothing to unblock. Approving
          has to actually *run* the utterance the item was holding.

        Getting this wrong is the difference between an approval that acts and
        an approval that only says it did.
        """
        item = registry.get(request.id)
        if item is None or item.approved is not None:
            # 404 rather than a quiet 200: a client that thinks it approved
            # something must not be told it succeeded when nothing happened.
            raise HTTPException(
                status_code=404,
                detail="No confirmation is waiting with that id.",
            )

        # Re-checked here, not only at /command: an approval can arrive minutes
        # after the question, and the switch may have been latched in between.
        # The whole point of a latch is that it stops what is already in train.
        try:
            kill_switch.guard("api.confirm")
        except KillSwitchDisarmed:
            registry.answer(request.id, approved=False)
            registry.discard(request.id)
            raise HTTPException(
                status_code=409,
                detail="Kill switch is latched. The action was refused, not held.",
            )

        kill_switch.log.append("api.confirm", id=request.id,
                               approved=request.approved)

        if item.payload is not None and hasattr(loop, "resolve_pending"):
            reply = await asyncio.to_thread(
                loop.resolve_pending, request.id, request.approved
            )
            return ConfirmResponse(id=request.id, approved=request.approved,
                                   status="answered", reply=reply)

        registry.answer(request.id, approved=request.approved)
        return ConfirmResponse(id=request.id, approved=request.approved,
                               status="answered")

    @app.post("/kill", response_model=KillResponse, dependencies=guard)
    def kill(request: KillRequest) -> KillResponse:
        """Halt KAVACH from anywhere. The one route the kill switch does not gate.

        Three properties this deliberately has:

        * **Not guarded.** Every other acting route calls `kill_switch.guard()`
          first. This one must work precisely when everything else is refusing,
          so it does not — a stop button that can be stopped is not one.
        * **Idempotent.** `trigger()` latches first and is safe to call
          repeatedly. From a pocket, "did that go through?" has to be
          answerable by pressing it again, so a second kill is a confirmation
          rather than an error.
        * **One-way.** There is no re-arming route, by name or by any other
          name, and a test asserts it over the route table. Stopping KAVACH
          from a device that is not in the room is safe; starting it again from
          one is not. Re-arming stays a deliberate act at this Mac.
        """
        record = kill_switch.trigger(
            source="api",
            reason=request.reason or "halted from the API",
        )
        log.warning("halted from the API: %s", request.reason or "(no reason)")
        return KillResponse(
            kill_switch="disarmed",
            cancelled_tasks=record.get("cancelled_tasks", 0),
            # A list of what was killed, not a count — read off the record
            # rather than assumed.
            killed_processes=len(record.get("killed_processes") or []),
            rearm="Re-arm at the Mac — no route does it.",
        )

    # ——— streaming ———

    @app.websocket("/ws")
    async def stream(websocket: WebSocket) -> None:
        """Snapshot stream, same shape as /status.

        The token comes from a query parameter because browser WebSocket
        clients cannot set headers. That puts it in the URL, so it is never
        logged by this app — and the socket is localhost-only.
        """
        supplied = websocket.query_params.get("token", "")
        if not hmac.compare_digest(supplied, token):
            await websocket.close(code=4401)
            return

        await websocket.accept()
        last = None
        try:
            while True:
                snapshot = loop.state.as_dict()
                if snapshot != last:
                    await websocket.send_text(json.dumps(snapshot))
                    last = dict(snapshot)
                await asyncio.sleep(0.2)
        except Exception:
            return

    return app


def _wake_word_state(loop) -> str:
    """Reported honestly: 'trained' is not 'in use'."""
    try:
        from ..voice.waketune import load_calibration

        wake = getattr(loop, "wake", None)
        if wake is None:
            return "off"
        from ..voice.loop import find_wake_model

        if load_calibration(model=find_wake_model()) is None:
            return "uncalibrated"
        return "ready"
    except Exception:
        return "unknown"


def _voiceprint_state(loop) -> str:
    vp = getattr(loop, "voiceprint", None)
    if vp is None:
        return "off"
    try:
        return "enrolled" if vp.is_enrolled else "not enrolled"
    except Exception:
        return "unknown"
