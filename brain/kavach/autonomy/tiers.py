"""Phase 30 — how much autonomy each action gets, and the ceiling on it.

Three tiers:

* ``AUTO`` — runs with no approval.
* ``PROPOSE`` — queued for batch review (Phase 33). Never executes unattended.
* ``ALWAYS_ASK`` — interrupts immediately. The Phase 4 behaviour, and the
  default for everything until deliberately reclassified.

**The ceiling is the point of this module.** An action that sends, deletes,
purchases or changes a system setting can never be ``AUTO`` — not through
config, not through a future phase, not through Phase 34's pattern-learned
trust however many times it has been approved. It is enforced here, in code, so
there is no file to edit that removes it.

That matters more than it looks, because the obvious way around a code rule is
the file the code reads. So the file is not trusted either: an ``auto`` written
into `autonomy.json` by hand — or by a future phase, or by an agent that read a
hostile web page — is read back as ``ALWAYS_ASK``. The ceiling applies on load
as well as on assignment, and `test_the_ceiling_survives_a_hand_edited_config`
fails the build if that stops being true.

This is the same shape as the rules that already hold in this project: the kill
switch latches rather than auto-recovering, `permission_mode` is documented as
"not a tunable", and denial is the default at every branch of the tool gate. A
ceiling that can be configured away is a default wearing a costume.

**Matching is on the verb, not the subsystem.** `anything.delete_thing` is
ceilinged the day it appears, without anyone remembering to add it — the same
reasoning that made `Policy.action_text` read the tool *name* after
`delete_file` slipped through with only a path for an argument.
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from pathlib import Path

log = logging.getLogger("kavach.autonomy.tiers")

DEFAULT_PATH = Path.home() / ".kavach" / "autonomy.json"


class Tier(str, Enum):
    #: Runs with no approval.
    AUTO = "auto"
    #: Queued for review. Nothing executes until the user acts on it.
    PROPOSE = "propose"
    #: Interrupts immediately. The default, and the only default.
    ALWAYS_ASK = "always_ask"


#: Verbs that can never reach ``AUTO``.
#:
#: Drawn from §7's list — sending, deleting, purchasing, changing a system
#: setting — plus the shell, because a shell command is every one of those and
#: names none of them.
#:
#: Matched as whole words anywhere in the action name, so a server that ships
#: `calendar.send_invite` tomorrow is covered without an edit. Over-matching
#: here costs a confirmation; under-matching costs the thing the ceiling
#: exists to prevent.
CEILINGED = (
    "send", "email", "reply", "forward", "post", "publish", "share",
    "delete", "remove", "trash", "erase", "destroy", "drop",
    "purchase", "buy", "pay", "order", "checkout",
    "setting", "settings", "preference", "config",
    "shell", "exec", "execute", "eval", "sudo", "install", "uninstall",
    "shutdown", "restart", "logout", "format", "wipe",
)

_WORDS = re.compile(r"[a-z0-9]+")


def is_ceilinged(action: str | None) -> bool:
    """Whether `action` may never be AUTO.

    Splits on non-alphanumerics so `file.delete`, `mail_send` and
    `deleteNote` all match, and a bare `read` does not.
    """
    if not action:
        # An action with no name cannot be reasoned about, so it gets the
        # most restrictive answer available.
        return True
    words = set(_WORDS.findall(str(action).lower()))
    return bool(words & set(CEILINGED))


class TierPolicy:
    """Which tier each action type has. Persisted, logged, ceilinged."""

    def __init__(self, path: Path | str = DEFAULT_PATH, log_=None):
        self.path = Path(path)
        self.log = log_
        self._tiers: dict[str, Tier] = {}
        self._load()

    # ——— persistence ———

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except Exception:
            # A corrupt policy file must not read as "everything is allowed".
            log.warning("could not read %s — every action stays ALWAYS_ASK",
                        self.path, exc_info=True)
            return

        for action, name in (data.get("tiers") or {}).items():
            try:
                tier = Tier(str(name).lower())
            except ValueError:
                log.warning("unknown tier %r for %r — leaving it ALWAYS_ASK",
                            name, action)
                continue
            # **The ceiling applies on load, not only on assignment.** The
            # file is the obvious way around a code rule; a hand-edited AUTO
            # for a destructive action is refused here rather than trusted.
            if tier is Tier.AUTO and is_ceilinged(action):
                log.warning(
                    "%s is set to AUTO in %s, which is not permitted — "
                    "reading it as ALWAYS_ASK", action, self.path)
                if self.log is not None:
                    self.log.append("autonomy.refused", action=action,
                                    tier="auto", source="config file",
                                    reason="ceilinged action cannot be AUTO")
                continue
            self._tiers[action] = tier

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tiers": {a: t.value for a, t in sorted(self._tiers.items())}}
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n")
        temp.replace(self.path)          # atomic: never a half file

    # ——— the decision ———

    def tier_for(self, action: str | None) -> Tier:
        """The tier for `action`. Unknown means ALWAYS_ASK, always."""
        if not action or not str(action).strip():
            return Tier.ALWAYS_ASK
        return self._tiers.get(str(action).strip(), Tier.ALWAYS_ASK)

    def set_tier(self, action: str, tier: Tier) -> None:
        """Assign a tier, or raise if the ceiling forbids it.

        Raises rather than silently downgrading: a caller that asked for AUTO
        and got PROPOSE without being told would report the wrong thing to
        the user, and Phase 34 would learn from a promotion that never
        happened.
        """
        action = str(action or "").strip()
        if not action:
            raise ValueError("an action type needs a name to be classified")
        tier = Tier(tier)

        if tier is Tier.AUTO and is_ceilinged(action):
            if self.log is not None:
                self.log.append("autonomy.refused", action=action,
                                tier=tier.value, source="set_tier",
                                reason="sends, deletes, buys or changes a "
                                       "system setting — can never be AUTO")
            raise ValueError(
                f"{action!r} sends, deletes, buys or changes a system "
                f"setting, so it can never be AUTO. PROPOSE is the most it "
                f"can be given."
            )

        previous = self.tier_for(action)
        self._tiers[action] = tier
        self._save()
        if self.log is not None:
            self.log.append("autonomy.tier", action=action, tier=tier.value,
                            previous=previous.value)
        log.info("autonomy: %s %s → %s", action, previous.value, tier.value)

    # ——— reporting ———

    def assignments(self) -> dict[str, Tier]:
        """Everything deliberately classified. Absent means ALWAYS_ASK."""
        return dict(self._tiers)


__all__ = ["Tier", "TierPolicy", "CEILINGED", "is_ceilinged", "DEFAULT_PATH"]
