"use client";

import { AnimatePresence, motion } from "motion/react";
import type { ToolCall, ToolCallStatus } from "@/lib/kavachState";

const STATUS_GLYPH: Record<ToolCallStatus, string> = {
  pending: "◐",
  "awaiting-confirmation": "⏸",
  ok: "✓",
  error: "✗",
  denied: "⊘",
};

const STATUS_LABEL: Record<ToolCallStatus, string> = {
  pending: "running",
  "awaiting-confirmation": "waiting for you",
  ok: "done",
  error: "failed",
  denied: "denied",
};

interface Props {
  toolCalls: ToolCall[];
}

/**
 * Every MCP tool call, as it happens. This is the visible half of the §7
 * action log — the JSONL on disk is the permanent record, this is the part
 * you can watch. Confirmation-gated calls are styled to stand out, because a
 * paused action is the one the user has to do something about.
 */
export function ToolCallLog({ toolCalls }: Props) {
  return (
    <section className="glass hud-panel toolcall-panel" aria-label="Tool calls">
      <header className="panel-head">
        <span className="panel-title">TOOL CALLS</span>
        <span className="panel-count">{toolCalls.length}</span>
      </header>

      <ul className="toolcall-list">
        <AnimatePresence initial={false}>
          {toolCalls.length === 0 && (
            <motion.li
              key="empty"
              className="toolcall-empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              No tool calls this session
            </motion.li>
          )}

          {toolCalls.map((call) => (
            <motion.li
              key={call.id}
              className={`toolcall status-${call.status}`}
              // Layout animation so existing rows slide down rather than jump
              // when a new call lands on top.
              layout
              initial={{ opacity: 0, x: -14 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 14 }}
              transition={{ duration: 0.2 }}
            >
              <span className="toolcall-glyph" aria-hidden="true">
                {STATUS_GLYPH[call.status]}
              </span>
              <div className="toolcall-body">
                <div className="toolcall-name">
                  <span className="toolcall-server">{call.server}</span>
                  <span className="toolcall-sep">·</span>
                  <span className="toolcall-tool">{call.tool}</span>
                </div>
                <div className="toolcall-summary">{call.summary}</div>
              </div>
              <span className="sr-only">{STATUS_LABEL[call.status]}</span>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </section>
  );
}
