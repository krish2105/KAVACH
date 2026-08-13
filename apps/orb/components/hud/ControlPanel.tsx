"use client";

import { useState } from "react";

/**
 * The controls that used to live in the menu bar.
 *
 * The 🛡 status item never attaches when the overlay runs from its app bundle
 * — it is created, reports a live button, logs success, and is absent from the
 * bar. Rather than keep chasing that, everything it carried lives here, where
 * it is visible by construction: if you can see the orb you can reach its
 * controls.
 *
 * Two different destinations, which is why there are two senders:
 *
 * - **Window actions** (size, full screen, minimise, quit) belong to the
 *   process that owns the panel, and reach it through a WebKit script message
 *   handler. A websocket round trip to the brain cannot resize a window the
 *   brain does not own.
 * - **Ghost mode and the kill switch** belong to the brain, and go over the
 *   bridge that already carries every other command.
 */

interface Props {
  ghost: boolean;
  /** True while hand control may drive other applications. */
  appControl: boolean;
  onAppControl: (on: boolean) => void;
  killSwitch: "armed" | "disarmed";
  /** Sends a command to the brain over the bridge. */
  onBrainCommand: (payload: Record<string, unknown>) => void;
}

type PanelMessage = { cmd: string; value?: string };

function toOverlay(message: PanelMessage) {
  const w = window as unknown as {
    webkit?: { messageHandlers?: { kavach?: { postMessage: (m: unknown) => void } } };
  };
  // Absent in a browser tab, which is the normal case while developing — the
  // window controls simply do nothing there rather than throwing.
  w.webkit?.messageHandlers?.kavach?.postMessage(message);
}

const SIZES = ["small", "medium", "large", "huge"] as const;

export function ControlPanel({
  ghost,
  killSwitch,
  appControl,
  onAppControl,
  onBrainCommand,
}: Props) {
  const [open, setOpen] = useState(false);
  const halted = killSwitch === "disarmed";

  return (
    <section className={`glass hud-panel control-panel${open ? " is-open" : ""}`}
             aria-label="KAVACH controls">
      <button
        type="button"
        className="control-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="panel-title">CONTROLS</span>
        <span className="control-chevron" aria-hidden="true">{open ? "▾" : "▸"}</span>
      </button>

      {open && (
        <div className="control-body">
          {/* Ghost first: it is the one you reach for in a hurry, and the one
              whose absence from the menu bar was most keenly felt. */}
          <button
            type="button"
            className={`control-btn is-wide${ghost ? " is-active" : ""}`}
            onClick={() => onBrainCommand({ cmd: "ghost", on: !ghost, source: "panel" })}
          >
            {ghost ? "👻 RESUME LISTENING" : "👻 GHOST MODE"}
          </button>

          {/* Off at every launch, never persisted. Arming something that
              drives your other applications should be a decision you remember
              making today — and it says which apps it may touch, because "on"
              is not the same as "on for everything". */}
          <button
            type="button"
            className={`control-btn is-wide${appControl ? " is-active is-armed" : ""}`}
            onClick={() => onAppControl(!appControl)}
            title="Scroll and zoom the frontmost app with your hand. Allowlisted apps only."
          >
            {appControl ? "✋ APP CONTROL — ARMED" : "✋ APP CONTROL — OFF"}
          </button>

          <div className="control-row">
            {SIZES.map((size) => (
              <button
                key={size}
                type="button"
                className="control-btn"
                onClick={() => toOverlay({ cmd: "size", value: size })}
              >
                {size[0].toUpperCase()}
              </button>
            ))}
          </div>

          <div className="control-row">
            <button type="button" className="control-btn"
                    onClick={() => toOverlay({ cmd: "fullscreen" })}>
              FULL SCREEN
            </button>
            <button type="button" className="control-btn"
                    onClick={() => toOverlay({ cmd: "interactive" })}>
              MOVE
            </button>
          </div>

          <div className="control-row">
            <button type="button" className="control-btn"
                    onClick={() => toOverlay({ cmd: "minimise" })}>
              MINIMISE
            </button>
            <button type="button" className="control-btn"
                    onClick={() => toOverlay({ cmd: "reset" })}>
              RECENTRE
            </button>
          </div>

          {/* The kill switch is deliberately last, styled apart, and says what
              it costs: it latches, and only a human at this Mac re-arms it. */}
          <button
            type="button"
            className={`control-btn is-wide is-danger${halted ? " is-active" : ""}`}
            onClick={() => onBrainCommand({ cmd: halted ? "rearm" : "halt" })}
          >
            {halted ? "⛔ RE-ARM" : "⛔ KILL SWITCH"}
          </button>

          <button type="button" className="control-btn is-wide is-quiet"
                  onClick={() => toOverlay({ cmd: "quit" })}>
            QUIT ORB
          </button>
        </div>
      )}
    </section>
  );
}
