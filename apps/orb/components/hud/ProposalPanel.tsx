"use client";

import type { Proposal, TrustOffer } from "@/lib/kavachState";

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
export function ProposalPanel({
  proposals,
  trustOffers = [],
}: {
  proposals: Proposal[];
  trustOffers?: TrustOffer[];
}) {
  const pending = proposals.filter((p) => p.status === "pending");
  if (pending.length === 0 && trustOffers.length === 0) return null;

  return (
    <section className="hud-panel proposal-panel" aria-live="polite">
      {pending.length > 0 && (
        <header className="proposal-head">
          <span className="proposal-count">{pending.length}</span>
          <span className="proposal-title">
            queued for review — nothing has run
          </span>
        </header>
      )}

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

      {trustOffers.length > 0 && (
        <div className="trust-offers">
          <p className="trust-offers-head">KAVACH would stop asking about:</p>
          <ul className="proposal-list">
            {trustOffers.map((offer) => (
              <li key={offer.action} className="proposal-item">
                <span className="proposal-action">{offer.action}</span>
                <span className="proposal-description">
                  → {offer.tier} · approved {offer.streak}&times;
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="proposal-how">
        review: <code>kavach-autonomy</code>
      </p>
    </section>
  );
}
