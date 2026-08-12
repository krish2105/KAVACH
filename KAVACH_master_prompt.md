# KAVACH — Master Prompt & Technical Specification
### A local-first, gesture-and-voice AI presence for macOS
**Prepared for:** Krishna Mathur · **Target machine:** MacBook Pro (M-series) · **Status:** Ready to hand to Claude Code

---

## 0. What this document is

This is a single, self-contained build spec. Paste sections of it directly into Claude Code (there's a literal kickoff prompt in §10) and it can scaffold, build, and iterate the whole system phase by phase. Nothing here is a placeholder — every tech choice below was checked against August 2026 sources before being written down.

**Why the name KAVACH:** कवच (Sanskrit) — armor, a protective shield. Same idea as an Iron Man suit, but it's your own word, not Marvel's IP — which matters the moment this becomes a portfolio piece or a demo video with your name on it. In the same spirit as Wasla (Arabic) and GALDR (Norse), this keeps your naming pattern: a real word from a real language, carrying the right meaning. Rename it in five seconds if you want something else — nothing below depends on the name.

---

## 1. What Ultron (the repo you linked) actually is, and what KAVACH does differently

The repo is a **front-end shell only**: Next.js + Three.js + MediaPipe hand tracking, rendering a holographic orb you spin and zoom with pinch gestures. No backend, no LLM, no voice, no device control — those pieces are explicitly proprietary/unreleased on the original project.

KAVACH takes that same visual language (it's genuinely good — keep it) and actually wires up the three things Ultron's demo *implies* but doesn't ship:

| Layer | Ultron repo | KAVACH |
|---|---|---|
| Orb UI | ✅ Open source | ✅ Reused + upgraded with premium-frontend polish |
| Voice agent | ❌ Proprietary, unreleased | ✅ Built, local-first |
| Device control | ❌ Proprietary, unreleased | ✅ Built on open MCP servers |

---

## 2. Architecture — three layers, one machine

*(see the diagram above this section)*

**Presence** — what you see and gesture at. Three.js orb, MediaPipe hand tracking, an ambient HUD.
**Brain** — what listens and decides. Wake word → speech-to-text → reasoning (routed between a fast local model and Claude) → text-to-speech.
**Hands** — what actually touches your Mac. MCP servers that execute AppleScript, Accessibility-API clicks, window management, and app control — with a permission layer in between.

All three run **on the MacBook itself**. Nothing needs to leave the machine except the calls you deliberately route to Claude's API (for the reasoning that's genuinely worth paying for). This is the same shape as the JARVIS spec we scoped together before — this document is the refreshed, orb-equipped V2 of that, checked against what's actually current in August 2026.

---

## 3. Full tech stack

| Component | Choice | Why |
|---|---|---|
| **Orb UI shell** | Next.js 16 + React 19 + Three.js (fork Ultron's `lib/orbScene.ts`, `lib/handTracker.ts`) | Already open source, MIT-licensed, proven |
| **UI polish layer** | `premium-frontend` skill (Motion v12, glassmorphism 2.0, bento HUD panels) + **21st MCP** for component generation | Turns the bare orb into a full HUD — transcript panel, tool-call log, status rings — without hand-building every component |
| **Wake word** | Picovoice **Porcupine** (free personal tier) | Always-listening without running STT continuously; on-device, near-zero CPU |
| **Speech-to-text** | **Whisper.cpp with Metal acceleration** (or MLX Whisper) | Sub-second transcription on M-series Silicon, fully offline |
| **Fast local reasoning** | **Ollama** running a small model (Qwen3 4B / Llama 3.2 3B) via MLX backend | Handles simple intents ("what time is it," "open Safari") in under a second, no API cost, no network round-trip |
| **Deep reasoning + tool use** | **Claude Agent SDK** (`claude-agent-sdk`, Python or TypeScript) | Same agent loop that powers Claude Code — file ops, bash, MCP tools, subagents, all inside your own app. Draws from your Claude Pro/Max plan's monthly Agent SDK credit, not your interactive chat limits |
| **Text-to-speech** | **Kokoro TTS (ONNX)** | Piper was archived in Oct 2025; Kokoro is the current best-in-class local voice, natural-sounding, small footprint |
| **Device control (MCP)** | `macos-automator-mcp` (AppleScript/JXA, 200+ prebuilt recipes) + `CursorTouch/MacOS-MCP` (Accessibility API, no-vision UI control) + **Peekaboo** (`@steipete/peekaboo`, screen-see-and-click for apps with no scriptable dictionary) | Three complementary layers of macOS control: scriptable apps, native UI elements, and screen-based fallback |
| **Orchestrator** | Python (`asyncio`) or Node — whichever you're faster in; Python has the cleaner MLX/Whisper/Kokoro bindings | Glue between wake word, STT, the reasoning router, TTS, and MCP dispatch |
| **Background service** | `launchd` daemon (macOS's native service manager) | Keeps the wake-word listener running at login without a Terminal window open |
| **Voice option B (paid, optional)** | OpenAI Realtime API (GPT-Realtime-2.1, WebRTC) | Only if you want cloud-grade speech-to-speech with near-zero latency instead of the STT→LLM→TTS chain — costs real money per minute, so treat as an optional "premium mode" toggle, not the default |

**Cost reality:** the entire stack above runs at **$0 marginal cost** except Claude API calls for deep reasoning (covered by your existing Claude plan's Agent SDK credit) and the optional OpenAI Realtime toggle. This matches your $0-infra instinct from Wasla.

---

## 4. Layer 1 — Presence (the orb)

**Base:** fork `lib/orbScene.ts`, `lib/handTracker.ts`, `components/JarvisOrb.tsx` from the Ultron repo directly (MIT license, so this is fully legitimate) — don't rebuild the wireframe shell and pinch detection from scratch, it's already solid.

**What you add on top, using the `premium-frontend` skill + 21st MCP:**
- A **glass HUD panel** (glassmorphism 2.0, used surgically — nav/status only, never the whole background) showing: live transcript, current agent state (`listening` / `thinking` / `acting` / `speaking`), and a scrolling log of tool calls as they happen
- **State-reactive orb behavior**: idle = slow ambient rotation; listening = pulse rings expand outward in time with mic amplitude; thinking = inner core spins faster + code-sprites flicker; speaking = bloom intensity pulses with TTS audio envelope
- Use the **21st MCP** (`npx @21st-dev/cli@latest install claude-code --api-key <key>`) inside Claude Code to generate the HUD panels, status pills, and settings drawer from natural-language prompts instead of hand-coding each one — it searches a 12,000+ component catalog and writes files straight into your project
- Keyboard shortcuts stay from the original repo (`G`, `R`, `+`/`-`) plus new ones: `Space` = push-to-talk override, `Esc` = interrupt/cancel current action

**Surprise-me additions (the differentiators):**
1. **"Suit-up" boot sequence** — a 2-second animated intro when the app launches: shells assemble from scattered fragments into the full orb, core ignites last. Pure Motion/Three.js choreography, no logic needed.
2. **Live tool-call visualization** — when the Brain dispatches an MCP tool call, a small glowing packet visibly travels from the orb's core out along a HUD line to the relevant panel, then back with the result. Makes the invisible agent loop *visible* — this is the single best demo moment for a portfolio video.
3. **Ambient ring = confidence, not just state** — the outer wireframe shell's opacity maps to the reasoning layer's confidence (a rough proxy: did it use the fast local model, or escalate to Claude?). Cheap to compute, reads as "smart."

---

## 5. Layer 2 — Brain (voice + reasoning)

**Pipeline:**
```
Porcupine (wake word, always-on, near-zero CPU)
   → triggers → Whisper.cpp (STT, Metal-accelerated)
   → text → Router
        ├─ simple intent (open app, check time, dictate note) → Ollama local model → direct MCP call
        └─ complex / multi-step / needs judgment → Claude Agent SDK
                → Agent SDK's own tool loop calls the MCP servers (§6)
                → returns final response text
   → Kokoro TTS → audio out → orb reacts to amplitude
```

**The router is the interesting engineering problem.** Don't just always call Claude — that's slow and burns credit for "what time is it." A simple heuristic first pass works fine: keyword/intent classification via the local model itself (cheap, one forward pass) decides whether to handle it locally or hand off. Log every routing decision during development so you can tune the split.

**Claude Agent SDK setup** (this is the part that makes KAVACH an *agent*, not a script):
```python
from claude_agent_sdk import query, ClaudeAgentOptions

options = ClaudeAgentOptions(
    system_prompt="You are KAVACH, a voice-controlled presence on this Mac...",
    mcp_servers={
        "macos-automator": {...},
        "macos-accessibility": {...},
        "peekaboo": {...},
    },
    allowed_tools=["mcp__macos-automator__*", "mcp__macos-accessibility__*"],
    permission_mode="ask",  # see §7 — never default this to auto-approve
)
async for message in query(prompt=transcribed_text, options=options):
    ...
```

**Interruption handling:** wire `Space` (push-to-talk override) and a spoken "stop" / "cancel" keyword to cancel the current Agent SDK stream and TTS playback immediately — an assistant that can't be interrupted stops feeling like a presence and starts feeling like a hung process.

---

## 6. Layer 3 — Hands (device control via MCP)

This is the layer that needs the most care, both technically and ethically. Three MCP servers, each covering a different reach:

| Server | Reaches | Install |
|---|---|---|
| `macos-automator-mcp` (steipete, now under openclaw) | Any scriptable app via AppleScript/JXA — Mail, Calendar, Reminders, Finder, Safari, Music, 200+ prebuilt recipes | `npx -y macos-automator-mcp` |
| `CursorTouch/MacOS-MCP` | Native UI elements via the macOS Accessibility API — window management, menu bar, Dock, Launchpad, Control Center | clone + `uv sync` (MIT, actively maintained) |
| **Peekaboo** (`@steipete/peekaboo`, openclaw org) | Screen-see-and-click for anything with *no* scriptable dictionary — the universal fallback | `npx -y @steipete/peekaboo` |

**Required macOS permissions** (grant once, in System Settings → Privacy & Security): Accessibility, Automation (per-app), Screen Recording (for Peekaboo). These are real, sensitive permissions — that's not a footnote, it's the whole reason §7 exists.

---

## 7. Safety guardrails — non-negotiable before you demo this to anyone

An agent with Accessibility + Automation permissions can click anything on your screen and script any app. That's the whole point, but it means the guardrails aren't optional polish — they're the difference between a portfolio piece and a story about the time your assistant sent an email it shouldn't have.

- **Confirm before anything destructive or externally visible**: sending a message, deleting a file, submitting a form, making a purchase, changing a system setting. KAVACH should speak the action back to you and wait for a spoken or Space-bar confirmation — mirror the same "ask before send/delete/purchase" boundary a well-behaved agent should always have.
- **App allowlist, not a blanklist**: start with a short list of apps KAVACH is allowed to control (Safari, Notes, Calendar, Finder) and add to it deliberately, rather than granting blanket control day one.
- **Full action log**: every tool call, every argument, timestamped, written to a local log file. When something goes wrong (and something will, early on), you need to see exactly what it tried to do.
- **A visible "kill switch"**: one keyboard shortcut that immediately halts any in-flight action, no matter what layer it's in. This should be the very first thing you build and test, before anything else in §6.
- **Never let the wake-word/always-listening layer log or transmit audio it didn't act on.** Local-first isn't just a cost decision here — it's a privacy commitment worth actually keeping, especially if you demo this to recruiters and someone asks.

---

## 8. Repo structure

```
kavach/
├── apps/
│   └── orb/                    # Next.js — forked from Ultron, Presence layer
│       ├── lib/orbScene.ts
│       ├── lib/handTracker.ts
│       ├── components/         # HUD panels (21st MCP generated)
│       └── components/JarvisOrb.tsx
├── brain/                      # Python — Brain layer
│   ├── wake_word.py            # Porcupine listener
│   ├── stt.py                  # Whisper.cpp wrapper
│   ├── router.py                # local-vs-Claude decision logic
│   ├── agent.py                 # Claude Agent SDK integration
│   └── tts.py                  # Kokoro wrapper
├── hands/                      # MCP server configs + custom tool wrappers
│   └── mcp.config.json
├── daemon/
│   └── com.krishna.kavach.plist  # launchd service definition
├── docs/
│   ├── demo-script.md          # for your portfolio video
│   └── permissions-setup.md
└── README.md
```

---

## 9. Sprint plan — 10 weeks

| Phase | Weeks | Deliverable |
|---|---|---|
| **0 — Setup** | 1 | Repo scaffolded, Ultron orb forked and running locally, MCP servers installed and permission-granted, kill switch wired and tested first |
| **1 — Presence polish** | 2–3 | HUD panels via 21st MCP + premium-frontend, state-reactive orb, suit-up boot sequence |
| **2 — Local voice loop** | 4–5 | Porcupine → Whisper.cpp → Kokoro round-trip working end to end, no LLM yet — just echo/dictation, get latency right first |
| **3 — Brain + router** | 6–7 | Ollama local model wired for simple intents; Claude Agent SDK wired for complex ones; router tuned |
| **4 — Hands + guardrails** | 8 | All three MCP servers live, allowlist enforced, confirmation flow working, action log in place |
| **5 — Integration + demo** | 9–10 | Full loop tested end to end, tool-call visualization polished, demo video recorded, README + portfolio write-up |

---

## 10. Claude Code kickoff prompt

Paste this directly into Claude Code in an empty directory to start Phase 0:

```
I'm building KAVACH — a local-first voice + gesture AI presence for my
MacBook Pro. Full spec is in KAVACH_master_prompt.md in this repo (paste
this file in first). Start with Phase 0 only:

1. Scaffold the monorepo structure from §8.
2. Clone/fork the orb UI from https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds
   (MIT licensed) into apps/orb — get `npm run dev` working first, unmodified.
3. Set up the three MCP servers from §6 (macos-automator-mcp, CursorTouch/MacOS-MCP,
   Peekaboo) and walk me through the macOS permission grants they each need.
4. Before touching any device-control logic, implement and test the kill switch
   from §7 — a single keyboard shortcut that halts any in-flight MCP action.
   Nothing in Phase 1+ starts until this works.

Ask me before installing anything that needs payment or account creation
(Picovoice/Porcupine API key, any paid tier). Everything else in this phase
should be free and local.
```

Use `/ui` or plain natural language with the 21st MCP once you reach Phase 1 — e.g. *"generate a glass HUD panel showing live transcript and agent status, dock it bottom-left of the orb canvas."*

---

## 11. Portfolio packaging (this matters for your job hunt)

This project demonstrates a different skill set than Wasla (product/business) or GALDR (game dev) — it's **agentic systems + multimodal HCI + local ML deployment**, which is exactly the kind of thing an AI/ML Analyst or AI Associate interviewer wants to see you've actually shipped, not just studied.

For the README and demo video:
- Lead with the tool-call visualization moment (§4, differentiator #2) — it's the part that makes "agent" visible instead of abstract
- Be explicit in the write-up about the safety guardrails in §7 — a recruiter who sees you thought about the permission model *before* the demo will trust the rest of the project more, not less
- Name-check the real building blocks (Claude Agent SDK, MCP, Whisper.cpp, MediaPipe) rather than just "AI assistant" — specificity signals you understand what's under the hood

---

## 12. Known limitations, stated up front

- Local STT/TTS quality is good but not cloud-grade — Whisper.cpp handles clear speech well, struggles more with background noise or fast speech; Kokoro is natural but not indistinguishable from a human voice yet.
- Accessibility-API and screen-based control (Peekaboo) are inherently slower and more brittle than a native API — some apps will need trial-and-error recipe-building.
- The OpenAI Realtime API option in §3 is genuinely lower-latency for pure conversation, but it's cloud, it's paid per minute, and it breaks the local-first privacy story — treat it as an optional toggle, not the backbone.
- macOS permission dialogs (Accessibility, Automation, Screen Recording) will interrupt setup more than once during development — budget time for it, especially first-time on a fresh permission grant per app.
