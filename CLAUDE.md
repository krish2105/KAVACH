# KAVACH — working agreement

KAVACH is a local-first voice + gesture AI presence for macOS. Full spec:
[`KAVACH_master_prompt.md`](KAVACH_master_prompt.md). Three layers — **Presence**
(Three.js orb), **Brain** (wake word → STT → router → TTS), **Hands** (macOS
control via MCP).

**Do not deviate from the spec's architecture, tech stack, or phase order without
saying why first.** The spec is the contract; this file is how we work on it.

---

## §A — Accuracy

For any library or SDK not verified **in the current session** — Kokoro ONNX,
MediaPipe Tasks Vision, Porcupine, Claude Agent SDK, pyobjc, any MCP server
config — check the actual repo or docs before writing integration code.

Do not guess function signatures, config shapes, or CLI flags. If unsure, say
**"I need to check X"** and look it up. Wrong-but-confident code here is worse
than a pause to verify.

If the master prompt conflicts with current docs (library renamed, API changed,
package archived), **report the discrepancy and propose a fix** — never silently
substitute.

### Verified dependency state (checked 2026-08-13, this machine)

| Package | Verified | Notes |
|---|---|---|
| Ultron orb repo | MIT, pushed 2026-07-15 | Next `^16.2.10`, React `^19.2.7`, three `^0.185.1` |
| `@steipete/macos-automator-mcp` | 0.4.6 | **node >=24**. Repo is `steipete/`, NOT openclaw |
| `@steipete/peekaboo` | 4.0.0 | **node >=22**. Repo moved to `openclaw/Peekaboo` |
| `macos-mcp` (CursorTouch) | 0.3.17, PyPI | `uvx macos-mcp`, py >=3.11. Needs Accessibility **+ Screen Recording** |
| `claude-agent-sdk` (py) | 0.2.137 | py >=3.10 |
| `pyobjc-framework-Cocoa`/`-Quartz` | 12.2.2 | Used for hotkey + menubar |
| `pvporcupine` | 4.0.3 | ⚠️ **DEAD END — see below** |
| `next` | 16.3.0 | patched; 0 advisories |
| `motion` | 13.1.0 | spec §4 says "v12"; 13 is current, `motion/react` import unchanged |

**⚠️ Porcupine is out.** Picovoice **discontinued the free tier on 2026-06-30**
and disabled existing free AccessKeys: *"we'll be focusing on our core business,
enterprise deployments. There is no non-commercial tier planned."* Signup now
yields a 7-day enterprise trial. This kills spec §3's "Porcupine (free personal
tier)". Phase 2 must pick a replacement — candidates are **openWakeWord**
(Apache-2.0, ONNX, but last PyPI release 2024-02), **livekit-wakeword** (newer,
openWakeWord-compatible ONNX), and **local-wake** (no training). **Verify all
three before choosing — this is not yet decided.**

**Corrections already applied to the spec:** §6/§10's `npx -y macos-automator-mcp`
is a 404 (needs the `@steipete/` scope); §6's "now under openclaw" applies to
Peekaboo only; `CursorTouch/MacOS-MCP` ships on PyPI so no clone+`uv sync` is
needed; the spec states no Node floor but two servers require ≥24/≥22.

**Node is pinned to 24 via `.nvmrc`.** Run `nvm use` before any npm work. The
user's global nvm default stays on 20 — do not change it.

---

## §B — Verification, not assertion

- After every phase, show **the actual command and its output**. Never "this
  should work now."
- For anything with clear pass/fail behaviour — the router, the kill switch, MCP
  permission gating — **write the test first, show it failing, then implement
  until it passes.**
- **Never modify a test to make it pass.** If a test looks wrong, flag it and
  wait. Changing a test to go green is the one thing that silently destroys the
  value of every other test in the repo.
- **Commit after every phase** as a rollback checkpoint. Tag phase completions.

---

## §C — Guardrails (spec §7 — not optional)

- **The kill switch is built and tested before any device-control code exists.**
  It halts in-flight actions and **latches disarmed** — no auto-recovery. An
  ambiguous state stays stopped. `KillSwitch.guard()` gates every action path.
- **Every MCP tool call that is destructive or externally visible needs explicit
  user confirmation** — sending, deleting, purchasing, submitting a form,
  changing a system setting. KAVACH speaks the action back and waits.
- **Never set `permission_mode` to auto-approve** in `ClaudeAgentOptions`. Use
  `"ask"`. This is not a tunable.
- **App allowlist, not a blocklist.** Starts at Safari, Notes, Calendar, Finder
  (`hands/allowlist.json`). **Ask before expanding it.**
- **Full action log**: every tool call, every argument, timestamped, local JSONL.
  Logs are gitignored — they record everything the agent touched.
- **Never log or transmit wake-word audio that wasn't acted on.**

---

## §D — Scope discipline

- **One phase at a time, in spec §9 order.** Stop at the end of each phase and
  wait for review. Do not cascade forward on momentum.
- **Ask before anything needing payment or account creation** — Picovoice
  AccessKey, 21st MCP key, any cloud service. Everything else in this stack is
  free and local; keep it that way unless told otherwise.

---

## Phase status

| Phase | State |
|---|---|
| 0 — Setup, orb forked, MCP installed, kill switch tested | **complete** (tag `phase-0`) |
| 1 — Presence polish (HUD, state-reactive orb, boot sequence, packets) | **complete** (tag `phase-1`) |
| 2 — Local voice loop (wake word → Whisper → Kokoro → orb) | **complete**; wake word **v2** in use — see below |
| 3 — Brain + router | **complete** (tag `phase-3`) |
| 4 — Hands + guardrail enforcement (gate, allowlist, spoken confirm) | **complete** (tag `phase-4`) |
| 5 — Integration + demo | **complete** (tag `phase-5`); demo video is yours to record |

**Wake word v2** (2026-08-13): AUT 0.0040, FPPH 0.00, recall 83.8%, measured
optimum **0.20**. v1's headline recall was higher (99.15%) and *meaningless* —
trained on one American voice, it scored the user's real utterances 0.027–0.571
against 0.789 for an unrelated phrase. v2 uses accent-diverse VoxCPM synthesis.
`find_wake_model()` now takes the newest export, and a calibration carries a
content hash of the model it measured — a threshold from a different model is
refused, not applied. **Still uncalibrated**: `uv run kavach-waketune` needs the
user's voice.

### Reach phases (the user's second numbering — restarts at 6)

Tags are `reach-N`, because `phase-6/7/8` were already taken by the earlier
expansion numbering. **Do not re-propose anything marked cut or blocked.**

| Phase | State |
|---|---|
| 6 — Local API surface | **complete** (tag `reach-6`) — FastAPI on 127.0.0.1:8770, bearer token, pending-confirmation flow |
| 7 — The phone commands KAVACH | **complete** (tag `reach-7`) — two Apple Shortcuts, `POST /kill`, Tailscale Serve |
| 8 — Apple Watch | **CUT — the user owns no Apple Watch.** Also: Tailscale has **no watchOS app** (iOS/iPadOS/tvOS/visionOS only), and `Get Contents of URL` is unreliable on watchOS. A Watch app would need the iPhone as a WatchConnectivity relay, hence Xcode |
| 9 — Remote access | in progress — mostly delivered by Phase 7's transport; needs the tailnet leg proven |
| 10 — Tiered memory | mostly built (`kavach/memory/store.py`, sqlite-vec). **Never index screen content or ambient audio** — the user cut that explicitly as a privacy/storage liability |
| 11 — Smart home | **CUT — the user owns no smart-home devices** |
| 12 — Speaker ID | built (Resemblyzer, threshold 0.613, margin 0.196). The **config toggle** is the missing piece. Eagle comparison needs a **paid Picovoice contract — ask before signing up** |
| 13 — Explainability | **complete** — `reason`/`intent` in the snapshot and the STATUS panel. `respond()` now publishes its decision; before, an API turn computed and logged a route the HUD never saw |
| 14 — Ghost mode | **complete** — `kavach/privacy/ghost.py`. Stops mic **and** camera, suppresses *perception* logging only |
| 15 — Meeting-aware muting | **complete** — `kavach/privacy/meetings.py`. Window-title detection; needs a real call to finish verifying |
| 16 — Session recorder | **complete** — `kavach/memory/session.py`. Rolling 15 min, `kavach-export`, no network path |
| 17 — Desktop widget | **complete as a menubar** — WidgetKit needs Xcode. `status_title()` in `presence/controls.py` |
| 18 — Multi-Mac awareness | **complete** — `kavach/single.py`. Lock + heartbeat, ownership per lock *object* not per PID |
| 19 — Dynamic Island / Live Activities | **BLOCKED — needs a native app, which needs Xcode.** Also needs push for remote updates, which needs the paid membership |
| 20 — Garage mode | not started; depends on 9. Get the user's definition before planning |

**CarPlay was cut by the user and must not be built.**

### One instance, enforced by a file (§18 extended)

`InstanceLock(name)` in `kavach/single.py`; `WakeWordLock` is a thin alias.
The overlay takes `~/.kavach/overlay.lock` and a second one **exits 1** naming
the holder.

Added after four overlay processes drew two panels on top of each other. Every
duplicate-instance bug in this project has come from `pgrep`/`pkill` patterns
not matching the process name — **do not add another one.** A lock the process
takes itself does not care what the command line looks like.

**A LaunchAgent respawns the overlay.** `com.krishna.kavach.overlay` is loaded
with KeepAlive, so killing the overlay brings it straight back and a manual
`kavach-overlay` stacks on top of it — that is where the duplicate panels
actually came from. To test a variant, `launchctl bootout
gui/$UID/com.krishna.kavach.overlay` first and bootstrap it back afterwards.
**Never `rm ~/.kavach/overlay.lock` while an instance is running** — that is
the one action that defeats the guard.

### Full screen (`⌃⌥⌘F`, or `kavach-overlay --fullscreen`)

Full screen is opaque, not a bigger transparent panel. Three things had to
change together, and all three were separately invisible as bugs:

* `html.kv-overlay.kv-fullscreen` restores an opaque black ground and the
  vignette/grain/scanline layers overlay mode strips. CSS alone is not enough —
  the NSWindow is also set opaque, or the desktop composites through anything
  not fully opaque, which is most of a glowing orb.
* The camera drops `PANEL_MARGIN` (1.12). That pull-back exists because the
  floating panel is *square* and crops the orb; full screen is 1800x1169 and
  the margin just left the orb small.
* **`bloomScale` is recomputed on resize.** It was a `const` fixed at scene
  creation, so a 760pt panel going full screen kept a bloom tuned for 760pt —
  the orb filled the display with the glow of a thumbnail.

Related: `.kv-overlay *` sets `-webkit-user-drag: none`. Dragging the TALK
button out of the WKWebView wrote 1.3 MB `.textClipping` files to the Desktop.
`user-select: none` does not prevent this and was only on `.hud` anyway.

`OverlayWindow.probe()` also checks whether the page is *styled* and reloads
(max 3) if not — a page whose CSS 404'd renders as unstyled text and WKWebView
keeps showing it forever.

### Ghost mode — the boundary that matters (§14)

Ghost suppresses **perception**, never **action**. `ActionLog.SUPPRESSED_IN_GHOST`
lists only `router.decision`, `voice.turn`, `voice.rejected` — the events
carrying what KAVACH heard or saw.

It was originally the other way round (suspend everything, exempt a few), and
**live testing found a typed API command reaching a tool with no log record at
all**. §7 says every tool call and argument is recorded; ghost mode does not get
to suspend that. A deny-list is also the right shape here: with an allowlist, a
*new* event defaults to hidden, and the events most worth adding are the ones
recording KAVACH doing something.

**The camera lives in the presence process, not the brain.** `GhostMode` in the
voice loop can only stop the mic; `privacy/camera_gate.py` closes it from the
other side, driven by the same snapshot stream the menu bar uses. The unit
tests missed this for a while because they attached a *fake* tracker in-process
— green tests, live camera.

### Apple constraints verified 2026-08-13 (do not re-derive)

* **No Xcode on this machine** — Command Line Tools only, no Simulator. Any
  native iOS/watchOS work starts with a ~15GB install. The user declined it.
* **Free personal team**: 7-day provisioning profiles, 3 apps, 10 App IDs/week,
  and Xcode **refuses App Groups, Push Notifications and iCloud** outright.
* **No push ⇒ phone approvals are pull-only.** Nothing can wake the phone when
  KAVACH needs an answer, and confirmations expire after 120 s.
* `tailscale serve --bg 8770` — auto TLS, no admin console setup, survives
  reboots, works on the Mac App Store build.

---

## Layout (spec §8)

```
apps/orb/    Next.js — forked from Ultron (MIT), Presence layer
brain/       Python (uv) — killswitch/, later wake_word/stt/router/agent/tts
hands/       MCP server configs, allowlist
daemon/      launchd plist
docs/        permissions-setup.md, demo-script.md, attribution.md
```

**Python:** `uv` in `brain/`. **Node:** `nvm use` (24) in `apps/orb/`.

### Hands layer notes (Phase 4) — READ BEFORE TOUCHING THE GATE

- **The enforcement point is the `PreToolUse` hook, NOT `can_use_tool`.**
  Verified live: a wildcard in `allowed_tools` auto-approves before
  `can_use_tool` (the SDK raises `CanUseToolShadowedWarning`), and with
  `allowed_tools` empty the CLI's interactive prompt intercepts instead and
  dies with "AbortError: Stream closed" in a headless loop. The hook fires in
  both cases. **A test asserts the hook is wired for all tools — do not
  remove it.**
- `NEVER_ALLOWED_TOOLS` (`Shell`, `agent`, `Desktop`) are refused outright and
  not exposed. A shell command names no app, so the allowlist cannot check it
  — it is a complete bypass of §7, not an edge case. Confirmation is never
  offered for these.
- Denial is the default at every step: unknown server, unidentifiable target
  app, missing confirmer, unclear spoken answer, timeout. Consent must be
  given, never merely not-withheld.

### Voice layer notes (Phase 2)

- **cmake is NOT needed** and **ollama is NOT a Phase 2 dep** — earlier notes
  in this file said otherwise. `pywhispercpp` ships prebuilt arm64 wheels, and
  §9 puts the LLM in Phase 3.
- **`livekit-wakeword` 0.2.1 has 7 undeclared dependencies** (found by AST
  scan): typer, pyyaml, pydantic, torchaudio, nltk, pronouncing, webrtcvad.
  Four are pinned in the `wakeword-training` group. Its CLI is unusable
  without them. The *inference* API is fine.
- Training also needs **system espeak-ng** (`brew install espeak-ng`). Kokoro
  does not — it bundles its own via `espeakng-loader`.
- `VoiceState.as_dict()` must stay field-identical to `KavachSnapshot` in
  `apps/orb/lib/kavachState.ts`. `tests/test_voice.py` reads the TypeScript
  and asserts this; **do not weaken that test** — drift fails silently.
- Audio is never written to disk and is dropped after every turn (§7).
  `mic.py` deliberately has no save path.
- The bridge binds **127.0.0.1 only**. It can trigger the kill switch.

### Presence layer notes (Phase 1)

- `lib/orbScene.ts` owns the visuals; `OrbSceneApi` now takes `setState`,
  `setAmplitude`, `setConfidence`, `playBoot`. The scene **never** owns agent
  state — it renders whatever the snapshot says.
- `lib/kavachState.ts` is the contract between Presence and the Brain.
  Phase 3 adds a WebSocket `KavachSource` beside `createMockSource()` and
  deletes nothing. **Do not let the HUD talk to anything but a `KavachSource`.**
- The palette lives in **two** places that must change together: `C_*`
  constants in `orbScene.ts` and `--kv-*` custom properties in `globals.css`.
- 21st MCP is connected on the **free tier: 2 component retrievals/day**
  (search is unmetered). Ask before spending one.
- The HUD's `K` key is a *stand-in* for the kill switch. Phase 4 bridges the
  real daemon socket into the browser; until then the browser knows nothing
  about the Python latch.
