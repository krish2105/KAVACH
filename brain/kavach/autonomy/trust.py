"""Phase 34 — trust learned from approvals, and what it can never learn.

After a run of consistent approvals, KAVACH **offers** to stop asking about
that action type. It offers; it never decides. The user is the one who gets to
notice they have stopped wanting to be asked.

Three rules, each preventing a specific failure:

* **The Phase 30 ceiling holds regardless of history.** A hundred approved
  deletes still cannot make ``file.delete`` AUTO. If approval history could
  reach AUTO the ceiling would be a speed bump — you would only need to
  approve enough times, which is precisely what someone in a hurry does. The
  ceiling is re-checked here rather than trusted from `TierPolicy`, for the
  same reason the gate re-checks it: a rule enforced in one place is a rule
  with one place to go wrong.
* **Demotion is instant and unconditional.** Nothing may stand between the
  user and making something *more* restrictive. There is no confirmation, no
  streak requirement, and no way for this module to refuse.
* **A rejection resets the streak.** "Yes, yes, yes, no, yes" is not four
  approvals; it is someone who does not consistently want this.

Depends on Phase 30 (`tiers.py`) and Phase 33 (`proposals.py`), both confirmed
present before this was written.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .tiers import Tier, TierPolicy, is_ceilinged

log = logging.getLogger("kavach.autonomy.trust")

DEFAULT_PATH = Path.home() / ".kavach" / "trust.json"

#: Consecutive approvals before an offer is made. Configurable, but the
#: default is deliberately not 1 or 2 — those are "you were agreeable twice",
#: not a pattern.
DEFAULT_STREAK = 5


@dataclass
class Offer:
    """A promotion KAVACH is willing to make, if asked."""

    action: str
    tier: Tier
    streak: int

    def as_dict(self) -> dict:
        return {"action": self.action, "tier": self.tier.value,
                "streak": self.streak}


class TrustLedger:
    """Approval streaks per action type, and the offers they justify."""

    def __init__(self, path: Path | str = DEFAULT_PATH,
                 tiers: TierPolicy | None = None,
                 streak_required: int = DEFAULT_STREAK,
                 log_=None):
        self.path = Path(path)
        self.tiers = tiers or TierPolicy()
        self.streak_required = int(streak_required)
        self.log = log_
        self._streaks: dict[str, int] = {}
        self._load()

    # ——— persistence ———

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except Exception:
            log.warning("could not read %s — trust starts from zero",
                        self.path, exc_info=True)
            return
        raw = data.get("streaks") or {}
        self._streaks = {str(k): int(v) for k, v in raw.items()
                         if isinstance(v, (int, float))}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"streaks": self._streaks}, indent=2) + "\n")
        temp.replace(self.path)

    # ——— learning ———

    def record(self, action: str, approved: bool) -> None:
        """Note one decision.

        A rejection resets rather than decrements: the question is whether
        the user *consistently* wants this, and one no answers it.
        """
        action = str(action or "").strip()
        if not action:
            return
        if approved:
            self._streaks[action] = self._streaks.get(action, 0) + 1
        else:
            self._streaks[action] = 0
        self._save()

    def streak(self, action: str) -> int:
        return self._streaks.get(str(action or "").strip(), 0)

    # ——— offering ———

    def _best_tier_for(self, action: str) -> Tier:
        """The lowest tier this action may reach. **Never AUTO if ceilinged.**

        Checked here rather than left to `TierPolicy.set_tier` to raise. An
        offer that cannot be accepted is worse than no offer: the user would
        be asked whether to do something impossible, say yes, and watch
        nothing change.
        """
        return Tier.PROPOSE if is_ceilinged(action) else Tier.AUTO

    def offer_for(self, action: str) -> Offer | None:
        """A promotion worth asking about, or None."""
        action = str(action or "").strip()
        if not action:
            return None
        count = self.streak(action)
        if count < self.streak_required:
            return None

        target = self._best_tier_for(action)
        if self.tiers.tier_for(action) is target:
            return None            # already there; nothing to offer
        return Offer(action=action, tier=target, streak=count)

    def pending_offers(self) -> list[Offer]:
        """Every promotion currently worth asking about.

        The spec says a promotion is "always something you ask me about,
        never something that happens silently". It was not silent — it was in
        `kavach-autonomy status`. But an offer you only see if you happen to
        type a command is one you will never see, which is silence with extra
        steps. This is what puts it on the orb.
        """
        offers = []
        for action in sorted(self._streaks):
            offer = self.offer_for(action)
            if offer is not None:
                offers.append(offer)
        return offers

    def accept(self, action: str) -> Tier:
        """Apply an offer the user agreed to. Returns the tier now in force.

        Goes through `TierPolicy.set_tier`, so the ceiling is enforced a
        second time by the thing that owns it — this module having computed
        a safe target is not a reason to skip the check that guarantees it.
        """
        offer = self.offer_for(action)
        if offer is None:
            raise ValueError(f"nothing to promote for {action!r}")
        self.tiers.set_tier(offer.action, offer.tier)
        if self.log is not None:
            self.log.append("trust.promoted", action=offer.action,
                            tier=offer.tier.value, streak=offer.streak)
        log.info("trust: %s promoted to %s after %d approvals",
                 offer.action, offer.tier.value, offer.streak)
        return offer.tier

    # ——— unlearning ———

    def demote(self, action: str) -> None:
        """Back to ALWAYS_ASK, immediately, no questions.

        The streak is cleared too. Without that, the next approval would
        re-offer at once and demoting would mean nothing.
        """
        action = str(action or "").strip()
        if not action:
            return
        self.tiers.set_tier(action, Tier.ALWAYS_ASK)
        self._streaks[action] = 0
        self._save()
        if self.log is not None:
            self.log.append("trust.demoted", action=action)
        log.info("trust: %s demoted to always_ask", action)


__all__ = ["TrustLedger", "Offer", "DEFAULT_STREAK"]
