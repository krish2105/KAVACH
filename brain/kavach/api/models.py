"""Request and response shapes.

Validation is a guardrail here, not paperwork: every one of these endpoints
can act on the machine, and a hand-parsed body is where that goes wrong.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CommandRequest(BaseModel):
    #: min_length=1 after stripping — an empty command would otherwise reach
    #: the router, which would send it to a model to think about nothing.
    text: str = Field(min_length=1, max_length=2000)

    def cleaned(self) -> str:
        return self.text.strip()


class CommandResponse(BaseModel):
    #: `pending` means nothing has happened yet and something is waiting on an
    #: approval — the distinction the whole phase turns on.
    status: Literal["done", "pending", "refused"]
    reply: str | None = None
    id: str | None = None
    prompt: str | None = None
    route: str | None = None


class ConfirmRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    approved: bool


class ConfirmResponse(BaseModel):
    id: str
    approved: bool
    status: Literal["answered"]
    #: Present when approving actually ran something, so a phone or Watch can
    #: show the outcome rather than only "approved".
    reply: str | None = None


class KillRequest(BaseModel):
    #: Optional, and it goes straight into the action log. Worth writing:
    #: "why did KAVACH stop at 3am" is answered by this field or by nothing.
    reason: str = Field("", max_length=500)


class KillResponse(BaseModel):
    kill_switch: Literal["disarmed"]
    cancelled_tasks: int
    killed_processes: int
    #: Said out loud in the response because the asymmetry is the point: this
    #: route stops KAVACH and no route starts it again.
    rearm: str


class GhostRequest(BaseModel):
    #: Deliberately has no "active" field. This endpoint only ever turns
    #: sensing OFF — see the route for why.
    reason: str = Field("", max_length=500)


class GhostResponse(BaseModel):
    ghost: Literal[True]
    #: Spelled out in the response because a client that assumes it can toggle
    #: needs to find out here, not by wondering why the mic never came back.
    resume: str


class PendingItem(BaseModel):
    id: str
    prompt: str
    created_at: float
    waiting_for: float


class PendingResponse(BaseModel):
    pending: list[PendingItem]


class LogResponse(BaseModel):
    entries: list[dict[str, Any]]


class StatusResponse(BaseModel):
    """What KAVACH is doing, and what it is capable of right now.

    Deliberately assembled field by field rather than dumping internal state:
    a serialiser that reflects over an object will eventually reflect over
    something it should not, and this response leaves the machine in Phase 9.
    """

    state: str
    transcript: str
    partial: str
    route: str | None
    confidence: float
    kill_switch: str
    wake_word: str
    voiceprint: str
    pending: int
    ghost: bool
    #: §13. Why the router chose this path — the same string the HUD shows and
    #: the action log records, so a phone and the orb never disagree.
    reason: str = ""
    intent: str = ""
