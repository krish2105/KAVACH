"use client";

import { motion } from "motion/react";
import type { AgentState, KillSwitchState, RouteTier } from "@/lib/kavachState";
import { STATE_LABEL } from "@/lib/kavachState";

interface Props {
  state: AgentState;
  route: RouteTier | null;
  confidence: number;
  killSwitch: KillSwitchState;
  amplitude: number;
  ghost: boolean;
}

/** Circumference of the r=13 confidence ring, for the stroke-dash trick. */
const RING_CIRCUMFERENCE = 2 * Math.PI * 13;

export function StatusPanel({ state, route, confidence, killSwitch, amplitude, ghost }: Props) {
  const halted = killSwitch === "disarmed";

  return (
    <section
      className={`glass hud-panel status-panel${halted ? " is-halted" : ""}${
        ghost ? " is-ghost" : ""
      }`}
      aria-label="Agent status"
    >
      <header className="panel-head">
        <span className="panel-title">STATUS</span>
        <span
          className={`kill-badge${halted ? " is-disarmed" : ""}`}
          title={
            halted
              ? "Kill switch latched. Explicit re-arm required."
              : "Kill switch armed"
          }
        >
          {halted ? "⛔ DISARMED" : "🛡 ARMED"}
        </span>
      </header>

      {/* In words, in the panel that is visible in EVERY layout.
          The greyed-out orb already says it, but the overlay caption that
          carried the badge is display:none in the full-window view — so
          outside the floating panel, ghost mode was being signalled by colour
          alone. Colour is exactly what a glance gets wrong, and "is it
          listening?" is the one question that must never need interpreting. */}
      {ghost && (
        <p className="ghost-banner" role="status" aria-live="assertive">
          👻 GHOST MODE — mic and camera off, not listening
        </p>
      )}

      <div className="status-row">
        <span className={`state-pill state-${state}`} role="status" aria-live="polite">
          <span className="state-dot" aria-hidden="true" />
          {STATE_LABEL[state]}
        </span>

        {/* Confidence ring — outer shell opacity mirrors this exact number. */}
        <div
          className="confidence"
          role="img"
          aria-label={`Confidence ${Math.round(confidence * 100)} percent`}
        >
          <svg viewBox="0 0 32 32" width="32" height="32" aria-hidden="true">
            <circle className="conf-track" cx="16" cy="16" r="13" />
            <motion.circle
              className="conf-value"
              cx="16"
              cy="16"
              r="13"
              strokeDasharray={RING_CIRCUMFERENCE}
              // Motion needs a concrete starting value; without `initial` it
              // reads `undefined` off the DOM and refuses to animate.
              initial={{ strokeDashoffset: RING_CIRCUMFERENCE }}
              animate={{
                strokeDashoffset: RING_CIRCUMFERENCE * (1 - (halted ? 0 : confidence)),
              }}
              transition={{ type: "spring", stiffness: 90, damping: 20 }}
            />
          </svg>
          <span className="conf-num">{halted ? "––" : Math.round(confidence * 100)}</span>
        </div>
      </div>

      <div className="status-meta">
        <span className="meta-label">ROUTE</span>
        <span className={`route-tag${route ? ` route-${route}` : ""}`}>
          {route === "claude" ? "CLAUDE" : route === "local" ? "LOCAL" : "—"}
        </span>

        {/* Level meter: transform-only, so it stays cheap every frame. */}
        <div className="level" aria-hidden="true">
          <div
            className="level-fill"
            style={{ transform: `scaleX(${halted ? 0 : amplitude})` }}
          />
        </div>
      </div>
    </section>
  );
}
