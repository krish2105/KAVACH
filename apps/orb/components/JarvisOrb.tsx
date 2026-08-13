"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createOrbScene, type OrbSceneApi } from "@/lib/orbScene";
import { HandTracker, type TrackerStatus } from "@/lib/handTracker";
import {
  INITIAL_SNAPSHOT,
  createMockSource,
  type KavachSnapshot,
  type KavachSource,
  STATE_LABEL,
} from "@/lib/kavachState";
import { createLiveSource } from "@/lib/liveSource";
import { StatusPanel } from "@/components/hud/StatusPanel";
import { TranscriptPanel } from "@/components/hud/TranscriptPanel";
import { ToolCallLog } from "@/components/hud/ToolCallLog";
import { ToolCallPackets } from "@/components/hud/ToolCallPackets";

type CameraState = "off" | "starting" | "on" | "error";

const MODE_LABEL: Record<TrackerStatus["mode"], string> = {
  idle: "STANDBY",
  spin: "SPIN",
  zoom: "ZOOM",
};

export default function JarvisOrb() {
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<OrbSceneApi | null>(null);
  const trackerRef = useRef<HandTracker | null>(null);
  const sourceRef = useRef<KavachSource | null>(null);
  const toolPanelRef = useRef<HTMLDivElement>(null);

  const [camera, setCamera] = useState<CameraState>("off");
  const [status, setStatus] = useState<TrackerStatus>({ hands: 0, mode: "idle" });
  const [error, setError] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<KavachSnapshot>(INITIAL_SNAPSHOT);
  const [brainOnline, setBrainOnline] = useState(false);
  const [usingMock, setUsingMock] = useState(false);
  const [gestureHold, setGestureHold] = useState<{
    gesture: "confirm" | "deny" | null;
    progress: number;
  }>({ gesture: null, progress: 0 });

  // The keydown listener is registered once, so it would otherwise close over
  // the first snapshot forever.
  const snapshotRef = useRef(snapshot);
  useEffect(() => {
    snapshotRef.current = snapshot;
  }, [snapshot]);

  // ?overlay=1 — the floating desktop panel (see brain/kavach/presence).
  //
  // A 340pt panel is a different medium from a browser window, not a smaller
  // one: the full HUD covers the canvas entirely at that size, which is why
  // the orb was invisible in the overlay. Overlay mode strips everything back
  // to the orb plus the one line of state worth glancing at, and drops the
  // page background so it floats on the desktop rather than sitting in a box.
  const [overlayMode, setOverlayMode] = useState(false);
  useEffect(() => {
    const on = new URLSearchParams(window.location.search).get("overlay") === "1";
    setOverlayMode(on);
    document.documentElement.classList.toggle("kv-overlay", on);
    return () => document.documentElement.classList.remove("kv-overlay");
  }, []);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const scene = createOrbScene(container);
    sceneRef.current = scene;

    // Prefer the real Brain (brain/kavach/bridge). If it isn't running, fall
    // back to the scripted mock after a grace period so the orb still
    // demonstrates itself — but label it, because a demo that looks live and
    // isn't is worse than no demo.
    const live = createLiveSource({
      onConnectionChange: (connected) => {
        setBrainOnline(connected);
        if (connected) {
          window.clearTimeout(fallbackTimer);
          mock?.stop();
          mock = null;
          unsubscribeMock?.();
          unsubscribeMock = undefined;
          setUsingMock(false);
        }
      },
    });
    sourceRef.current = live;

    let mock: KavachSource | null = null;
    let unsubscribeLive: (() => void) | undefined;
    let unsubscribeMock: (() => void) | undefined;
    let fallbackTimer = 0;
    let cancelled = false;

    void scene.playBoot().then(() => {
      if (cancelled) return;
      unsubscribeLive = live.subscribe(setSnapshot);
      fallbackTimer = window.setTimeout(() => {
        if (cancelled) return;
        mock = createMockSource();
        unsubscribeMock = mock.subscribe(setSnapshot);
        setUsingMock(true);
      }, 2000);
    });

    return () => {
      cancelled = true;
      window.clearTimeout(fallbackTimer);
      unsubscribeLive?.();
      unsubscribeMock?.();
      mock?.stop();
      live.stop();
      sourceRef.current = null;
      trackerRef.current?.stop();
      trackerRef.current = null;
      scene.dispose();
      sceneRef.current = null;
    };
  }, []);

  // Let the native panel pause rendering while it is hidden. A WebGL canvas
  // at zero opacity still renders every frame, and the panel is invisible for
  // most of its life — this was a constant CPU cost for pixels nobody sees.
  useEffect(() => {
    if (!overlayMode) return;
    const w = window as unknown as { __kavachSetRendering?: (on: boolean) => void };
    w.__kavachSetRendering = (on: boolean) => sceneRef.current?.setRendering(on);
    return () => { delete w.__kavachSetRendering; };
  }, [overlayMode]);

  // The orb is a view of the snapshot — the scene never owns agent state.
  // Driving it through refs rather than React props keeps this off the
  // render path; these fire at animation rate.
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || scene.getState() === "boot") return;
    scene.setState(snapshot.state);
    scene.setAmplitude(snapshot.amplitude);
    scene.setConfidence(snapshot.confidence);
  }, [snapshot.state, snapshot.amplitude, snapshot.confidence]);

  const stopGestures = useCallback(() => {
    trackerRef.current?.stop();
    trackerRef.current = null;
    setCamera("off");
    setStatus({ hands: 0, mode: "idle" });
  }, []);

  const startGestures = useCallback(async () => {
    const video = videoRef.current;
    const overlay = overlayRef.current;
    if (!video || !overlay || trackerRef.current) return;

    setCamera("starting");
    setError(null);

    const tracker = new HandTracker(video, overlay, {
      onRotate: (dt, dp) => sceneRef.current?.rotateBy(dt, dp),
      onZoom: (factor) => sceneRef.current?.zoomBy(factor),
      onStatus: setStatus,
      onConfirmGesture: (gesture, progress, fired) => {
        setGestureHold({ gesture, progress });
        // Only the completed hold is an answer; progress is just feedback.
        if (fired && gesture) {
          sourceRef.current?.answerConfirmation?.(gesture === "confirm");
        }
      },
    });
    trackerRef.current = tracker;

    try {
      await tracker.start();
      setCamera("on");
    } catch (err) {
      trackerRef.current = null;
      tracker.stop();
      setCamera("error");
      setError(
        err instanceof DOMException && err.name === "NotAllowedError"
          ? "CAMERA ACCESS DENIED"
          : "TRACKING INIT FAILED",
      );
    }
  }, []);

  const toggleGestures = useCallback(() => {
    if (trackerRef.current) stopGestures();
    else void startGestures();
  }, [startGestures, stopGestures]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      switch (e.key) {
        case "+":
        case "=":
          sceneRef.current?.zoomIn();
          break;
        case "-":
        case "_":
          sceneRef.current?.zoomOut();
          break;
        case "r":
        case "R":
          sceneRef.current?.resetView();
          break;
        case "g":
        case "G":
          toggleGestures();
          break;
        case "k":
        case "K":
          // Stand-in for the real kill switch until Phase 4 bridges the
          // daemon socket into the browser. Same latch semantics: no
          // auto-recovery, explicit re-arm only.
          if (sourceRef.current) {
            if (snapshotRef.current.killSwitch === "armed") sourceRef.current.halt();
            else sourceRef.current.rearm();
          }
          break;
        case "Escape":
          // §5: an assistant that can't be interrupted feels like a hung
          // process. Cancels the current turn without latching.
          sourceRef.current?.interrupt?.();
          break;
        case " ":
          // Push-to-talk override (§4) — held, not toggled.
          e.preventDefault();
          if (!e.repeat) sourceRef.current?.pushToTalk?.(true);
          break;
      }
    };

    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === " ") sourceRef.current?.pushToTalk?.(false);
    };

    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKeyUp);
      // Never leave the mic latched open because the component unmounted
      // mid-press.
      sourceRef.current?.pushToTalk?.(false);
    };
  }, [toggleGestures]);

  const cameraOn = camera === "on";

  return (
    <>
      <div ref={containerRef} className="orb-root" />

      <div className="overlay-vignette" />
      <div className="overlay-grain" />
      <div className="overlay-scanlines" />

      {overlayMode ? (
        // One glanceable line. Anything more competes with the orb, which is
        // the thing you are actually meant to be looking at.
        <div className="hud overlay-caption">
          <span className={`state-pill state-${snapshot.state}`}>
            <span className="state-dot" aria-hidden="true" />
            {STATE_LABEL[snapshot.state]}
          </span>
          {snapshot.killSwitch === "disarmed" && (
            <span className="kill-badge is-disarmed">⛔ DISARMED</span>
          )}
          {snapshot.route && (
            <span className={`overlay-route route-${snapshot.route}`}>
              {snapshot.route === "claude" ? "CLAUDE" : "LOCAL"}
              <span className="overlay-conf">{Math.round(snapshot.confidence * 100)}</span>
            </span>
          )}
          {(snapshot.partial || snapshot.transcript) && (
            <p className="overlay-transcript">
              {snapshot.transcript}
              {snapshot.partial && (
                <span className="transcript-partial">{snapshot.partial}</span>
              )}
            </p>
          )}
          {/* Newest tool call only. The full log belongs in the window; here
              it would crowd out the orb, which is the point of the panel. */}
          {snapshot.toolCalls.length > 0 && (
            <span className={`overlay-tool status-${snapshot.toolCalls[0].status}`}>
              {snapshot.toolCalls[0].server} · {snapshot.toolCalls[0].summary}
            </span>
          )}
        </div>
      ) : (
        <div className="hud hud-title">
          <span className="title-mark">KAVACH</span>
          <span className="title-sub">
            कवच · local-first presence
            <span
              className={`brain-badge${brainOnline ? " is-online" : usingMock ? " is-mock" : ""}`}
              title={
                brainOnline
                  ? "Connected to the Brain over ws://127.0.0.1:8765"
                  : usingMock
                    ? "Brain not running — this is the scripted demo, not live audio"
                    : "Connecting to the Brain…"
              }
            >
              {brainOnline ? "BRAIN LIVE" : usingMock ? "DEMO (MOCK)" : "CONNECTING…"}
            </span>
          </span>
        </div>
      )}

      <ToolCallPackets toolCalls={snapshot.toolCalls} targetRef={toolPanelRef} />

      {/* Held-gesture indicator (§7). Visible commitment: the user can see
          how far through the hold they are and back out before it fires. */}
      {gestureHold.gesture && (
        <div className={`gesture-hold gesture-${gestureHold.gesture}`} role="status">
          <svg viewBox="0 0 48 48" width="48" height="48" aria-hidden="true">
            <circle className="gh-track" cx="24" cy="24" r="20" />
            <circle
              className="gh-fill"
              cx="24"
              cy="24"
              r="20"
              strokeDasharray={2 * Math.PI * 20}
              strokeDashoffset={2 * Math.PI * 20 * (1 - gestureHold.progress)}
            />
          </svg>
          <span className="gh-label">
            {gestureHold.gesture === "confirm" ? "👍 HOLD TO CONFIRM" : "👎 HOLD TO DENY"}
          </span>
        </div>
      )}

      <div className="hud hud-stack hud-stack-left">
        <StatusPanel
          state={snapshot.state}
          route={snapshot.route}
          confidence={snapshot.confidence}
          killSwitch={snapshot.killSwitch}
          amplitude={snapshot.amplitude}
        />
        <TranscriptPanel
          transcript={snapshot.transcript}
          partial={snapshot.partial}
          state={snapshot.state}
        />
      </div>

      <div className="hud hud-stack hud-stack-right" ref={toolPanelRef}>
        <ToolCallLog toolCalls={snapshot.toolCalls} />
      </div>

      <div className="hud hud-hint">
        <div>
          <span className="key">DRAG</span> spin&nbsp;&nbsp;
          <span className="key">SCROLL</span> zoom
        </div>
        {cameraOn ? (
          <div>
            <span className="key">PINCH + MOVE</span> spin&nbsp;&nbsp;
            <span className="key">PINCH BOTH HANDS ± SPREAD</span> zoom
          </div>
        ) : (
          <div>
            <span className="key">G</span> gestures&nbsp;&nbsp;
            <span className="key">R</span> reset&nbsp;&nbsp;
            <span className="key">K</span> kill switch&nbsp;&nbsp;
            <span className="key">ESC</span> interrupt
          </div>
        )}
      </div>

      <div className="hud hud-controls">
        <div className={`camera-panel${cameraOn ? " visible" : ""}`}>
          {/* Mirrored preview so it behaves like a mirror */}
          <video ref={videoRef} muted playsInline className="camera-video" />
          <canvas ref={overlayRef} width={208} height={156} className="camera-overlay" />
          <div className="camera-status">
            {status.hands > 0
              ? `${status.hands} HAND${status.hands > 1 ? "S" : ""} · ${MODE_LABEL[status.mode]}`
              : "SHOW HANDS"}
          </div>
        </div>

        {error && <div className="hud-error">{error}</div>}

        <div className="hud-row">
          <button
            type="button"
            className="hud-btn"
            aria-pressed={cameraOn}
            onClick={toggleGestures}
            disabled={camera === "starting"}
          >
            {camera === "starting" ? "INITIALIZING…" : cameraOn ? "GESTURES ON" : "GESTURES OFF"}
          </button>
        </div>
        <div className="hud-row">
          <button type="button" className="hud-btn" onClick={() => sceneRef.current?.zoomIn()} aria-label="Zoom in">
            +
          </button>
          <button type="button" className="hud-btn" onClick={() => sceneRef.current?.zoomOut()} aria-label="Zoom out">
            −
          </button>
          <button type="button" className="hud-btn" onClick={() => sceneRef.current?.resetView()}>
            RESET
          </button>
        </div>
      </div>
    </>
  );
}
