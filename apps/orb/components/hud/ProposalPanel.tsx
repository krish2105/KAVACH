"use client";

import type { Proposal } from "@/lib/kavachState";

/**
 * Phase 33 — what is waiting for your review, where you can see it.
 *
 * The API and the CLI could both show the queue before this could, and the
 * orb is the thing actually on screen. A queue you have to remember to go and
 * check is a queue that fills up.
 *
 * **Nothing here has run.** A PROPOSE-tier action is queued *instead of*
 * executing, and there is no auto-execute timeout — an unreviewed proposal
 * sits, or expires unexecuted. The panel says so in as many words, because a
 * list of pending destructive actions is exactly the place someone assumes
 * the opposite.
 *
 * Deliberately read-only. Approving from the orb would mean a click could
 * authorise a destructive action, and a click has no speaker verification
 * behind it — the CLI and the token-gated API are the review surfaces. This
 * one tells you there is something to review.
 */
export function ProposalPanel({ proposals }: { proposals: Proposal[] }) {
  const pending = proposals.filter((p) => p.status === "pending");
  if (pending.length === 0) return null;

  return (
    <section className="hud-panel proposal-panel" aria-live="polite">
      <header className="proposal-head">
        <span className="proposal-count">{pending.length}</span>
        <span className="proposal-title">
          queued for review — nothing has run
        </span>
      </header>

      <ul className="proposal-list">
        {pending.slice(0, 4).map((item) => (
          <li key={item.id} className="proposal-item">
            <span className="proposal-action">{item.action}</span>
            <span className="proposal-description">{item.description}</span>
          </li>
        ))}
      </ul>

      {pending.length > 4 && (
        <p className="proposal-more">
          and {pending.length - 4} more
        </p>
      )}

      <p className="proposal-how">
        review: <code>kavach-autonomy</code>
      </p>
    </section>
  );
}
