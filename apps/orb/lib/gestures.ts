/**
 * Gesture classification for silent confirmation (spec §7 extension).
 *
 * Confirming a destructive action out loud is awkward in an open office — and
 * "yes" is exactly what someone else in earshot can also say. A held
 * thumbs-up/thumbs-down lets you answer without speaking.
 *
 * Two properties matter more than accuracy here:
 *
 * 1. **A gesture must be HELD.** A hand passing through a thumbs-up shape on
 *    its way somewhere else must never authorise a delete. `HOLD_MS` of
 *    continuous detection is required, and the orb shows a filling ring so the
 *    commitment is visible.
 * 2. **Ambiguity is not an answer.** Anything that isn't clearly one gesture
 *    or the other returns null, and null is a denial upstream — the same
 *    asymmetry the spoken confirmation uses.
 *
 * Runs on the landmarks MediaPipe already produces for the orb's pinch
 * controls, so there is no second model to load.
 */

import type { NormalizedLandmark } from "@mediapipe/tasks-vision";

export type ConfirmGesture = "confirm" | "deny";

/** How long the gesture must be held before it counts. */
export const HOLD_MS = 800;

// MediaPipe hand landmark indices.
const WRIST = 0;
const THUMB_MCP = 2;
const THUMB_IP = 3;
const THUMB_TIP = 4;
const INDEX_MCP = 5;
const INDEX_TIP = 8;
const MIDDLE_MCP = 9;
const MIDDLE_TIP = 12;
const RING_TIP = 16;
const PINKY_MCP = 17;
const PINKY_TIP = 20;

function distance(a: NormalizedLandmark, b: NormalizedLandmark): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/**
 * A finger counts as curled when its tip sits closer to the wrist than its
 * knuckle does. Scale-free, so it works at any distance from the camera.
 */
function isCurled(
  landmarks: NormalizedLandmark[],
  tip: number,
  mcp: number,
): boolean {
  const wrist = landmarks[WRIST];
  return distance(landmarks[tip], wrist) < distance(landmarks[mcp], wrist) * 1.05;
}

/**
 * Classify a single frame. Returns null unless the hand is unambiguously
 * making one of the two gestures.
 *
 * The shape required is deliberately strict: all four fingers curled AND the
 * thumb clearly extended AND pointing up or down past a minimum vertical
 * distance. A relaxed fist with a slightly proud thumb does not qualify.
 */
export function classifyFrame(
  landmarks: NormalizedLandmark[] | undefined,
): ConfirmGesture | null {
  if (!landmarks || landmarks.length < 21) return null;

  const fingersCurled =
    isCurled(landmarks, INDEX_TIP, INDEX_MCP) &&
    isCurled(landmarks, MIDDLE_TIP, MIDDLE_MCP) &&
    isCurled(landmarks, RING_TIP, MIDDLE_MCP) &&
    isCurled(landmarks, PINKY_TIP, PINKY_MCP);

  if (!fingersCurled) return null;

  const wrist = landmarks[WRIST];
  const thumbTip = landmarks[THUMB_TIP];
  const thumbMcp = landmarks[THUMB_MCP];

  // The thumb must actually be sticking out, not tucked against the fist.
  const handSpan = distance(wrist, landmarks[MIDDLE_MCP]) || 1;
  const thumbExtended = distance(thumbTip, landmarks[THUMB_IP]) > handSpan * 0.25;
  if (!thumbExtended) return null;

  // Image coordinates: y grows downward, so "up" is a NEGATIVE delta.
  const vertical = thumbTip.y - thumbMcp.y;
  const minimumTravel = handSpan * 0.35;

  if (vertical < -minimumTravel) return "confirm";
  if (vertical > minimumTravel) return "deny";
  return null; // sideways thumb — not an answer
}

export interface GestureProgress {
  gesture: ConfirmGesture | null;
  /** 0–1, how far through the hold. Drives the ring in the orb. */
  progress: number;
  /** True on the single frame the hold completes. */
  fired: boolean;
}

/**
 * Accumulates frames into a held gesture.
 *
 * Resets the moment the gesture changes or disappears — a hold must be
 * continuous. Partial progress toward "confirm" cannot carry over into
 * "deny", which would let a wavering hand produce an answer nobody meant.
 */
export class GestureHold {
  private current: ConfirmGesture | null = null;
  private startedAt = 0;
  private alreadyFired = false;

  constructor(private holdMs: number = HOLD_MS) {}

  update(
    landmarks: NormalizedLandmark[] | undefined,
    now: number = performance.now(),
  ): GestureProgress {
    const detected = classifyFrame(landmarks);

    if (detected === null || detected !== this.current) {
      this.current = detected;
      this.startedAt = detected === null ? 0 : now;
      this.alreadyFired = false;
      return { gesture: detected, progress: 0, fired: false };
    }

    const elapsed = now - this.startedAt;
    const progress = Math.min(1, elapsed / this.holdMs);

    // Fire once per hold, not every frame after the threshold.
    const fired = progress >= 1 && !this.alreadyFired;
    if (fired) this.alreadyFired = true;

    return { gesture: detected, progress, fired };
  }

  reset(): void {
    this.current = null;
    this.startedAt = 0;
    this.alreadyFired = false;
  }
}
