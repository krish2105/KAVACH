"use client";

import { AnimatePresence, motion } from "motion/react";
import type { AgentState } from "@/lib/kavachState";

interface Props {
  transcript: string;
  partial: string;
  state: AgentState;
}

export function TranscriptPanel({ transcript, partial, state }: Props) {
  const listening = state === "listening";
  const empty = !transcript && !partial;

  return (
    <section className="glass hud-panel transcript-panel" aria-label="Transcript">
      <header className="panel-head">
        <span className="panel-title">TRANSCRIPT</span>
        {listening && (
          <span className="rec-dot" aria-label="Listening">
            <span className="rec-pulse" aria-hidden="true" />
            REC
          </span>
        )}
      </header>

      {/* aria-live so the transcript is announced rather than silently redrawn. */}
      <div className="transcript-body" aria-live="polite">
        <AnimatePresence mode="wait">
          {empty ? (
            <motion.p
              key="idle"
              className="transcript-idle"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              Say <span className="wake">“KAVACH”</span> to wake, or hold{" "}
              <kbd>Space</kbd>
            </motion.p>
          ) : (
            <motion.p
              key="text"
              className="transcript-text"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.22 }}
            >
              {transcript}
              {partial && <span className="transcript-partial">{partial}</span>}
              {listening && <span className="caret" aria-hidden="true" />}
            </motion.p>
          )}
        </AnimatePresence>
      </div>
    </section>
  );
}
