"""`kavach-autonomy` — see and change how much KAVACH does on its own.

Nothing here can widen a rule past the Phase 30 ceiling. `set_tier` raises,
and this prints the refusal rather than swallowing it: a CLI that reported
success for a change it did not make would be the "claimed to have done
something it never did" failure, in the one place where being wrong about
permissions matters most.
"""

from __future__ import annotations

import sys

from ..killswitch.log import ActionLog
from .proposals import ProposalQueue, Status
from .tiers import Tier, TierPolicy, is_ceilinged
from .trust import TrustLedger

RULE = "─" * 64


def _status(tiers: TierPolicy, queue: ProposalQueue, trust: TrustLedger) -> int:
    assignments = tiers.assignments()
    print()
    print(RULE)
    print("  AUTONOMY")
    print(RULE)
    if not assignments:
        print("  Everything is ALWAYS_ASK — the default, and what shipped.")
        print("  Nothing runs unattended until you say so.")
    else:
        for action, tier in sorted(assignments.items()):
            mark = " (ceilinged — can never be AUTO)" if is_ceilinged(action) else ""
            print(f"  {tier.value:<11} {action}{mark}")

    pending = queue.pending()
    print()
    print(f"  queue      {len(pending)} awaiting your review")
    for item in pending[:5]:
        print(f"               {item.id}  {item.description[:48]}")
    if len(pending) > 5:
        print(f"               … and {len(pending) - 5} more")

    offers = [trust.offer_for(a) for a in trust._streaks]
    offers = [o for o in offers if o is not None]
    if offers:
        print()
        print("  KAVACH would stop asking about these, if you want:")
        for offer in offers:
            print(f"    {offer.action} → {offer.tier.value} "
                  f"(approved {offer.streak}x)   "
                  f"kavach-autonomy accept {offer.action}")
    print()
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="kavach-autonomy",
        description="How much KAVACH does without asking (Phases 30-34).")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("status")

    setter = sub.add_parser("set", help="assign a tier to an action type")
    setter.add_argument("action")
    setter.add_argument("tier", choices=[t.value for t in Tier])

    for name, helptext in (("accept", "take a promotion KAVACH offered"),
                           ("demote", "back to ALWAYS_ASK, immediately")):
        one = sub.add_parser(name, help=helptext)
        one.add_argument("action")

    approve = sub.add_parser("approve", help="approve queued proposals")
    approve.add_argument("ids", nargs="+")
    reject = sub.add_parser("reject", help="reject queued proposals")
    reject.add_argument("ids", nargs="+")

    args = parser.parse_args(argv)

    log = ActionLog()
    tiers = TierPolicy(log_=log)
    trust = TrustLedger(tiers=tiers, log_=log)
    queue = ProposalQueue(log_=log, trust=trust)

    if args.cmd in (None, "status"):
        return _status(tiers, queue, trust)

    if args.cmd == "set":
        try:
            tiers.set_tier(args.action, Tier(args.tier))
        except ValueError as exc:
            # Printed, not swallowed. A CLI that reported success for a
            # change it did not make would be the worst possible place for
            # this project's oldest failure mode.
            print(f"\n  ✗ {exc}\n")
            return 2
        print(f"\n  ✓ {args.action} → {args.tier}\n")
        return 0

    if args.cmd == "accept":
        try:
            tier = trust.accept(args.action)
        except ValueError as exc:
            print(f"\n  ✗ {exc}\n")
            return 2
        print(f"\n  ✓ {args.action} → {tier.value}\n")
        return 0

    if args.cmd == "demote":
        trust.demote(args.action)
        print(f"\n  ✓ {args.action} → always_ask\n")
        return 0

    if args.cmd in ("approve", "reject"):
        act = queue.approve if args.cmd == "approve" else queue.reject
        done, failed = 0, []
        for item_id in args.ids:
            try:
                act(item_id)
                done += 1
            except (KeyError, ValueError) as exc:
                failed.append(f"{item_id}: {exc}")
        print(f"\n  {args.cmd}d {done}")
        for line in failed:
            print(f"  ✗ {line}")
        if args.cmd == "approve" and done:
            print("\n  Approved means may be ATTEMPTED. The tool gate still")
            print("  runs when it does — kill switch, confirmation, log.")
        print()
        return 0 if not failed else 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
