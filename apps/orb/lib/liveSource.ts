/**
 * Live `KavachSource` — the real Brain over a WebSocket.
 *
 * This is the payoff for Phase 1's seam: `createMockSource()` is untouched and
 * the HUD is untouched. The orb doesn't know or care which source it has.
 *
 * The bridge (brain/kavach/bridge/server.py) binds to 127.0.0.1 only. It can
 * trigger the kill switch and reports what the machine is hearing, so it has
 * no business being reachable off-host.
 */

import {
  INITIAL_SNAPSHOT,
  type KavachSnapshot,
  type KavachSource,
} from "./kavachState";

export const DEFAULT_BRIDGE_URL = "ws://127.0.0.1:8765";

/** Backoff so a Brain that isn't running yet doesn't hammer the socket. */
const RECONNECT_MIN_MS = 500;
const RECONNECT_MAX_MS = 8000;

export interface LiveSourceOptions {
  url?: string;
  /** Called on every connect/disconnect so the UI can show the truth. */
  onConnectionChange?: (connected: boolean) => void;
}

export function createLiveSource(options: LiveSourceOptions = {}): KavachSource {
  const url = options.url ?? DEFAULT_BRIDGE_URL;
  const listeners = new Set<(snapshot: KavachSnapshot) => void>();

  let socket: WebSocket | null = null;
  let snapshot: KavachSnapshot = { ...INITIAL_SNAPSHOT };
  let reconnectDelay = RECONNECT_MIN_MS;
  let reconnectTimer: number | undefined;
  let stopped = false;

  function emit() {
    const frozen = { ...snapshot, toolCalls: [...snapshot.toolCalls] };
    for (const listener of listeners) listener(frozen);
  }

  function send(payload: Record<string, unknown>) {
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
  }

  function connect() {
    if (stopped) return;
    try {
      socket = new WebSocket(url);
    } catch {
      scheduleReconnect();
      return;
    }

    socket.onopen = () => {
      reconnectDelay = RECONNECT_MIN_MS;
      options.onConnectionChange?.(true);
    };

    socket.onmessage = (event) => {
      try {
        const incoming = JSON.parse(event.data as string) as Partial<KavachSnapshot>;
        // Merge rather than replace: a future bridge version sending a partial
        // update must not blank fields this client already knows.
        snapshot = { ...snapshot, ...incoming } as KavachSnapshot;
        emit();
      } catch {
        // A malformed frame is not worth tearing the connection down for.
      }
    };

    socket.onclose = () => {
      options.onConnectionChange?.(false);
      scheduleReconnect();
    };

    socket.onerror = () => socket?.close();
  }

  function scheduleReconnect() {
    if (stopped) return;
    window.clearTimeout(reconnectTimer);
    reconnectTimer = window.setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(RECONNECT_MAX_MS, reconnectDelay * 2);
  }

  return {
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1) connect();
      listener({ ...snapshot, toolCalls: [...snapshot.toolCalls] });
      return () => listeners.delete(listener);
    },
    halt() {
      // Drives the real Python KillSwitch — the same latch the ⌃⌥⌘K hotkey
      // and the menu bar PANIC item drive. Not a UI-only state.
      send({ cmd: "halt" });
    },
    rearm() {
      send({ cmd: "rearm" });
    },
    interrupt() {
      send({ cmd: "interrupt" });
    },
    pushToTalk(pressed: boolean) {
      send({ cmd: "ptt", pressed });
    },
    answerConfirmation(approved: boolean) {
      send({ cmd: "confirm", answer: approved });
    },
    stop() {
      stopped = true;
      window.clearTimeout(reconnectTimer);
      socket?.close();
      socket = null;
      listeners.clear();
    },
  };
}
