/**
 * The contract between the Presence layer and everything behind it.
 *
 * The Brain (spec §5) does not exist yet — it is Phase 3. So Phase 1 defines
 * the shape it will eventually publish and ships a mock that produces it. The
 * HUD only ever talks to a `KavachSource`, which means Phase 3 adds a
 * WebSocket implementation of this same interface and deletes nothing.
 *
 * Getting this seam right now is what stops Phase 3 from becoming a rewrite.
 */

import type { OrbState } from "./orbScene";

export type AgentState = OrbState;

/** Which reasoning path handled the turn — the confidence proxy from §4 #3. */
export type RouteTier = "local" | "claude";

export type ToolCallStatus = "pending" | "awaiting-confirmation" | "ok" | "error" | "denied";

export interface ToolCall {
  id: string;
  /** MCP server name, matching hands/mcp.config.json. */
  server: string;
  tool: string;
  /** Human-readable one-liner — what KAVACH would speak back. */
  summary: string;
  status: ToolCallStatus;
  startedAt: number;
  endedAt?: number;
}

export type KillSwitchState = "armed" | "disarmed";

export interface KavachSnapshot {
  state: AgentState;
  /** Finalised transcript of the current turn. */
  transcript: string;
  /** In-progress partial from STT, shown dimmer than the final text. */
  partial: string;
  /** Mic level while listening, TTS envelope while speaking (0–1). */
  amplitude: number;
  /** 0–1, drives outer shell opacity. */
  confidence: number;
  route: RouteTier | null;
  /** Newest first. */
  toolCalls: ToolCall[];
  killSwitch: KillSwitchState;
  /**
   * Ghost mode (§14): every input suspended — mic, camera, action logging.
   *
   * Rendered as an unmistakable dead state rather than a subtle cue. Whether
   * KAVACH is listening is the one thing about it that must never be
   * ambiguous, so this deliberately overrides the look of every other state.
   */
  ghost: boolean;
  /**
   * §13 — why the router chose this path, in one line.
   *
   * The router's own explanation of the *routing decision* ("simple intent
   * (clock)", "open-ended reasoning"), not a rationale about the answer. It
   * is already written to the action log; this is the same string, shown
   * where you are actually looking.
   */
  reason: string;
  /** What the router thought you wanted, when it could name it. */
  intent: string;
}

export interface KavachSource {
  subscribe(listener: (snapshot: KavachSnapshot) => void): () => void;
  /** Latch the kill switch. The live source drives the real Python latch. */
  halt(): void;
  rearm(): void;
  /** Cancel the current turn without latching (§5 — Esc / spoken "stop"). */
  interrupt?(): void;
  /** Push-to-talk override (§4). Only the live source can act on it. */
  pushToTalk?(pressed: boolean): void;
  /**
   * Start a turn that ends on silence, with no key held.
   *
   * The floating panel is non-activating, so a keydown handler in the page can
   * never fire there, and the global-hotkey alternative needs an Input
   * Monitoring grant the process may not have. A button in the panel needs
   * neither.
   */
  startTurn?(): void;
  /** A held thumbs-up/down answering a pending confirmation (§7). */
  answerConfirmation?(approved: boolean): void;
  stop(): void;
}

export const INITIAL_SNAPSHOT: KavachSnapshot = {
  state: "boot",
  transcript: "",
  partial: "",
  amplitude: 0,
  confidence: 1,
  route: null,
  toolCalls: [],
  killSwitch: "armed",
  ghost: false,
  reason: "",
  intent: "",
};

// ───────────────────────────────────────────────────────────────
// Mock source
// ───────────────────────────────────────────────────────────────

interface ScriptedBeat {
  state: AgentState;
  ms: number;
  transcript?: string;
  /** Cleared explicitly when a beat finalises the text. */
  partial?: string;
  /** Typed out character by character to mimic streaming STT. */
  streamPartial?: string;
  /** `null` clears the route tag when a turn ends. */
  route?: RouteTier | null;
  confidence?: number;
  /** Tool calls dispatched when this beat starts. */
  dispatch?: Omit<ToolCall, "id" | "startedAt" | "status">[];
  /** Resolve all pending tool calls with this status. */
  resolve?: ToolCallStatus;
}

/**
 * One full turn, chosen to exercise every visual state the orb can enter —
 * including a destructive action that has to be confirmed (§7), because the
 * guardrail is part of the demo, not a footnote.
 */
const SCRIPT: ScriptedBeat[] = [
  { state: "idle", ms: 2600, transcript: "", partial: "" },
  {
    state: "listening",
    ms: 3200,
    streamPartial: "kavach, what's on my calendar tomorrow?",
  },
  {
    state: "thinking",
    ms: 1500,
    transcript: "KAVACH, what's on my calendar tomorrow?",
    partial: "",
    route: "claude",
    confidence: 0.55,
  },
  {
    state: "acting",
    ms: 2400,
    dispatch: [
      {
        server: "macos-automator",
        tool: "execute_script",
        summary: "Read tomorrow's events from Calendar",
      },
    ],
  },
  { state: "acting", ms: 900, resolve: "ok" },
  {
    state: "speaking",
    ms: 3400,
    transcript: "Three events tomorrow. The first is a 9am standup.",
    confidence: 0.92,
  },
  { state: "idle", ms: 2200, transcript: "", route: null },
  {
    state: "listening",
    ms: 2600,
    streamPartial: "delete the draft in notes",
  },
  {
    state: "thinking",
    ms: 1200,
    transcript: "Delete the draft in Notes.",
    partial: "",
    route: "local",
    confidence: 0.78,
  },
  {
    // Destructive → KAVACH speaks it back and waits rather than acting.
    state: "speaking",
    ms: 3000,
    transcript: "That deletes a note permanently. Say confirm, or press Space.",
    dispatch: [
      {
        server: "macos-automator",
        tool: "execute_script",
        summary: "Delete note “Draft” — awaiting your confirmation",
      },
    ],
  },
  { state: "idle", ms: 2400, resolve: "denied", transcript: "" },
];

export function createMockSource(): KavachSource {
  let snapshot: KavachSnapshot = { ...INITIAL_SNAPSHOT, state: "idle" };
  const listeners = new Set<(s: KavachSnapshot) => void>();

  let beatIndex = 0;
  let beatStartedAt = 0;
  let rafId = 0;
  let stopped = false;
  let seq = 0;

  function emit() {
    const frozen = { ...snapshot, toolCalls: [...snapshot.toolCalls] };
    for (const listener of listeners) listener(frozen);
  }

  function enterBeat(index: number, now: number) {
    const beat = SCRIPT[index];
    beatStartedAt = now;

    snapshot.state = beat.state;
    if (beat.transcript !== undefined) snapshot.transcript = beat.transcript;
    if (beat.partial !== undefined) snapshot.partial = beat.partial;
    if (beat.route !== undefined) snapshot.route = beat.route;
    if (beat.confidence !== undefined) snapshot.confidence = beat.confidence;

    if (beat.dispatch) {
      for (const call of beat.dispatch) {
        const status: ToolCallStatus = call.summary.includes("confirmation")
          ? "awaiting-confirmation"
          : "pending";
        const dispatched: ToolCall = {
          ...call,
          id: `call-${++seq}`,
          startedAt: now,
          status,
        };
        snapshot.toolCalls = [dispatched, ...snapshot.toolCalls].slice(0, 12);
      }
    }

    if (beat.resolve) {
      snapshot.toolCalls = snapshot.toolCalls.map((call) =>
        call.status === "pending" || call.status === "awaiting-confirmation"
          ? { ...call, status: beat.resolve!, endedAt: now }
          : call,
      );
    }
  }

  function frame(now: number) {
    if (stopped) return;
    rafId = requestAnimationFrame(frame);

    // Halted freezes the script exactly where it was — no auto-recovery,
    // mirroring the real kill switch's latch.
    if (snapshot.killSwitch === "disarmed") {
      snapshot.amplitude = 0;
      emit();
      return;
    }

    const beat = SCRIPT[beatIndex];
    const elapsed = now - beatStartedAt;

    // Amplitude only means something while there is audio.
    snapshot.amplitude =
      beat.state === "listening" || beat.state === "speaking"
        ? Math.min(
            1,
            Math.abs(Math.sin(now / 190)) * 0.55 +
              Math.abs(Math.sin(now / 70)) * 0.35 +
              Math.random() * 0.1,
          )
        : 0;

    // Stream the partial transcript in, character by character.
    if (beat.streamPartial) {
      const ratio = Math.min(1, elapsed / (beat.ms * 0.75));
      snapshot.partial = beat.streamPartial.slice(
        0,
        Math.floor(beat.streamPartial.length * ratio),
      );
    }

    if (elapsed >= beat.ms) {
      beatIndex = (beatIndex + 1) % SCRIPT.length;
      enterBeat(beatIndex, now);
    }

    emit();
  }

  return {
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1) {
        beatStartedAt = performance.now();
        enterBeat(0, beatStartedAt);
        rafId = requestAnimationFrame(frame);
      }
      listener({ ...snapshot, toolCalls: [...snapshot.toolCalls] });
      return () => listeners.delete(listener);
    },
    halt() {
      snapshot.killSwitch = "disarmed";
      snapshot.state = "halted";
      snapshot.partial = "";
      snapshot.amplitude = 0;
      // In-flight work is cancelled, exactly as KillSwitch.trigger does.
      snapshot.toolCalls = snapshot.toolCalls.map((call) =>
        call.status === "pending" || call.status === "awaiting-confirmation"
          ? { ...call, status: "denied", endedAt: performance.now() }
          : call,
      );
      emit();
    },
    rearm() {
      snapshot.killSwitch = "armed";
      snapshot.state = "idle";
      beatIndex = 0;
      beatStartedAt = performance.now();
      enterBeat(0, beatStartedAt);
      emit();
    },
    stop() {
      stopped = true;
      cancelAnimationFrame(rafId);
      listeners.clear();
    },
  };
}

export const STATE_LABEL: Record<AgentState, string> = {
  boot: "INITIALISING",
  idle: "IDLE",
  listening: "LISTENING",
  thinking: "THINKING",
  acting: "ACTING",
  speaking: "SPEAKING",
  halted: "HALTED",
};
