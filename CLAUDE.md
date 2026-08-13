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
| 2 — Local voice loop (push-to-talk → Whisper → Kokoro → orb) | **complete**; wake-word model still training |
| 3 — Brain + router | not started |
| 4 — Hands + guardrail enforcement | not started |
| 5 — Integration + demo | not started |

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
