"""Confirmation over HTTP, for actions that cannot be confirmed by voice.

A spoken destructive action is gated by `VoiceConfirmer`: KAVACH speaks the
action back and waits for a spoken yes **in the enrolled voice**. Two things
authorise it — that you said yes, and that it was you.

An HTTP request has neither. The bearer token proves a caller holds a secret,
which is not the same as a person agreeing to a specific deletion right now.
So the API does not act on destructive requests directly: it registers what it
is about to do, blocks, and waits for a second call to approve it. The Watch
and Dynamic Island screens in later phases exist to answer exactly this.

`ApiConfirmer` implements the same `confirm(prompt) -> bool` protocol
`ToolGate` already expects, so the gate is untouched — it cannot tell whether
the answer came from a voice or a phone.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field

log = logging.getLogger("kavach.api.confirm")

#: How long a question waits before it is treated as refused.
#:
#: Long enough to pick up a phone, short enough that a forgotten prompt does
#: not sit around able to authorise something later.
DEFAULT_TIMEOUT = 120.0

#: How long a *registered* confirmation stays answerable.
#:
#: `ApiConfirmer` has its own timeout because something is blocked on it. An
#: item registered by the router blocks nothing, so without this it would sit
#: in the registry forever — and an approval arriving an hour later would run
#: a deletion nobody is watching for. Same reason the blocking path denies on
#: timeout, applied to the path that does not block.
PENDING_TTL = 120.0


@dataclass
class Pending:
    id: str
    prompt: str
    created_at: float = field(default_factory=time.time)
    #: Set when answered. None means still waiting.
    approved: bool | None = None
    #: The utterance this confirmation is gating, so an approval has something
    #: to run. Without it "approved" is a fact about a question nobody kept.
    payload: str | None = None
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "created_at": self.created_at,
            "waiting_for": round(time.time() - self.created_at, 1),
        }


class PendingRegistry:
    """Confirmations awaiting an answer.

    Deliberately in memory: a pending confirmation must not survive a restart.
    An approval that outlives the process it belonged to could authorise an
    action nobody is watching for any more.
    """

    def __init__(self) -> None:
        self._items: dict[str, Pending] = {}

    def register(self, prompt: str, payload: str | None = None) -> Pending:
        # token_urlsafe, not a counter: ids are quoted back by an untrusted
        # client, and a guessable one lets a wrong answer land on the right
        # question.
        item = Pending(id=secrets.token_urlsafe(12), prompt=prompt,
                       payload=payload)
        self._items[item.id] = item
        log.info("awaiting confirmation %s: %s", item.id, prompt)
        return item

    def _expired(self, item: Pending) -> bool:
        return (time.time() - item.created_at) > PENDING_TTL

    def sweep(self) -> None:
        """Drop anything too old to still be answerable."""
        for key in [k for k, v in self._items.items()
                    if v.approved is None and self._expired(v)]:
            log.info("confirmation %s expired unanswered — dropped", key)
            self._items.pop(key, None)

    def answer(self, item_id: str, approved: bool) -> bool:
        """Answer one. False if unknown or already answered.

        Refusing a second answer matters: without it a replayed approval could
        be applied to whatever question happened to be open later.
        """
        item = self._items.get(item_id)
        if item is None or item.approved is not None:
            return False
        if self._expired(item):
            # Expiry is a denial, not a delay. Answering "yes" to a question
            # that has already lapsed must not act.
            log.info("confirmation %s answered too late — refused", item_id)
            self._items.pop(item_id, None)
            return False
        item.approved = approved
        item._event.set()
        log.info("confirmation %s %s", item_id, "approved" if approved else "denied")
        return True

    def discard(self, item_id: str) -> None:
        self._items.pop(item_id, None)

    def list(self) -> list[Pending]:
        self.sweep()
        return [i for i in self._items.values() if i.approved is None]

    def get(self, item_id: str) -> Pending | None:
        item = self._items.get(item_id)
        if item is not None and item.approved is None and self._expired(item):
            self._items.pop(item_id, None)
            return None
        return item


class ApiConfirmer:
    """The `confirm(prompt) -> bool` the ToolGate expects, answered over HTTP."""

    def __init__(self, registry: PendingRegistry, timeout: float = DEFAULT_TIMEOUT):
        self.registry = registry
        self.timeout = timeout

    async def confirm(self, prompt: str) -> bool:
        item = self.registry.register(prompt)
        try:
            await asyncio.wait_for(item._event.wait(), timeout=self.timeout)
        except (asyncio.TimeoutError, TimeoutError):
            # Denied, never approved. An unanswered question is not consent —
            # the same rule the voice path follows on a silent timeout.
            log.info("confirmation %s timed out — denied", item.id)
            return False
        finally:
            # Cleared either way: a dead confirmation left in the list would
            # show up as still actionable on every client polling /pending.
            self.registry.discard(item.id)
        return bool(item.approved)


class EitherConfirmer:
    """Accept an answer by voice or over the API, whichever arrives first.

    The gate holds one confirmer, but a destructive action can now be raised
    by a spoken command or an HTTP one — and the answer should not have to
    come back the same way. Being asked by voice and approving from a phone in
    your pocket is the whole point of the Reach layer.

    Both are offered simultaneously and the first answer wins; the loser is
    cancelled. A refusal from either is a refusal, because the safe outcome of
    a race between "yes" and "no" is not acting.
    """

    def __init__(self, voice_confirmer, api_confirmer: ApiConfirmer):
        self.voice = voice_confirmer
        self.api = api_confirmer

    async def confirm(self, prompt: str) -> bool:
        tasks = {
            asyncio.create_task(self.voice.confirm(prompt)): "voice",
            asyncio.create_task(self.api.confirm(prompt)): "api",
        }
        try:
            done, pending = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()

            first = next(iter(done))
            try:
                approved = bool(first.result())
            except Exception:
                # A confirmer that raised has not approved anything.
                log.warning("confirmer %s failed", tasks[first])
                return False
            log.info("answered by %s: %s", tasks[first],
                     "approved" if approved else "denied")
            return approved
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
