"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState, type RefObject } from "react";
import type { ToolCall } from "@/lib/kavachState";

/**
 * Tool-call visualization (spec §4 differentiator #2).
 *
 * When the Brain dispatches an MCP tool call, a glowing packet travels from
 * the orb's core out along a HUD line to the tool-call panel; when the result
 * comes back, another travels the other way, coloured by outcome. It makes
 * the agent loop *visible* instead of abstract — §11 calls it the best moment
 * in the demo video, and it costs almost nothing to run.
 *
 * Phase 1 drives this from the mock source. Phase 4 emits real events into
 * exactly the same props.
 */

type Direction = "out" | "back";

interface Packet {
  key: string;
  direction: Direction;
  /** Tracks the tool call's status so the return trip is coloured by outcome. */
  tone: "pending" | "ok" | "error" | "denied";
}

interface Props {
  toolCalls: ToolCall[];
  /** The panel the packets fly to — the tool-call log. */
  targetRef: RefObject<HTMLElement | null>;
}

interface Geometry {
  d: string;
  from: { x: number; y: number };
}

export function ToolCallPackets({ toolCalls, targetRef }: Props) {
  const reduceMotion = useReducedMotion();
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const [packets, setPackets] = useState<Packet[]>([]);
  const seenRef = useRef<Map<string, string>>(new Map());

  // ——— geometry: core (viewport centre) → panel edge ———
  useEffect(() => {
    function measure() {
      const target = targetRef.current;
      if (!target) return;
      const rect = target.getBoundingClientRect();

      // The orb's core sits at the centre of the canvas, which fills the view.
      const from = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
      // Aim at the panel's leading edge, vertically centred.
      const to = { x: rect.left, y: rect.top + rect.height / 2 };

      // A single quadratic bend, bowed upward, so the packet arcs rather than
      // sliding along a straight line — reads as travel, not as a progress bar.
      const cx = (from.x + to.x) / 2;
      const cy = Math.min(from.y, to.y) - Math.abs(to.x - from.x) * 0.18;

      setGeometry({
        d: `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`,
        from,
      });
    }

    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [targetRef]);

  // ——— spawn packets on dispatch and on resolution ———
  useEffect(() => {
    if (reduceMotion) return;

    const seen = seenRef.current;
    const spawned: Packet[] = [];

    for (const call of toolCalls) {
      const previous = seen.get(call.id);

      if (previous === undefined) {
        // First sighting: the outbound trip.
        spawned.push({ key: `${call.id}-out`, direction: "out", tone: "pending" });
      } else if (previous !== call.status && call.status !== "pending") {
        // Settled: the return trip, coloured by what actually happened.
        spawned.push({
          key: `${call.id}-back-${call.status}`,
          direction: "back",
          tone:
            call.status === "ok"
              ? "ok"
              : call.status === "error"
                ? "error"
                : call.status === "denied"
                  ? "denied"
                  : "pending",
        });
      }
      seen.set(call.id, call.status);
    }

    // Drop bookkeeping for calls that have aged out of the log.
    const live = new Set(toolCalls.map((c) => c.id));
    for (const id of seen.keys()) if (!live.has(id)) seen.delete(id);

    if (spawned.length === 0) return;
    setPackets((current) => [...current, ...spawned]);

    const timer = window.setTimeout(() => {
      const keys = new Set(spawned.map((p) => p.key));
      setPackets((current) => current.filter((p) => !keys.has(p.key)));
    }, 1400);
    return () => window.clearTimeout(timer);
  }, [toolCalls, reduceMotion]);

  if (!geometry || reduceMotion) return null;

  return (
    <div className="packet-layer" aria-hidden="true">
      <svg className="packet-svg">
        <AnimatePresence>
          {packets.length > 0 && (
            <motion.path
              key="wire"
              d={geometry.d}
              className="packet-wire"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
            />
          )}
        </AnimatePresence>
      </svg>

      <AnimatePresence>
        {packets.map((packet) => (
          <motion.span
            key={packet.key}
            className={`packet packet-${packet.tone}`}
            style={{ offsetPath: `path("${geometry.d}")` }}
            initial={{
              offsetDistance: packet.direction === "out" ? "0%" : "100%",
              opacity: 0,
              scale: 0.4,
            }}
            animate={{
              offsetDistance: packet.direction === "out" ? "100%" : "0%",
              opacity: [0, 1, 1, 0],
              scale: [0.4, 1, 1, 0.5],
            }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.9, ease: "easeInOut" }}
          />
        ))}
      </AnimatePresence>
    </div>
  );
}
