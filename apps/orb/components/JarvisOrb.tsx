"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createOrbScene, type OrbSceneApi } from "@/lib/orbScene";
import { HandTracker, type TrackerStatus } from "@/lib/handTracker";
import {
  INITIAL_SNAPSHOT,
  createMockSource,
  type KavachSnapshot,
  type KavachSource,
} from "@/lib/kavachState";
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

  // The keydown listener is registered once, so it would otherwise close over
  // the first snapshot forever.
  const snapshotRef = useRef(snapshot);
  useEffect(() => {
    snapshotRef.current = snapshot;
  }, [snapshot]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const scene = createOrbScene(container);
    sceneRef.current = scene;

    // Suit-up sequence first (§4 #1); the mock Brain only starts once the
    // shells have assembled and the core has ignited.
    const source = createMockSource();
    sourceRef.current = source;

    let unsubscribe: (() => void) | undefined;
    let cancelled = false;

    void scene.playBoot().then(() => {
      if (cancelled) return;
      unsubscribe = source.subscribe(setSnapshot);
    });

    return () => {
      cancelled = true;
      unsubscribe?.();
      source.stop();
      sourceRef.current = null;
      trackerRef.current?.stop();
      trackerRef.current = null;
      scene.dispose();
      sceneRef.current = null;
    };
  }, []);

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
          sourceRef.current?.rearm();
          break;
        case " ":
          // Push-to-talk override (§4). Phase 2 wires this to the mic;
          // for now it just proves the key is claimed and does not scroll.
          e.preventDefault();
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggleGestures]);

  const cameraOn = camera === "on";

  return (
    <>
      <div ref={containerRef} className="orb-root" />

      <div className="overlay-vignette" />
      <div className="overlay-grain" />
      <div className="overlay-scanlines" />

      <div className="hud hud-title">
        <span className="title-mark">KAVACH</span>
        <span className="title-sub">कवच · local-first presence</span>
      </div>

      <ToolCallPackets toolCalls={snapshot.toolCalls} targetRef={toolPanelRef} />

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
