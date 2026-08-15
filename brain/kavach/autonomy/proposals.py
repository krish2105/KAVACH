"""Phase 33 — the proposal queue. Nothing in it runs on its own.

PROPOSE-tier actions queue here instead of interrupting. The user approves,
rejects or edits them in a batch, through the HUD or the Phase 6 API
(`reach-6`: FastAPI on 127.0.0.1:8770, bearer token — confirmed to exist
before this was built, as the spec asked).

**There is no auto-execute timeout, and that is the load-bearing property.**
An unreviewed item sits, or expires unexecuted. It never runs by default.

That matters because the §7 confirmation already treats a timeout as a denial,
and a queue that executed on expiry would be the opposite rule living next
door. Two rules disagreeing about what silence means is how one of them
quietly becomes the one that matters — and this project has found six
instances of one fact living in two places already.

**This module records decisions. It does not execute them.** A test greps it
for `subprocess`, `osascript` and friends. Approving a proposal authorises it
to be *attempted*; when it runs, it still passes the tool gate — kill switch,
confirmation, action log. A queue approval is not a second permission system.

`EXPIRED` is deliberately distinct from `REJECTED`. "You said no" and "nobody
looked" are different facts, and Phase 34 learns from approval history —
counting a lapse as a rejection would teach it something that never happened.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

log = logging.getLogger("kavach.autonomy.proposals")

DEFAULT_PATH = Path.home() / ".kavach" / "proposals.json"

#: How long an unreviewed proposal stays offerable. Long, because the point of
#: a queue is that the user gets to it when they get to it — and nothing is
#: lost by waiting, since nothing executes either way.
DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60


class Status(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    #: Nobody reviewed it in time. **Not** the same as rejected.
    EXPIRED = "expired"


@dataclass
class Proposal:
    id: str
    action: str
    description: str
    created_at: float
    ttl_seconds: float
    status: Status = Status.PENDING

    def as_dict(self) -> dict:
        out = asdict(self)
        out["status"] = self.status.value
        return out


class ProposalQueue:
    """Proposals, persisted. Records decisions; executes nothing."""

    def __init__(self, path: Path | str = DEFAULT_PATH, log_=None,
                 trust=None):
        self.path = Path(path)
        self.log = log_
        #: Phase 34. Approvals and rejections advance or reset its streaks.
        #: An EXPIRY teaches it nothing — nobody looked, which is neither a
        #: yes nor a no, and recording it as either invents a decision.
        self.trust = trust
        self._items: dict[str, Proposal] = {}
        self._load()

    # ——— persistence ———

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except Exception:
            log.warning("could not read %s — starting with an empty queue",
                        self.path, exc_info=True)
            return
        for raw in data.get("proposals", []):
            try:
                raw = dict(raw)
                raw["status"] = Status(raw.get("status", "pending"))
                item = Proposal(**raw)
            except Exception:
                log.debug("skipping an unreadable proposal", exc_info=True)
                continue
            self._items[item.id] = item

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"proposals": [p.as_dict() for p in self._items.values()]}
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n")
        temp.replace(self.path)

    def _record(self, event: str, **fields) -> None:
        if self.log is not None:
            self.log.append(event, **fields)

    # ——— adding ———

    def add(self, action: str, description: str,
            ttl_seconds: float = DEFAULT_TTL_SECONDS) -> Proposal:
        item = Proposal(
            id=secrets.token_urlsafe(8),
            action=str(action),
            description=str(description),
            created_at=time.time(),
            ttl_seconds=float(ttl_seconds),
        )
        self._items[item.id] = item
        self._save()
        self._record("proposal.added", id=item.id, action=item.action,
                     description=item.description)
        return item

    # ——— review ———

    def get(self, item_id: str) -> Proposal:
        try:
            return self._items[item_id]
        except KeyError:
            raise KeyError(f"no proposal {item_id!r}") from None

    def _decide(self, item_id: str, status: Status, event: str) -> Proposal:
        item = self.get(item_id)
        if item.status is Status.EXPIRED:
            # Approving something that already lapsed would resurrect an
            # action the user never reviewed in time.
            raise ValueError(
                f"proposal {item_id!r} expired and cannot be {status.value}"
            )
        item.status = status
        self._save()
        self._record(event, id=item.id, action=item.action)
        if self.trust is not None:
            try:
                self.trust.record(item.action,
                                  approved=status is Status.APPROVED)
            except Exception:
                log.debug("could not record trust", exc_info=True)
        return item

    def approve(self, item_id: str) -> Proposal:
        return self._decide(item_id, Status.APPROVED, "proposal.approved")

    def reject(self, item_id: str) -> Proposal:
        return self._decide(item_id, Status.REJECTED, "proposal.rejected")

    def approve_many(self, ids) -> list[Proposal]:
        """Batch review — the point of a queue rather than a prompt."""
        return [self.approve(item_id) for item_id in ids]

    def edit(self, item_id: str, description: str) -> Proposal:
        """Change what is proposed. It stays PENDING.

        An edited proposal is a different proposal and needs approving on its
        own terms — carrying an approval across an edit would approve text
        the user never read.
        """
        item = self.get(item_id)
        item.description = str(description)
        item.status = Status.PENDING
        self._save()
        self._record("proposal.edited", id=item.id, description=item.description)
        return item

    # ——— time ———

    def sweep(self, now: float | None = None) -> list[Proposal]:
        """Mark lapsed proposals EXPIRED. **Executes nothing.**"""
        moment = time.time() if now is None else now
        expired = []
        for item in self._items.values():
            if item.status is not Status.PENDING:
                continue
            if moment - item.created_at >= item.ttl_seconds:
                item.status = Status.EXPIRED
                expired.append(item)
                self._record("proposal.expired", id=item.id, action=item.action)
        if expired:
            self._save()
        return expired

    # ——— reading ———

    def pending(self) -> list[Proposal]:
        return [p for p in self._items.values() if p.status is Status.PENDING]

    def ready_to_run(self) -> list[Proposal]:
        """Approved and not lapsed. **Approved means may be attempted** — the
        tool gate still runs when it does."""
        return [p for p in self._items.values() if p.status is Status.APPROVED]


__all__ = ["Proposal", "ProposalQueue", "Status", "DEFAULT_TTL_SECONDS"]
