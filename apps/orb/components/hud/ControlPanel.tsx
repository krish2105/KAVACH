"use client";

import { useEffect, useRef, useState } from "react";

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
 */

interface Props {
  ghost: boolean;
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
  killSwitch,
  appControl,
  onAppControl,
  onBrainCommand,
}: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const halted = killSwitch === "disarmed";

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

  return (
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
}
