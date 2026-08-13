# KAVACH

**कवच** — *armor, a protective shield.*

A local-first voice and gesture AI presence for macOS. Say something, and it
reasons about whether it can answer instantly, whether a small local model will
do, or whether it needs Claude — then acts on your Mac through MCP, behind an
allowlist, a confirmation gate and a kill switch.

The wake word, speech-to-text, the voice, and the fast reasoning path all run
on-device. Nothing leaves the machine except the requests the router
deliberately escalates.

```bash
nvm use && cd apps/orb && npm ci && npm run dev   # the orb
cd brain && uv sync && uv run python -m kavach.voice
```

---

## What it actually does

```
 mic ─▶ wake word ─▶ Whisper ─▶ ROUTER ─┬─▶ deterministic handler   0.02 ms
                                        ├─▶ qwen3:4b (local)        ~0.9 s
                                        └─▶ Claude Agent SDK ─▶ MCP ─▶ macOS
                                                                 │
                                              ┌──────────────────┴───────┐
                                              │  kill switch             │
                                              │  app allowlist           │
                                              │  spoken confirmation     │
                                              │  JSONL action log        │
                                              └──────────────────────────┘
                                        ▼
                              Kokoro TTS ─▶ the orb reacts
```

**Measured on an M4 Pro:** 1171 ms from silence to first audio out for a local
turn (821 ms of that is Whisper `large-v3-turbo`). The cold first turn is
~7.7 s — reported rather than hidden, because it's what you actually
experience once.

---

## The part worth looking at

**The router.** Always calling Claude is slow and burns credit on "what time is
it"; routing a multi-step request to a 4B model produces confident nonsense. So
there are three tiers, and each boundary came from a measurement:

| Tier | When | Cost |
|---|---|---|
| Deterministic handler | the clock, the battery | **0.02 ms** |
| `qwen3:4b` via Ollama | simple, open-ended | ~877 ms |
| Claude Agent SDK + MCP | judgement, multiple steps, tools | ~7 s |

Two things I only learned by measuring:

- **qwen3:4b cannot answer "what time is it."** It spends 3.4 seconds
  explaining it has no access to the system clock. The obvious reading of
  "send simple intents to the small model" is wrong twice over — slower *and*
  unable to answer. Adding a deterministic tier took that turn from 9494 ms to
  1171 ms.
- **Its self-reported confidence is a constant** — exactly `0.95` on every
  call, right or wrong. The orb renders confidence as the outer shell's
  opacity, so passing that through would have shown a ring that never moves.
  The router derives confidence from *how* it decided and discards the field.

---

## Beyond the original spec

Six features built after the spec's five phases, each tested:

| | |
|---|---|
| **Speaker verification** | Confirmations require an affirmative answer **in your voice**. Resemblyzer, on-device, threshold calibrated at enrolment. Closes the hole where anyone in earshot could approve a delete. |
| **Gesture confirmation** | A held thumbs-up (0.8s, with a filling ring) approves without speaking. A bystander cannot supply a gesture from across the room. |
| **iPhone control** | `mirroir-mcp` drives a real iPhone through macOS iPhone Mirroring — no app on the phone. Gated per-tool, because its tools are device-scoped rather than app-scoped. |
| **Screen understanding** | "What's on my screen" reaches Peekaboo. A whole-screen capture is confirmed, because it sees apps deliberately kept off the allowlist. |
| **Memory + file search** | `sqlite-vec` + `nomic-embed-text`, one file, no server. Indexing is explicit, auditable and revocable. |
| **Multilingual** | Replies in the language you spoke — 8 languages including Hindi. The wake word stays English-only, because that is what its 99.15% recall measures. |

Setup steps that need you: [`SETUP.md`](SETUP.md).

---

## The guardrails are the point

An agent holding Accessibility, Automation and Screen Recording can click
anything on your screen. So the kill switch was built, tested and committed
**before any device-control code existed** — not added afterwards.

**Kill switch.** Halts in-flight work, SIGKILLs MCP subprocesses by process
group, then **latches disarmed**. No auto-recovery. Five surfaces, one latch:
global hotkey **⌃⌥⌘K**, a menu bar PANIC item, `kavach kill`, a Unix socket,
and the orb itself. The three unprivileged surfaces exist because the one
needing a permission is the one that can fail silently — the daemon self-tests
Input Monitoring at startup and reports `[NOT WORKING]` rather than pretending.

**Allowlist, not blocklist.** Safari, Notes, Calendar, Finder. An app nobody
thought of is denied by default. An action whose target app can't be
identified is denied too — if it can't be checked, it doesn't run.

**Tools that defeat the model are never offered.** `macos-accessibility` ships
a `Shell` tool. A shell command names no app, so the allowlist can't check it;
that's a complete bypass rather than an edge case. `Shell`, `agent` and
`Desktop` are refused outright, with no confirmation path that could unlock
them.

**Confirmation is fail-safe.** Destructive or externally visible actions are
spoken back and wait. Denials include: no confirmer, silence, a timeout, an
unparsed answer, and a kill switch firing mid-question. *"Sure"* and *"okay"*
are deliberately not affirmative — they appear far too readily in ordinary
speech to authorise a delete.

### The bug I'd tell you about in an interview

My first wiring listed `allowed_tools=["mcp__<server>__*"]`. That
auto-approves tools **before** the permission callback runs — so the kill
switch, the allowlist and the confirmation flow were all unreachable. The agent
still *appeared* to behave, because the system prompt asked it nicely.

It would have demoed perfectly and protected nothing. The SDK emitted a
`CanUseToolShadowedWarning`; the only reason I caught it was reading the
warning instead of filtering it. The enforcement point is now a `PreToolUse`
hook, verified live by watching a Mail script get denied — and by watching the
agent try `Bash` as a fallback and get denied too.

---

## Verified, not asserted

Every claim here has a command behind it.

| | |
|---|---|
| Tests | **250 Python + 13 TypeScript** — kill switch, router, gate, confirmation, voiceprint, gestures, devices, memory |
| Kill switch | live: task cancelled, child `exit -9`, latched, all 5 surfaces |
| Gate | live denials for a non-allowlisted app, `Shell`, and `Bash` |
| Wake word | trained from scratch: **recall 0.9915, FPPH 0.00** at threshold 0.18 |
| MCP servers | **4/4** handshake + a real privileged call each |
| Full loop | speech → router → Claude → AppleScript → Finder → spoken reply |

One test reads `apps/orb/lib/kavachState.ts` and asserts the Python snapshot
matches the TypeScript interface field-for-field — drift there would fail
silently at runtime, with the HUD rendering a stale value forever.

---

## Built on

[Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) ·
[whisper.cpp](https://github.com/ggerganov/whisper.cpp) (Metal) ·
[Kokoro TTS](https://github.com/thewh1teagle/kokoro-onnx) ·
[Ollama](https://ollama.com) + Qwen3 4B ·
[livekit-wakeword](https://github.com/livekit/livekit-wakeword) ·
[macos-automator-mcp](https://github.com/steipete/macos-automator-mcp) ·
[MacOS-MCP](https://github.com/CursorTouch/MacOS-MCP) ·
[Peekaboo](https://github.com/openclaw/Peekaboo) ·
MediaPipe Tasks Vision · Three.js · Next.js 16

The orb is forked from [ULTRON Orb UI](https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds)
by **Sagar Tamang** (MIT, commit `a65306f`) — see
[`docs/attribution.md`](docs/attribution.md).

**Porcupine is not in this list.** Spec'd as the wake word, but Picovoice
discontinued its free tier on 2026-06-30 with no non-commercial replacement,
so KAVACH trains its own instead.

---

## Layout

```
apps/orb/     Next.js 16 + Three.js — Presence
brain/        Python (uv) — killswitch, voice, reasoning, hands, bridge
hands/        MCP configs, app allowlist, probe scripts
daemon/       launchd Launch Agent (not installed by default)
docs/         permissions-setup, demo-script, attribution
```

**Node 24 is mandatory** — `@steipete/macos-automator-mcp` declares
`engines: node >=24`. Pinned via `.nvmrc`.

macOS permissions: [`docs/permissions-setup.md`](docs/permissions-setup.md).
Working agreement: [`CLAUDE.md`](CLAUDE.md).
