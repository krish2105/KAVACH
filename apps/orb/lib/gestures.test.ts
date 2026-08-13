/**
 * Gesture confirmation tests (spec §7 extension).
 *
 * A gesture that authorises deleting something has to be deliberate. The two
 * properties under test:
 *
 *   - a gesture must be HELD; a hand passing through the shape is not consent
 *   - anything ambiguous returns null, and null is a denial upstream
 *
 * Landmarks are synthesised rather than recorded, so this runs anywhere with
 * no camera and no fixture files.
 */

import { describe, expect, it } from "vitest";
import { classifyFrame, GestureHold, HOLD_MS } from "./gestures";

// MediaPipe's NormalizedLandmark requires `visibility`; the classifier
// only reads x/y, but the type has to match to keep tsc honest.
type LM = { x: number; y: number; z: number; visibility: number };

/**
 * Build a 21-point hand. Origin is the wrist at (0.5, 0.8); the hand extends
 * upward, matching a hand held up to a webcam.
 */
function hand(opts: {
  thumbY?: number;      // thumb tip y relative to the wrist (negative = up)
  thumbOut?: number;    // how far the thumb is extended
  fingersCurled?: boolean;
}): LM[] {
  const { thumbY = -0.25, thumbOut = 0.12, fingersCurled = true } = opts;
  const wx = 0.5;
  const wy = 0.8;
  const p = (x: number, y: number): LM => ({ x, y, z: 0, visibility: 1 });

  const knuckleY = wy - 0.18;   // MCPs sit above the wrist
  // Curled: tips near the wrist. Extended: tips well above the knuckles.
  const tipY = fingersCurled ? wy - 0.06 : wy - 0.34;

  const points: LM[] = new Array(21).fill(null).map(() => p(wx, wy));
  points[0] = p(wx, wy);                                  // wrist
  points[2] = p(wx - 0.06, wy - 0.10);                    // thumb MCP
  points[3] = p(wx - 0.06 + thumbOut * 0.4, wy - 0.10 + thumbY * 0.4); // thumb IP
  points[4] = p(wx - 0.06 + thumbOut, wy - 0.10 + thumbY);            // thumb TIP
  points[5] = p(wx - 0.05, knuckleY);                     // index MCP
  points[8] = p(wx - 0.05, tipY);                         // index TIP
  points[9] = p(wx, knuckleY);                            // middle MCP
  points[12] = p(wx, tipY);                               // middle TIP
  points[16] = p(wx + 0.04, tipY);                        // ring TIP
  points[17] = p(wx + 0.07, knuckleY);                    // pinky MCP
  points[20] = p(wx + 0.07, tipY);                        // pinky TIP
  return points;
}

describe("classifyFrame", () => {
  it("recognises a clear thumbs-up as confirm", () => {
    expect(classifyFrame(hand({ thumbY: -0.25 }))).toBe("confirm");
  });

  it("recognises a clear thumbs-down as deny", () => {
    expect(classifyFrame(hand({ thumbY: 0.25 }))).toBe("deny");
  });

  it("refuses an open hand — fingers must be curled", () => {
    expect(classifyFrame(hand({ thumbY: -0.25, fingersCurled: false }))).toBeNull();
  });

  it("refuses a plain fist with the thumb tucked in", () => {
    // A tucked thumb means the TIP sits close to the IP joint. Shrinking only
    // the horizontal offset isn't enough — an earlier version of this test
    // did that while leaving thumbY at -0.25, which is a thumb pointing
    // firmly upward, i.e. not tucked at all.
    expect(classifyFrame(hand({ thumbY: -0.02, thumbOut: 0.01 }))).toBeNull();
  });

  it("refuses a fist where the thumb rides up the side without extending", () => {
    // The near-miss that matters: fingers curled, thumb roughly upward, but
    // never actually sticking out from the hand.
    expect(classifyFrame(hand({ thumbY: -0.06, thumbOut: 0.02 }))).toBeNull();
  });

  it("refuses a sideways thumb — that is not an answer", () => {
    expect(classifyFrame(hand({ thumbY: 0 }))).toBeNull();
  });

  it("refuses missing or partial landmarks rather than guessing", () => {
    expect(classifyFrame(undefined)).toBeNull();
    expect(classifyFrame([])).toBeNull();
    expect(classifyFrame(hand({}).slice(0, 10))).toBeNull();
  });
});

describe("GestureHold", () => {
  it("does not fire on a hand passing through the shape", () => {
    const hold = new GestureHold();
    // 300ms of thumbs-up, well under the threshold, then gone.
    expect(hold.update(hand({ thumbY: -0.25 }), 0).fired).toBe(false);
    expect(hold.update(hand({ thumbY: -0.25 }), 300).fired).toBe(false);
    expect(hold.update(undefined, 400).fired).toBe(false);
  });

  it("fires once the gesture is held long enough", () => {
    const hold = new GestureHold();
    hold.update(hand({ thumbY: -0.25 }), 0);
    const result = hold.update(hand({ thumbY: -0.25 }), HOLD_MS + 1);
    expect(result.fired).toBe(true);
    expect(result.gesture).toBe("confirm");
  });

  it("fires only once per hold, not every frame after", () => {
    const hold = new GestureHold();
    hold.update(hand({ thumbY: -0.25 }), 0);
    expect(hold.update(hand({ thumbY: -0.25 }), HOLD_MS + 1).fired).toBe(true);
    expect(hold.update(hand({ thumbY: -0.25 }), HOLD_MS + 200).fired).toBe(false);
  });

  it("reports progress so the orb can show the commitment", () => {
    const hold = new GestureHold();
    hold.update(hand({ thumbY: -0.25 }), 0);
    const half = hold.update(hand({ thumbY: -0.25 }), HOLD_MS / 2);
    expect(half.progress).toBeGreaterThan(0.4);
    expect(half.progress).toBeLessThan(0.6);
    expect(half.fired).toBe(false);
  });

  it("resets when the hand disappears mid-hold", () => {
    const hold = new GestureHold();
    hold.update(hand({ thumbY: -0.25 }), 0);
    hold.update(undefined, HOLD_MS / 2);
    // Re-appearing starts the clock over rather than resuming.
    hold.update(hand({ thumbY: -0.25 }), HOLD_MS / 2 + 10);
    expect(hold.update(hand({ thumbY: -0.25 }), HOLD_MS).fired).toBe(false);
  });

  it("does not let progress toward confirm carry over into deny", () => {
    // A wavering hand must not produce an answer nobody meant.
    const hold = new GestureHold();
    hold.update(hand({ thumbY: -0.25 }), 0);
    hold.update(hand({ thumbY: -0.25 }), HOLD_MS - 50);
    const flipped = hold.update(hand({ thumbY: 0.25 }), HOLD_MS - 40);
    expect(flipped.fired).toBe(false);
    expect(flipped.progress).toBe(0);
  });
});
