"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * The 🛡 menu, rebuilt inside the panel.
 *
 * The real menu bar item never attaches when the overlay runs from its app
 * bundle — created, reports a live button, absent from the bar, three attempts
 * and it did not move. So the menu lives here instead, and deliberately looks
 * and behaves like the one it replaces: a small shield you click, and the full
 * menu floats over the orb.
 *
 * It replaced a collapsed `CONTROLS ▸` row, which was a mistake worth naming:
 * controls you have to expand to discover read as controls that are missing,
 * and the expanded block was tall enough to force the whole panel to 560pt.
 * A button costs one corner; the menu costs nothing until you open it.
 *
 * ## Why this renders through a portal
 *
 * Because the menu is the only way back from a size you cannot read, and it
 * was reachable only by accident.
 *
 * Measured at 280pt (the Small size), eight of the twelve items could not be
 * clicked: `Medium`, `Large` and `Huge` sat *underneath* the GESTURES/±/RESET
 * buttons, and five more fell off the bottom of a 280pt panel. Choosing Small
 * therefore removed every control that could undo it — and the global hotkeys
 * are dead without Input Monitoring, so there was no second way out. The one
 * item that still happened to be clickable was `Full screen`, which is exactly
 * what got clicked four seconds later.
 *
 * The z-index looked right and did nothing: this lived inside a `.hud` element
 * that is `position: fixed; z-index: 20`, which opens a stacking context, so
 * `z-index: 40` only ever ranked it against its own siblings. The later `.hud`
 * block painted on top no matter how high that number went.
 *
 * A portal to `document.body` takes it out of that context entirely, so the
 * menu is ranked against the page rather than against its neighbours. Paired
 * with `max-height`/`overflow-y` in the stylesheet, every item is reachable at
 * every size — verified by hit-testing each one, not by looking at it.
 */

interface Props {
  ghost: boolean;
  gestures: boolean;
  appControl: boolean;
  onAppControl: (on: boolean) => void;
  killSwitch: "armed" | "disarmed";
  onBrainCommand: (payload: Record<string, unknown>) => void;
}

type PanelMessage = { cmd: string; value?: string | boolean };

function toOverlay(message: PanelMessage) {
  const w = window as unknown as {
    webkit?: { messageHandlers?: { kavach?: { postMessage: (m: unknown) => void } } };
  };
  // Absent in a browser tab, which is the normal case while developing — the
  // window actions do nothing there rather than throwing.
  w.webkit?.messageHandlers?.kavach?.postMessage(message);
}

const SIZES = [
  ["small", "Small"],
  ["medium", "Medium"],
  ["large", "Large"],
  ["huge", "Huge"],
] as const;

export function ControlPanel({
  ghost,
  gestures,
  killSwitch,
  appControl,
  onAppControl,
  onBrainCommand,
}: Props) {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const halted = killSwitch === "disarmed";

  // `document` does not exist while this renders on the server, so the portal
  // can only be created after mount.
  useEffect(() => setMounted(true), []);

  // Close on Escape or a click elsewhere, like a real menu. Without this the
  // menu stays over the orb until you find the shield again.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const close = () => setOpen(false);

  const shield = (
    <div className="shield-root" ref={rootRef}>
      <button
        type="button"
        className={`shield-btn${open ? " is-open" : ""}${ghost ? " is-ghost" : ""}${
          halted ? " is-halted" : ""
        }`}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label="KAVACH controls"
        title="KAVACH controls"
      >
        🛡
      </button>

      {open && (
        <div className="shield-menu" role="menu">
          <p className="shield-head">KAVACH</p>

          <button className="shield-item" role="menuitem"
                  onClick={() => { onBrainCommand({ cmd: "ghost", on: !ghost, source: "panel" }); close(); }}>
            {ghost ? "👻 Resume listening" : "👻 Ghost mode"}
          </button>

          {/* The G key's replacement.
              G toggles gestures in a browser tab and is a deliberate no-op in
              the panel — the camera lives in the presence process there, not
              in this page — and no keydown reaches a non-activating panel
              anyway. So the only way to turn the camera off was Ghost mode,
              which also stops the microphone. */}
          <button className={`shield-item${gestures ? " is-armed" : ""}`} role="menuitem"
                  onClick={() => { toOverlay({ cmd: "gestures", value: !gestures }); close(); }}>
            {gestures ? "👁 Gestures — on" : "👁 Gestures — off"}
          </button>

          <button className={`shield-item${appControl ? " is-armed" : ""}`} role="menuitem"
                  onClick={() => { onAppControl(!appControl); close(); }}>
            {appControl ? "✋ App control — armed" : "✋ App control — off"}
          </button>

          <div className="shield-sep" />

          {SIZES.map(([value, label]) => (
            <button key={value} className="shield-item" role="menuitem"
                    onClick={() => { toOverlay({ cmd: "size", value }); close(); }}>
              {label}
            </button>
          ))}

          <div className="shield-sep" />

          <button className="shield-item" role="menuitem"
                  onClick={() => { toOverlay({ cmd: "fullscreen" }); close(); }}>
            Full screen ⌃⌥⌘F
          </button>
          <button className="shield-item" role="menuitem"
                  onClick={() => { toOverlay({ cmd: "interactive" }); close(); }}>
            Move / resize ⌃⌥⌘M
          </button>
          <button className="shield-item" role="menuitem"
                  onClick={() => { toOverlay({ cmd: "minimise" }); close(); }}>
            Minimise ⌃⌥⌘H
          </button>
          <button className="shield-item" role="menuitem"
                  onClick={() => { toOverlay({ cmd: "reset" }); close(); }}>
            Reset position
          </button>

          <div className="shield-sep" />

          {/* Apart from the rest, and last: it latches, and only a human at
              this Mac re-arms it. It should not look like "Medium". */}
          <button className="shield-item is-danger" role="menuitem"
                  onClick={() => { onBrainCommand({ cmd: halted ? "rearm" : "halt" }); close(); }}>
            {halted ? "⛔ Re-arm" : "⛔ Kill switch"}
          </button>
          <button className="shield-item is-quiet" role="menuitem"
                  onClick={() => { toOverlay({ cmd: "quit" }); close(); }}>
            Quit orb
          </button>
        </div>
      )}
    </div>
  );

  return mounted ? createPortal(shield, document.body) : null;
}
