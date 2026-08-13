import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { ShaderPass } from "three/addons/postprocessing/ShaderPass.js";

/**
 * What the orb is doing, expressed visually (spec §4).
 *
 * `halted` is KAVACH's own addition: when the kill switch latches, the orb
 * goes cold and nearly still. The guardrail should be visible in the product,
 * not just true in the logs.
 */
export type OrbState =
  | "boot"
  | "idle"
  | "listening"
  | "thinking"
  | "acting"
  | "speaking"
  | "halted";

export interface OrbSceneApi {
  /** Rotate the camera around the orb by the given angles (radians). */
  rotateBy(deltaTheta: number, deltaPhi: number): void;
  /** Multiply the camera distance by `factor` (<1 zooms in, >1 zooms out). */
  zoomBy(factor: number): void;
  zoomIn(): void;
  zoomOut(): void;
  resetView(): void;

  /** Drive the orb's behaviour. Transitions are eased, never snapped. */
  setState(state: OrbState): void;
  /**
   * Mic amplitude while listening, or the TTS envelope while speaking (0–1).
   * Feeds the pulse rings and bloom so the orb breathes with the audio.
   */
  setAmplitude(value: number): void;
  /**
   * Reasoning confidence (0–1) → outer shell opacity (§4 differentiator #3).
   * In practice a proxy for whether the fast local model handled it or it had
   * to escalate to Claude.
   */
  setConfidence(value: number): void;
  /**
   * Stop or resume rendering entirely.
   *
   * The floating panel spends most of its life at zero opacity, but a hidden
   * WebGL canvas still renders every frame — measured here as a constant
   * WebKit CPU cost for pixels nobody can see. Pausing while hidden is the
   * single biggest power saving in the Presence layer.
   */
  setRendering(enabled: boolean): void;
  /** Run the suit-up sequence: shells assemble, core ignites last. */
  playBoot(): Promise<void>;
  /** Current state, for callers that need to read back. */
  getState(): OrbState;

  dispose(): void;
}

interface StateProfile {
  /** Rotation speed multiplier applied to every layer. */
  spin: number;
  /** Base bloom strength before the amplitude term. */
  bloom: number;
  /** Gain on the core's own pulse. */
  core: number;
  /** How strongly amplitude drives the pulse rings. */
  ring: number;
}

const STATE_PROFILES: Record<OrbState, StateProfile> = {
  // Shells are still assembling; keep everything quiet underneath.
  boot: { spin: 0.3, bloom: 0.6, core: 0.2, ring: 0 },
  idle: { spin: 1.0, bloom: 1.6, core: 1.0, ring: 0 },
  // Rings expand outward in time with the mic.
  listening: { spin: 1.25, bloom: 2.0, core: 1.15, ring: 1.0 },
  // Inner core spins hard — the "working" read.
  thinking: { spin: 2.6, bloom: 1.85, core: 1.35, ring: 0.15 },
  acting: { spin: 1.9, bloom: 2.2, core: 1.25, ring: 0.3 },
  // Bloom pulses with the TTS envelope.
  speaking: { spin: 1.15, bloom: 2.35, core: 1.4, ring: 0.75 },
  // Latched: cold, near-frozen, unmistakably stopped.
  halted: { spin: 0.04, bloom: 0.35, core: 0.15, ring: 0 },
};

const HOME_POSITION = new THREE.Vector3(0, 0.5, 5.5);
const MIN_DISTANCE = 0.6;
const MAX_DISTANCE = 40;

export function createOrbScene(container: HTMLElement): OrbSceneApi {
  // Fall back to the viewport if the container has not been laid out yet —
  // a 0×0 renderer starts with an incomplete framebuffer it never recovers from.
  const width = container.clientWidth || window.innerWidth;
  const height = container.clientHeight || window.innerHeight;

  // ——— SCENE ———
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 500);
  camera.position.copy(HOME_POSITION);

  // `alpha` + a zero clear-alpha let the canvas be genuinely see-through, so
  // the floating desktop panel is an orb rather than an orb in a black box.
  // In the browser it changes nothing visible: the page behind it is #000.
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setClearColor(0x000000, 0);
  renderer.setSize(width, height);
  // The floating panel renders ~5.6x fewer pixels than a full browser window
  // (400pt at 2x is 0.64 Mpx against roughly 3.6 Mpx), which is most of why it
  // looked soft next to the same orb in a tab. Because the panel is small, 3x
  // there still costs less in absolute pixels than the window does at 2x — so
  // the sharpness is nearly free, and the earlier 3x-everywhere experiment
  // that warmed the machine is not repeated.
  const inPanel =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("overlay") === "1";
  // Supersample in the panel — deliberately *above* devicePixelRatio.
  //
  // `Math.min(devicePixelRatio, 3)` cannot exceed the display's own ratio, so
  // on a 2x screen it silently stayed at 2 and the "3x" did nothing. Going
  // over dpr renders more pixels than the screen has and downsamples, which
  // is what actually sharpens fine wireframe.
  //
  // Affordable only because the panel is small and now pauses entirely while
  // hidden; the window keeps the ordinary 2x ceiling.
  // Budget total pixels rather than fixing a ratio. A flat 3x looks identical
  // on a small panel and costs 5.2 Mpx on a large one — measured as ~83% of a
  // core in WebKit, for an orb most of whose detail the eye cannot resolve
  // anyway. Targeting a constant ~2.5 Mpx supersamples hard where it helps
  // (small panels, where thin wireframe aliases) and backs off where it does
  // not.
  const PANEL_PIXEL_BUDGET = 2_500_000;
  renderer.setPixelRatio(
    inPanel
      ? Math.max(1, Math.min(3, Math.sqrt(PANEL_PIXEL_BUDGET / (width * height))))
      : Math.min(window.devicePixelRatio, 2),
  );
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.8;
  container.appendChild(renderer.domElement);

  // ——— POST PROCESSING ———
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));

  const bloom = new UnrealBloomPass(
    new THREE.Vector2(width, height),
    1.8, // strength
    0.4, // radius
    0.2, // threshold
  );
  composer.addPass(bloom);

  // Chromatic aberration + color grade shader
  const chromaticShader = {
    uniforms: {
      tDiffuse: { value: null },
      uTime: { value: 0 },
      uIntensity: { value: 0.003 },
    },
    vertexShader: `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform sampler2D tDiffuse;
      uniform float uTime;
      uniform float uIntensity;
      varying vec2 vUv;
      void main() {
        vec2 dir = vUv - vec2(0.5);
        float d = length(dir);
        float offset = uIntensity * d;
        // Slight flicker
        float flicker = 1.0 + 0.02 * sin(uTime * 30.0) * sin(uTime * 7.3);
        vec4 cr = texture2D(tDiffuse, vUv + dir * offset);
        vec4 cg = texture2D(tDiffuse, vUv);
        vec4 cb = texture2D(tDiffuse, vUv - dir * offset * 0.5);
        vec3 graded = vec3(cr.r, cg.g * 1.05, cb.b * 0.6) * flicker;
        // Warm grade, matching the gold palette.
        graded = mix(graded, graded * vec3(1.15, 0.85, 0.55), 0.3);

        // Alpha from brightness, not from the source's alpha channel.
        //
        // UnrealBloomPass writes alpha = 1 across the whole frame, so reading
        // it back made the entire canvas opaque — the panel rendered as a
        // solid yellow rectangle with the orb somewhere inside it. Luminance
        // is what we actually want anyway: black background falls away and
        // the orb's glow carries its own soft edge onto the desktop.
        float alpha = clamp(max(graded.r, max(graded.g, graded.b)) * 1.15, 0.0, 1.0);
        gl_FragColor = vec4(graded, alpha);
      }
    `,
  };
  //: 1.0 at a full window, ~0.55 in a small panel. Derived from width rather
  //: than hardcoded per mode so an intermediate size lands sensibly too.
  //:
  //: `let`, and recomputed on resize. As a `const` it was fixed at scene
  //: creation, so a 760pt panel going full screen kept a bloom tuned for
  //: 760pt: the orb filled the display with the glow of a thumbnail, and the
  //: core read as a dim wireframe instead of the white-hot centre it has in a
  //: browser window. Nothing about it looked broken — just wrong.
  let bloomScale = Math.max(0.5, Math.min(1, width / 1400));

  const chromaticPass = new ShaderPass(chromaticShader);
  composer.addPass(chromaticPass);

  // Controls
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.04;
  controls.minDistance = MIN_DISTANCE;
  controls.maxDistance = MAX_DISTANCE;
  controls.zoomSpeed = 1.4;
  controls.enablePan = false;

  // ——— COLORS ———
  // Gold. Values step in luminance, not just hue, so additive blending still
  // separates the layers where many lines overlap near the core.
  const C_BRIGHT = 0xffaa30;
  const C_MID = 0xdd7700;
  const C_DIM = 0x884400;
  const C_FAINT = 0x553300;
  const C_HOT = 0xffcc66;

  // ——— ORB ROOT ———
  // Every part of the orb (shells, core, orbiting debris, text, dust, rings)
  // lives under this group.
  const orbGroup = new THREE.Group();
  scene.add(orbGroup);

  // ——— MATERIAL HELPERS ———
  function lineMat(color: number, opacity = 1) {
    return new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
  }

  // ——— UTILITY: Create ring at latitude ———
  function latRing(radius: number, lat: number, segs = 120) {
    const r = radius * Math.cos(lat);
    const y = radius * Math.sin(lat);
    const pts: THREE.Vector3[] = [];
    for (let i = 0; i <= segs; i++) {
      const a = (i / segs) * Math.PI * 2;
      pts.push(new THREE.Vector3(r * Math.cos(a), y, r * Math.sin(a)));
    }
    return new THREE.BufferGeometry().setFromPoints(pts);
  }

  // ——— UTILITY: Create meridian ———
  function meridian(radius: number, lon: number, segs = 120) {
    const pts: THREE.Vector3[] = [];
    for (let i = 0; i <= segs; i++) {
      const lat = (i / segs) * Math.PI - Math.PI / 2;
      pts.push(
        new THREE.Vector3(
          radius * Math.cos(lat) * Math.cos(lon),
          radius * Math.sin(lat),
          radius * Math.cos(lat) * Math.sin(lon),
        ),
      );
    }
    return new THREE.BufferGeometry().setFromPoints(pts);
  }

  // ═══════════════════════════════════════════════
  // LAYER 1: OUTER SHELL — dense wireframe grid
  // ═══════════════════════════════════════════════
  const outerShell = new THREE.Group();
  const R1 = 2.0;

  // Dense latitude rings (30+)
  for (let i = -15; i <= 15; i++) {
    const lat = (i / 15) * (Math.PI / 2) * 0.95;
    const opacity = i % 3 === 0 ? 0.5 : 0.12;
    const color = i % 3 === 0 ? C_MID : C_FAINT;
    outerShell.add(new THREE.Line(latRing(R1, lat), lineMat(color, opacity)));
  }

  // Dense meridians (24)
  for (let i = 0; i < 24; i++) {
    const lon = (i / 24) * Math.PI * 2;
    const isMajor = i % 6 === 0;
    outerShell.add(
      new THREE.Line(
        meridian(R1, lon),
        lineMat(isMajor ? C_MID : C_FAINT, isMajor ? 0.6 : 0.1),
      ),
    );
  }

  // 4 bright cross meridians (the "plus" shape) — wide bands
  const CROSS_LINES = 18;
  const CROSS_SPREAD = 0.25; // radians total width
  for (let i = 0; i < 4; i++) {
    const lon = (i / 4) * Math.PI * 2;
    for (let j = 0; j < CROSS_LINES; j++) {
      const t = (j / (CROSS_LINES - 1)) * 2 - 1; // -1 to 1
      const offset = (t * CROSS_SPREAD) / 2;
      const falloff = 1 - Math.abs(t) * 0.7; // brighter at center, dimmer at edges
      const opacity = 0.85 * falloff;
      const color = Math.abs(t) < 0.3 ? C_BRIGHT : C_MID;
      outerShell.add(
        new THREE.Line(meridian(R1, lon + offset, 200), lineMat(color, opacity)),
      );
    }
  }

  // Bright equator band — wide
  const EQ_LINES = 20;
  const EQ_SPREAD = 0.35;
  for (let j = 0; j < EQ_LINES; j++) {
    const t = (j / (EQ_LINES - 1)) * 2 - 1;
    const offset = (t * EQ_SPREAD) / 2;
    const falloff = 1 - Math.abs(t) * 0.65;
    const opacity = 0.8 * falloff;
    const color = Math.abs(t) < 0.3 ? C_BRIGHT : C_MID;
    outerShell.add(
      new THREE.Line(latRing(R1, offset, 200), lineMat(color, opacity)),
    );
  }

  orbGroup.add(outerShell);

  // ═══════════════════════════════════════════════
  // LAYER 2: GRID PANELS on the sphere surface
  // ═══════════════════════════════════════════════
  const panelGroup = new THREE.Group();

  function createSpherePanel(
    latCenter: number,
    lonCenter: number,
    latSpan: number,
    lonSpan: number,
    radius: number,
    divisions = 4,
  ) {
    const group = new THREE.Group();
    const mat = lineMat(C_DIM, 0.25);

    // horizontal lines
    for (let i = 0; i <= divisions; i++) {
      const lat = latCenter - latSpan / 2 + (i / divisions) * latSpan;
      const pts: THREE.Vector3[] = [];
      for (let j = 0; j <= divisions * 4; j++) {
        const lon = lonCenter - lonSpan / 2 + (j / (divisions * 4)) * lonSpan;
        pts.push(
          new THREE.Vector3(
            radius * Math.cos(lat) * Math.cos(lon),
            radius * Math.sin(lat),
            radius * Math.cos(lat) * Math.sin(lon),
          ),
        );
      }
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat));
    }

    // vertical lines
    for (let j = 0; j <= divisions; j++) {
      const lon = lonCenter - lonSpan / 2 + (j / divisions) * lonSpan;
      const pts: THREE.Vector3[] = [];
      for (let i = 0; i <= divisions * 4; i++) {
        const lat = latCenter - latSpan / 2 + (i / (divisions * 4)) * latSpan;
        pts.push(
          new THREE.Vector3(
            radius * Math.cos(lat) * Math.cos(lon),
            radius * Math.sin(lat),
            radius * Math.cos(lat) * Math.sin(lon),
          ),
        );
      }
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat));
    }

    return group;
  }

  // Scatter panels across the sphere
  for (let i = 0; i < 30; i++) {
    const lat = (Math.random() - 0.5) * Math.PI * 0.8;
    const lon = Math.random() * Math.PI * 2;
    const size = 0.15 + Math.random() * 0.25;
    const panel = createSpherePanel(
      lat,
      lon,
      size,
      size,
      R1 + 0.01,
      3 + Math.floor(Math.random() * 3),
    );
    panelGroup.add(panel);
  }
  orbGroup.add(panelGroup);

  // ═══════════════════════════════════════════════
  // LAYER 3: SECONDARY SHELL — offset, partial arcs
  // ═══════════════════════════════════════════════
  const shell2 = new THREE.Group();
  const R2 = 2.12;

  // Partial arcs at random latitudes
  for (let i = 0; i < 16; i++) {
    const lat = (Math.random() - 0.5) * Math.PI * 0.85;
    const startLon = Math.random() * Math.PI * 2;
    const arcLen = 0.3 + Math.random() * 1.2;
    const pts: THREE.Vector3[] = [];
    const segs = 60;
    const r = R2 * Math.cos(lat);
    const y = R2 * Math.sin(lat);
    for (let j = 0; j <= segs; j++) {
      const a = startLon + (j / segs) * arcLen;
      pts.push(new THREE.Vector3(r * Math.cos(a), y, r * Math.sin(a)));
    }
    shell2.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        lineMat(C_MID, 0.2 + Math.random() * 0.3),
      ),
    );
  }

  // Partial meridian arcs
  for (let i = 0; i < 12; i++) {
    const lon = Math.random() * Math.PI * 2;
    const startLat = (Math.random() - 0.5) * Math.PI * 0.8;
    const arcLen = 0.3 + Math.random() * 0.8;
    const pts: THREE.Vector3[] = [];
    const segs = 40;
    for (let j = 0; j <= segs; j++) {
      const lat = startLat + (j / segs) * arcLen;
      pts.push(
        new THREE.Vector3(
          R2 * Math.cos(lat) * Math.cos(lon),
          R2 * Math.sin(lat),
          R2 * Math.cos(lat) * Math.sin(lon),
        ),
      );
    }
    shell2.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        lineMat(C_DIM, 0.15 + Math.random() * 0.2),
      ),
    );
  }
  orbGroup.add(shell2);

  // ═══════════════════════════════════════════════
  // LAYER 4: INNER CORE — spiral geodesic
  // ═══════════════════════════════════════════════
  const innerCore = new THREE.Group();
  const R3 = 0.9;

  // Dense spirals
  for (let s = 0; s < 8; s++) {
    const pts: THREE.Vector3[] = [];
    const turns = 3 + Math.random() * 2;
    const segs = 300;
    const phase = (s / 8) * Math.PI * 2;
    for (let i = 0; i <= segs; i++) {
      const t = i / segs;
      const lat = t * Math.PI - Math.PI / 2;
      const lon = t * turns * Math.PI * 2 + phase;
      pts.push(
        new THREE.Vector3(
          R3 * Math.cos(lat) * Math.cos(lon),
          R3 * Math.sin(lat),
          R3 * Math.cos(lat) * Math.sin(lon),
        ),
      );
    }
    innerCore.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        lineMat(C_BRIGHT, 0.3 + Math.random() * 0.2),
      ),
    );
  }

  // Inner latitude rings
  for (let i = -6; i <= 6; i++) {
    const lat = (i / 6) * (Math.PI / 2) * 0.9;
    innerCore.add(new THREE.Line(latRing(R3, lat, 80), lineMat(C_DIM, 0.2)));
  }

  // Inner meridians
  for (let i = 0; i < 12; i++) {
    const lon = (i / 12) * Math.PI * 2;
    innerCore.add(new THREE.Line(meridian(R3, lon, 80), lineMat(C_DIM, 0.15)));
  }

  orbGroup.add(innerCore);

  // ═══════════════════════════════════════════════
  // LAYER 5: INNERMOST CORE — bright hot center
  // ═══════════════════════════════════════════════
  const coreR = 0.25;

  // Icosahedron wireframe core
  const icoGeo = new THREE.IcosahedronGeometry(coreR, 1);
  const icoEdges = new THREE.EdgesGeometry(icoGeo);
  const icoWireMat = lineMat(C_HOT, 0.9);
  const icoWire = new THREE.LineSegments(icoEdges, icoWireMat);
  orbGroup.add(icoWire);

  // Glowing center sphere — subtle, see-through
  const coreSphereMat = new THREE.MeshBasicMaterial({
    color: C_HOT,
    transparent: true,
    opacity: 0.15,
    blending: THREE.AdditiveBlending,
  });
  const coreSphere = new THREE.Mesh(new THREE.SphereGeometry(0.15, 16, 16), coreSphereMat);
  orbGroup.add(coreSphere);

  // Larger faint glow — very subtle
  const glowSphereMat = new THREE.MeshBasicMaterial({
    color: C_MID,
    transparent: true,
    opacity: 0.04,
    blending: THREE.AdditiveBlending,
  });
  const glowSphere = new THREE.Mesh(new THREE.SphereGeometry(0.5, 16, 16), glowSphereMat);
  orbGroup.add(glowSphere);

  // ═══════════════════════════════════════════════
  // CODE TEXT — tiny, dense, scattered
  // ═══════════════════════════════════════════════
  const codeSnippets = [
    "sys.init()", "0xFF3A", "malloc()", ">> SCAN", "void*", "ACK",
    "SYNC OK", "ptr_ref", "exec()", "hash256", "::bind", "core.0",
    "01101001", "10110100", ">>> RDY", "HEAP 4K", "TCP/SYN",
    "mutex.lk", "IRQ 0x7", "DMA xfer", "REG EAX", "FAULT 0",
    "kernel.d", "pipe |>", "chmod +x", "fork()", "SIGTERM",
    "eth0: UP", "AES-256", "RSA 4096", "TLS 1.3", "HTTP/2",
    "latency", "200 OK", "PATCH /", "fn main", "use std",
    "impl Orb", "async {}", "spawn()", "arc::new", ".unwrap",
  ];

  interface SpriteDrift {
    phi: number;
    theta: number;
    r: number;
    speed: number;
  }

  function makeTextSprite(text: string, size = 0.08) {
    const c = document.createElement("canvas");
    c.width = 256;
    c.height = 32;
    const ctx = c.getContext("2d")!;
    ctx.font = "bold 14px Courier New";
    const alpha = 0.35 + Math.random() * 0.55;
    // Code sprites are canvas textures, so they never pick up the C_ constants
    // above — their colour has to be written here too, and a palette change
    // that misses this line leaves green text scattered through a gold orb.
    // Matches the dust gradient below: red pinned high, green and blue vary.
    ctx.fillStyle = `rgba(255, ${(140 + Math.random() * 60) | 0}, ${(20 + Math.random() * 60) | 0}, ${alpha})`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 128, 16);
    const tex = new THREE.CanvasTexture(c);
    tex.minFilter = THREE.LinearFilter;
    const s = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: tex,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    );
    s.scale.set(size * 5, size * 0.7, 1);
    return s;
  }

  function scatterText(count: number, sizeFn: () => number, rFn: () => number, speedScale: [number, number]) {
    const group = new THREE.Group();
    for (let i = 0; i < count; i++) {
      const sp = makeTextSprite(
        codeSnippets[Math.floor(Math.random() * codeSnippets.length)],
        sizeFn(),
      );
      const phi = Math.acos(2 * Math.random() - 1);
      const theta = Math.random() * Math.PI * 2;
      const r = rFn();
      sp.position.set(
        r * Math.sin(phi) * Math.cos(theta),
        r * Math.cos(phi),
        r * Math.sin(phi) * Math.sin(theta),
      );
      sp.userData = {
        phi,
        theta,
        r,
        speed:
          (speedScale[0] + Math.random() * speedScale[1]) *
          (Math.random() > 0.5 ? 1 : -1),
      } satisfies SpriteDrift;
      group.add(sp);
    }
    return group;
  }

  // On outer sphere — dense text coverage
  const textOuter = scatterText(
    1200,
    () => 0.04 + Math.random() * 0.04,
    () => R1 + 0.03 + Math.random() * 0.08,
    [0.0002, 0.0008],
  );
  orbGroup.add(textOuter);

  // On inner core — more text
  const textInner = scatterText(
    100,
    () => 0.03 + Math.random() * 0.03,
    () => R3 + 0.02,
    [0.0005, 0.001],
  );
  orbGroup.add(textInner);

  // Floating ambient text between shells
  const textAmbient = scatterText(
    400,
    () => 0.03,
    () => R3 + 0.2 + Math.random() * (R1 - R3 - 0.3),
    [0.0003, 0.0006],
  );
  orbGroup.add(textAmbient);

  // ═══════════════════════════════════════════════
  // ORBITING DEBRIS / ROCKS
  // ═══════════════════════════════════════════════
  // Shared geometries for performance — reuse across 250 satellites
  const debrisGeos = [
    new THREE.IcosahedronGeometry(0.012, 0),
    new THREE.IcosahedronGeometry(0.02, 0),
    new THREE.IcosahedronGeometry(0.03, 1),
    new THREE.IcosahedronGeometry(0.008, 0),
    new THREE.TetrahedronGeometry(0.015, 0),
    new THREE.OctahedronGeometry(0.018, 0),
  ];
  interface DebrisOrbit {
    orbitR: number;
    speed: number;
    tiltX: number;
    tiltZ: number;
    phase: number;
  }
  const debris: THREE.Mesh[] = [];
  for (let i = 0; i < 250; i++) {
    const geo = debrisGeos[Math.floor(Math.random() * debrisGeos.length)];
    const mat = new THREE.MeshBasicMaterial({
      color: Math.random() > 0.7 ? C_BRIGHT : C_MID,
      transparent: true,
      opacity: 0.3 + Math.random() * 0.6,
      blending: THREE.AdditiveBlending,
    });
    const mesh = new THREE.Mesh(geo, mat);
    const orbitR = 1.2 + Math.random() * 4.0;
    const speed = (0.08 + Math.random() * 0.6) * (Math.random() > 0.5 ? 1 : -1);
    const tiltX = (Math.random() - 0.5) * Math.PI * 0.9;
    const tiltZ = (Math.random() - 0.5) * Math.PI * 0.5;
    const phase = Math.random() * Math.PI * 2;
    mesh.userData = { orbitR, speed, tiltX, tiltZ, phase } satisfies DebrisOrbit;
    debris.push(mesh);
    orbGroup.add(mesh);

    // ~15% get a faint trailing line
    if (Math.random() > 0.85) {
      const trailPts: THREE.Vector3[] = [];
      for (let j = 0; j <= 15; j++) {
        const a = -(j / 15) * 0.3;
        trailPts.push(
          new THREE.Vector3(
            orbitR * Math.cos(a + phase),
            orbitR * 0.08 * Math.sin(a * 3),
            orbitR * Math.sin(a + phase),
          ),
        );
      }
      const trail = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(trailPts),
        lineMat(C_FAINT, 0.08),
      );
      mesh.add(trail);
    }
  }

  // ═══════════════════════════════════════════════
  // DUST PARTICLES — lots of them
  // ═══════════════════════════════════════════════
  const dustCount = 2000;
  const dustPos = new Float32Array(dustCount * 3);

  for (let i = 0; i < dustCount; i++) {
    // Concentrate near the sphere, sparse further out
    const rr = 0.5 + Math.pow(Math.random(), 0.6) * 7;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    dustPos[i * 3] = rr * Math.sin(phi) * Math.cos(theta);
    dustPos[i * 3 + 1] = rr * Math.cos(phi);
    dustPos[i * 3 + 2] = rr * Math.sin(phi) * Math.sin(theta);
  }

  const dustGeo = new THREE.BufferGeometry();
  dustGeo.setAttribute("position", new THREE.Float32BufferAttribute(dustPos, 3));

  // Soft dot texture
  const dotC = document.createElement("canvas");
  dotC.width = dotC.height = 64;
  const dCtx = dotC.getContext("2d")!;
  const g = dCtx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, "rgba(255,170,48,1)");
  g.addColorStop(0.2, "rgba(255,120,20,0.6)");
  g.addColorStop(0.5, "rgba(200,80,0,0.15)");
  g.addColorStop(1, "rgba(100,40,0,0)");
  dCtx.fillStyle = g;
  dCtx.fillRect(0, 0, 64, 64);

  const dustMat = new THREE.PointsMaterial({
    map: new THREE.CanvasTexture(dotC),
    size: 0.04,
    transparent: true,
    opacity: 0.5,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    sizeAttenuation: true,
    color: C_BRIGHT,
  });
  const dustPoints = new THREE.Points(dustGeo, dustMat);
  orbGroup.add(dustPoints);

  // ═══════════════════════════════════════════════
  // SCANNING RINGS
  // ═══════════════════════════════════════════════
  function makeScanRing(radius: number, thickness = 0.015) {
    const geo = new THREE.RingGeometry(radius - thickness, radius + thickness, 120);
    const mat = new THREE.MeshBasicMaterial({
      color: C_BRIGHT,
      transparent: true,
      opacity: 0,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.rotation.x = Math.PI / 2;
    return mesh;
  }

  const scanRing1 = makeScanRing(R1, 0.01);
  const scanRing2 = makeScanRing(R1 * 0.7, 0.008);
  orbGroup.add(scanRing1, scanRing2);

  // ═══════════════════════════════════════════════
  // HEXAGONAL NODES — small tech details
  // ═══════════════════════════════════════════════
  for (let i = 0; i < 15; i++) {
    const phi = Math.acos(2 * Math.random() - 1);
    const theta = Math.random() * Math.PI * 2;
    const r = R1 + 0.02;
    const hexGeo = new THREE.CircleGeometry(0.03 + Math.random() * 0.02, 6);
    const hexEdges = new THREE.EdgesGeometry(hexGeo);
    const hex = new THREE.LineSegments(hexEdges, lineMat(C_MID, 0.5));
    hex.position.set(
      r * Math.sin(phi) * Math.cos(theta),
      r * Math.cos(phi),
      r * Math.sin(phi) * Math.sin(theta),
    );
    hex.lookAt(0, 0, 0);
    outerShell.add(hex);
  }

  // ═══════════════════════════════════════════════
  // GESTURE / PROGRAMMATIC CAMERA CONTROL
  // ═══════════════════════════════════════════════
  const sphericalScratch = new THREE.Spherical();
  const offsetScratch = new THREE.Vector3();

  function rotateBy(deltaTheta: number, deltaPhi: number) {
    offsetScratch.copy(camera.position).sub(controls.target);
    sphericalScratch.setFromVector3(offsetScratch);
    sphericalScratch.theta -= deltaTheta;
    sphericalScratch.phi = THREE.MathUtils.clamp(
      sphericalScratch.phi - deltaPhi,
      0.05,
      Math.PI - 0.05,
    );
    sphericalScratch.makeSafe();
    offsetScratch.setFromSpherical(sphericalScratch);
    camera.position.copy(controls.target).add(offsetScratch);
    camera.lookAt(controls.target);
  }

  function zoomBy(factor: number) {
    offsetScratch.copy(camera.position).sub(controls.target);
    const dist = THREE.MathUtils.clamp(
      offsetScratch.length() * factor,
      MIN_DISTANCE,
      MAX_DISTANCE,
    );
    offsetScratch.setLength(dist);
    camera.position.copy(controls.target).add(offsetScratch);
  }

  function resetView() {
    camera.position.copy(HOME_POSITION);
    controls.target.set(0, 0, 0);
    camera.lookAt(controls.target);
    controls.update();
  }

  // ═══════════════════════════════════════════════
  // ANIMATION
  // ═══════════════════════════════════════════════
  // ═══════════════════════════════════════════════
  // AGENT STATE DRIVE
  // Nothing here snaps. Every visual quantity is a target that the frame loop
  // eases toward, so a state change reads as the orb *reacting* rather than
  // cutting. The one exception is the halt, which is meant to feel abrupt.
  // ═══════════════════════════════════════════════

  type TrackedMat = { mat: THREE.Material & { opacity: number }; base: number };

  /** Snapshot every material under `root` with the opacity it was authored at,
   *  so we can scale them as a group without losing the hand-tuned mix. */
  function collectMats(root: THREE.Object3D): TrackedMat[] {
    const out: TrackedMat[] = [];
    root.traverse((obj) => {
      const material = (obj as THREE.Mesh).material;
      if (!material) return;
      for (const mat of Array.isArray(material) ? material : [material]) {
        if (mat && "opacity" in mat) {
          out.push({ mat: mat as TrackedMat["mat"], base: mat.opacity });
        }
      }
    });
    return out;
  }

  const outerMats = collectMats(outerShell);
  // The core's own materials are animated per-frame below, so they are driven
  // explicitly rather than through the group multiplier — otherwise the two
  // would fight and the core would flicker.
  const structureMats = [
    ...outerMats,
    ...collectMats(panelGroup),
    ...collectMats(shell2),
    ...collectMats(innerCore),
  ];

  // ——— PULSE RINGS (listening / speaking) ———
  // Expand outward from the shell in time with audio amplitude.
  const PULSE_RING_COUNT = 3;
  const pulseRings: THREE.Line[] = [];
  {
    const pts: THREE.Vector3[] = [];
    for (let i = 0; i <= 128; i++) {
      const a = (i / 128) * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a), 0, Math.sin(a)));
    }
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    for (let i = 0; i < PULSE_RING_COUNT; i++) {
      const ring = new THREE.Line(geo, lineMat(C_BRIGHT, 0));
      ring.rotation.x = Math.PI / 2;
      ring.visible = false;
      pulseRings.push(ring);
      orbGroup.add(ring);
    }
  }

  const DANGER = new THREE.Color(0xff3b3b);
  const coreBaseColor = coreSphereMat.color.clone();
  const glowBaseColor = glowSphereMat.color.clone();
  const icoBaseColor = icoWireMat.color.clone();

  const drive = {
    state: "idle" as OrbState,
    // eased values
    spin: 1,
    bloom: 1.6,
    core: 1,
    ring: 0,
    halt: 0,
    amplitude: 0,
    confidence: 1,
    // raw inputs
    targetAmplitude: 0,
    targetConfidence: 1,
    // boot
    bootProgress: 1,
    booting: false,
  };

  /** Frame-rate independent easing — a fixed lerp factor would ease at
   *  different speeds on a 60Hz and a 120Hz display. */
  function approach(current: number, target: number, rate: number, dt: number) {
    return current + (target - current) * (1 - Math.exp(-rate * dt));
  }

  function setState(state: OrbState) {
    drive.state = state;
  }

  function setAmplitude(value: number) {
    drive.targetAmplitude = Math.min(1, Math.max(0, value));
  }

  function setConfidence(value: number) {
    drive.targetConfidence = Math.min(1, Math.max(0, value));
  }

  // ——— SUIT-UP BOOT SEQUENCE (§4 differentiator #1) ———
  // Shells assemble from scattered fragments; the core ignites last.
  const BOOT_MS = 2200;
  const bootLayers: { group: THREE.Group; offset: THREE.Vector3; delay: number }[] = [
    { group: outerShell, offset: new THREE.Vector3(0, 0, 0), delay: 0.30 },
    { group: panelGroup, offset: new THREE.Vector3(0, 0, 0), delay: 0.22 },
    { group: shell2, offset: new THREE.Vector3(0, 0, 0), delay: 0.14 },
    { group: innerCore, offset: new THREE.Vector3(0, 0, 0), delay: 0.06 },
  ];
  for (const layer of bootLayers) {
    // Scatter direction is deterministic per layer, not random per load —
    // the sequence should look the same in every take of the demo video.
    const seed = layer.delay * 37.7;
    layer.offset.set(Math.cos(seed) * 9, Math.sin(seed * 1.7) * 6, Math.sin(seed) * 9);
  }

  const easeOutExpo = (x: number) => (x >= 1 ? 1 : 1 - Math.pow(2, -10 * x));

  let bootResolve: (() => void) | null = null;

  function playBoot(): Promise<void> {
    drive.bootProgress = 0;
    drive.booting = true;
    setState("boot");
    return new Promise<void>((resolve) => {
      bootResolve = resolve;
    });
  }

  function applyBoot() {
    const p = drive.bootProgress;
    for (const layer of bootLayers) {
      // Each layer runs its own sub-window of the timeline, so they land in
      // sequence (core first, outer shell last) rather than all at once.
      const local = Math.min(1, Math.max(0, (p - layer.delay) / (1 - layer.delay)));
      const eased = easeOutExpo(local);
      layer.group.position.copy(layer.offset).multiplyScalar(1 - eased);
      const scale = 0.35 + 0.65 * eased;
      layer.group.scale.setScalar(scale);
    }
    // Core ignition is the last beat — held dark until the shells have landed.
    const ignition = Math.min(1, Math.max(0, (p - 0.72) / 0.28));
    drive.core = ignition * (1 + (1 - ignition) * 2.5); // brief overshoot = flash
  }

  function clearBoot() {
    for (const layer of bootLayers) {
      layer.group.position.set(0, 0, 0);
      layer.group.scale.setScalar(1);
    }
  }

  const clock = new THREE.Clock();
  let flickerTimer = 0;
  let rafId = 0;
  let disposed = false;

  let renderingEnabled = true;

  function setRendering(enabled: boolean) {
    if (enabled === renderingEnabled) return;
    renderingEnabled = enabled;
    if (enabled) {
      // getDelta() has been accumulating while paused; drop that interval so
      // the orb resumes where it left off instead of jumping.
      clock.getDelta();
      animate();
    }
  }

  function animate() {
    if (disposed || !renderingEnabled) return;
    rafId = requestAnimationFrame(animate);
    const dt = Math.min(0.05, clock.getDelta()); // clamp: tab-switch spikes
    const t = clock.getElapsedTime();

    // ——— advance the state drive ———
    if (drive.booting) {
      drive.bootProgress = Math.min(1, drive.bootProgress + (dt * 1000) / BOOT_MS);
      applyBoot();
      if (drive.bootProgress >= 1) {
        drive.booting = false;
        clearBoot();
        setState("idle");
        bootResolve?.();
        bootResolve = null;
      }
    }

    const profile = STATE_PROFILES[drive.state];
    // The halt is the one transition allowed to feel abrupt.
    const rate = drive.state === "halted" ? 9 : 3.2;
    drive.spin = approach(drive.spin, profile.spin, rate, dt);
    drive.bloom = approach(drive.bloom, profile.bloom, rate, dt);
    drive.ring = approach(drive.ring, profile.ring, rate, dt);
    drive.halt = approach(drive.halt, drive.state === "halted" ? 1 : 0, rate, dt);
    drive.amplitude = approach(drive.amplitude, drive.targetAmplitude, 14, dt);
    drive.confidence = approach(drive.confidence, drive.targetConfidence, 2.5, dt);
    if (!drive.booting) drive.core = approach(drive.core, profile.core, rate, dt);

    const spin = drive.spin;

    // Outer shell rotation
    outerShell.rotation.y += 0.0015 * spin;
    outerShell.rotation.x = Math.sin(t * 0.08) * 0.05;

    // Panel group follows shell but with slight offset
    panelGroup.rotation.y += 0.0018 * spin;
    panelGroup.rotation.x = Math.sin(t * 0.08 + 0.5) * 0.04;

    // Secondary shell counter-rotates slowly
    shell2.rotation.y -= 0.001 * spin;
    shell2.rotation.z = Math.sin(t * 0.12) * 0.03;

    // Inner core — opposite, faster. Spins hardest while thinking, which is
    // what sells "working" without any text on screen.
    innerCore.rotation.y -= 0.005 * spin;
    innerCore.rotation.z += 0.002 * spin;
    innerCore.rotation.x = Math.cos(t * 0.1) * 0.08;

    // Innermost wireframe
    icoWire.rotation.x += 0.008 * spin;
    icoWire.rotation.y += 0.012 * spin;

    // Core pulse — dramatic surges but mostly transparent
    const wave1 = Math.sin(t * 1.2);
    const wave3 = Math.pow(Math.max(0, Math.sin(t * 0.4)), 5); // rare big surge
    const wave4 = Math.pow(Math.max(0, Math.sin(t * 0.7 + 2)), 8); // mega surge
    const fadeOut = Math.pow(Math.max(0, Math.sin(t * 0.25)), 3); // periodic full transparency
    const surge = wave3 * 1.5 + wave4 * 2.0;
    const coreScale = 1 + surge + Math.sin(t * 5) * 0.05;
    coreSphere.scale.setScalar(coreScale);
    // Opacity: mostly very low (0-0.15), sometimes fully transparent, brief bright on surge
    const coreOpacity = Math.max(
      0,
      (0.08 + wave1 * 0.05 + surge * 0.2) * (1 - fadeOut * 0.95),
    );
    coreSphereMat.opacity = Math.min(0.85, coreOpacity * drive.core);
    glowSphere.scale.setScalar(1 + surge * 0.8);
    glowSphereMat.opacity =
      Math.max(0, (0.03 + surge * 0.08) * (1 - fadeOut * 0.9)) * drive.core;
    // Icosahedron wireframe stays visible even when glow fades
    icoWire.scale.setScalar(1 + surge * 0.6);
    icoWireMat.opacity = Math.min(1, (0.5 + surge * 0.4) * drive.core);

    // Debris orbits
    debris.forEach((d) => {
      const u = d.userData as DebrisOrbit;
      const a = t * u.speed + u.phase;
      d.position.set(
        u.orbitR * Math.cos(a) * Math.cos(u.tiltX),
        u.orbitR * Math.sin(u.tiltX) * Math.sin(a * 0.8) + Math.sin(a * 0.3 + u.tiltZ) * 0.2,
        u.orbitR * Math.sin(a) * Math.cos(u.tiltZ),
      );
      d.rotation.x += 0.015;
      d.rotation.z += 0.01;
    });

    // Text drift
    const driftGroups: [THREE.Group, number][] = [
      [textOuter, 1],
      [textInner, 2],
      [textAmbient, 1.2],
    ];
    for (const [group, mult] of driftGroups) {
      group.children.forEach((sp) => {
        const u = sp.userData as SpriteDrift;
        u.theta += u.speed * mult;
        sp.position.set(
          u.r * Math.sin(u.phi) * Math.cos(u.theta),
          u.r * Math.cos(u.phi),
          u.r * Math.sin(u.phi) * Math.sin(u.theta),
        );
      });
    }

    // Scan rings sweeping
    const scanY1 = Math.sin(t * 0.4) * R1;
    scanRing1.position.y = scanY1;
    const scanS1 = Math.sqrt(Math.max(0, R1 * R1 - scanY1 * scanY1)) / R1;
    scanRing1.scale.set(scanS1, scanS1, 1);
    (scanRing1.material as THREE.MeshBasicMaterial).opacity = 0.2 * scanS1;

    const scanY2 = Math.sin(t * 0.6 + 2) * R3;
    scanRing2.position.y = scanY2;
    const scanS2 = Math.sqrt(Math.max(0, R3 * R3 - scanY2 * scanY2)) / R3;
    scanRing2.scale.set(scanS2, scanS2, 1);
    (scanRing2.material as THREE.MeshBasicMaterial).opacity = 0.15 * scanS2;

    // Dust rotation
    dustPoints.rotation.y += 0.0002;

    // Random flicker on some panels
    flickerTimer += 0.016;
    if (flickerTimer > 0.1) {
      flickerTimer = 0;
      panelGroup.children.forEach((p) => {
        if (Math.random() > 0.95) {
          p.visible = !p.visible;
        }
      });
    }

    // ——— state-driven opacity, rings and tint ———

    // Confidence maps to the outer shell (§4 #3). Floor at 0.35 so low
    // confidence reads as "thinner", never as a rendering fault.
    const shellMul = (0.35 + 0.65 * drive.confidence) * (1 - drive.halt * 0.6);
    for (const { mat, base } of outerMats) mat.opacity = base * shellMul;

    // Everything else dims together when halted, and while booting.
    const structureMul =
      (drive.booting ? easeOutExpo(Math.min(1, drive.bootProgress / 0.8)) : 1) *
      (1 - drive.halt * 0.55);
    if (structureMul < 0.999 || drive.halt > 0.001) {
      for (const { mat, base } of structureMats) mat.opacity = base * structureMul;
    }

    // Pulse rings ride the audio envelope outward.
    const ringGain = drive.ring * (0.25 + drive.amplitude * 0.75);
    for (let i = 0; i < pulseRings.length; i++) {
      const ring = pulseRings[i];
      if (ringGain < 0.02) {
        ring.visible = false;
        continue;
      }
      ring.visible = true;
      // Staggered phases so the rings chase each other rather than pulsing
      // in lockstep.
      const phase = (t * 0.55 + i / pulseRings.length) % 1;
      const radius = R1 * (0.85 + phase * 1.5);
      ring.scale.setScalar(radius);
      // Fade in at birth, out at death — no popping at either end.
      const envelope = Math.sin(phase * Math.PI);
      (ring.material as THREE.LineBasicMaterial).opacity = envelope * ringGain * 0.5;
    }

    // Halted: the core goes red and cold. Three materials, so this is cheap.
    if (drive.halt > 0.001) {
      coreSphereMat.color.copy(coreBaseColor).lerp(DANGER, drive.halt);
      glowSphereMat.color.copy(glowBaseColor).lerp(DANGER, drive.halt);
      icoWireMat.color.copy(icoBaseColor).lerp(DANGER, drive.halt);
    }

    // Bloom pulse — base from the state, breathing from the audio, scaled to
    // the canvas. UnrealBloomPass spreads in pixels, so a strength tuned for a
    // 1900px window swamps an 800px panel: the fine wireframe disappears into
    // glow and the orb reads as a bright disc. Scaling it back is what makes
    // the panel look like the same orb rather than a blown-out copy.
    bloom.strength =
      (drive.bloom +
        Math.sin(t * 0.8) * 0.3 * (1 - drive.halt) +
        drive.amplitude * drive.ring * 0.9) * bloomScale;

    // Update chromatic aberration time
    chromaticPass.uniforms.uTime.value = t;

    controls.update();
    composer.render();
  }

  animate();

  // ——— RESIZE ———
  function onResize() {
    const w = container.clientWidth;
    const h = container.clientHeight;
    // A collapsed or hidden pane reports 0, and sizing the renderer to zero
    // leaves the framebuffer permanently incomplete — WebGL then spams
    // GL_INVALID_FRAMEBUFFER_OPERATION and never recovers when the pane
    // returns. Keep the last good size instead.
    if (w === 0 || h === 0) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    composer.setSize(w, h);
    // Retune the glow for the new size. UnrealBloomPass spreads in pixels, so
    // the same strength is a halo at 760pt and a rumour at 1800pt.
    bloomScale = Math.max(0.5, Math.min(1, w / 1400));
    bloom.setSize(w, h);
  }
  window.addEventListener("resize", onResize);
  // The container can change size without the window doing so (a docked
  // preview pane, a split editor), which `resize` alone would miss.
  const resizeObserver = new ResizeObserver(onResize);
  resizeObserver.observe(container);

  // ——— CLEANUP ———
  function dispose() {
    disposed = true;
    cancelAnimationFrame(rafId);
    window.removeEventListener("resize", onResize);
    resizeObserver.disconnect();
    controls.dispose();
    scene.traverse((obj) => {
      const mesh = obj as THREE.Mesh;
      if (mesh.geometry) mesh.geometry.dispose();
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const mat of mats) {
        if (!mat) continue;
        const anyMat = mat as THREE.Material & { map?: THREE.Texture };
        anyMat.map?.dispose();
        mat.dispose();
      }
    });
    composer.dispose();
    renderer.dispose();
    renderer.domElement.remove();
  }

  return {
    rotateBy,
    zoomBy,
    zoomIn: () => zoomBy(0.65),
    zoomOut: () => zoomBy(1.55),
    resetView,
    setState,
    setAmplitude,
    setConfidence,
    setRendering,
    playBoot,
    getState: () => drive.state,
    dispose,
  };
}
