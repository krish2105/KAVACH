# KAVACH

**कवच** — *armor, a protective shield.*

A local-first voice + gesture AI presence for macOS. Three layers, one machine:

- **Presence** — a Three.js holographic orb with MediaPipe hand tracking
- **Brain** — wake word → speech-to-text → a router that splits work between a
  fast local model and Claude → text-to-speech
- **Hands** — real macOS control through MCP servers, behind a permission layer

Nothing leaves the machine except the reasoning deliberately routed to Claude.

Full specification: [`KAVACH_master_prompt.md`](KAVACH_master_prompt.md).
Working agreement: [`CLAUDE.md`](CLAUDE.md).

---

## Status

| Phase | State |
|---|---|
| **0 — Setup, orb forked, MCP installed, kill switch tested** | **complete** |
| **1 — Presence polish (HUD, state-reactive orb, boot sequence)** | **complete** |
| 2 — Local voice loop (wake word → Whisper.cpp → Kokoro) | not started |
| 3 — Brain + router | not started |
| 4 — Hands + guardrail enforcement | not started |
| 5 — Integration + demo | not started |

---

## The kill switch comes first

An agent holding Accessibility, Automation, and Screen Recording can click
anything on this Mac. So the kill switch was built, tested, and committed
**before** a single line of device-control code existed — not as polish
afterwards.

It **halts and latches disarmed**. Triggering cancels in-flight async work and
SIGKILLs registered subprocesses (by process group, so `npx`/`uvx`
grandchildren die too), then refuses every subsequent action until an explicit
re-arm. There is no auto-recovery: an ambiguous state stays stopped.

Four ways to fire it, one core:

| Surface | How | Needs permission? |
|---|---|---|
| Global hotkey | **⌃⌥⌘K** | Input Monitoring |
| Menu bar | 🛡 → *PANIC — Halt Everything* | no |
| CLI | `kavach kill` | no |
| Unix socket | `~/.kavach/kill.sock` (0600) | no |

The three unprivileged surfaces exist because the one that needs a permission
is the one that can silently fail — and a kill switch that quietly doesn't work
is worse than none. The daemon self-tests Input Monitoring at startup and says
`[NOT WORKING]` rather than pretending.

```bash
cd brain
uv run python -m kavach.killswitch.daemon      # hotkey + menu bar + socket
uv run kavach status                            # armed?
uv run kavach kill                              # halt everything, latch
uv run kavach rearm                             # the only way back
```

See it work end to end — a real subprocess and a real async task, really killed:

```bash
cd brain && uv run python -m kavach.killswitch.demo
```

---

## The Presence layer

The orb is a *view of the agent's state*, never its owner. `lib/kavachState.ts`
defines what the Brain will publish; Phase 1 ships a mock that produces it, so
Phase 3 adds a WebSocket source beside the mock and changes no HUD code.

What the orb tells you without any text on screen:

| State | Orb |
|---|---|
| `idle` | slow ambient rotation |
| `listening` | pulse rings expand outward with mic amplitude |
| `thinking` | inner core spins hard, bloom tightens |
| `acting` | packets travel core → tool-call panel and back |
| `speaking` | bloom breathes with the TTS envelope |
| `halted` | **core extinguished, orb frozen, everything cold and red** |

The outer shell's opacity tracks reasoning confidence (§4 #3) — a thinner shell
means it was less sure. A suit-up boot sequence assembles the shells on launch
and ignites the core last.

Keys: `G` gestures · `R` reset · `+`/`−` zoom · **`K` kill switch** · `Esc`
interrupt · `Space` push-to-talk (Phase 2).

---

## Quick start

```bash
nvm use                                  # Node 24 — required, see below
cd apps/orb && npm ci && npm run dev     # the orb at localhost:3000

cd brain && uv sync && uv run pytest     # 30 tests
python3 hands/probe_mcp.py               # all three MCP servers
```

**Node 24 is mandatory.** `@steipete/macos-automator-mcp` declares
`engines: node >=24` and Peekaboo `>=22`; Node 20 cannot run either. Pinned via
`.nvmrc` — the global nvm default is left alone.

macOS permissions: [`docs/permissions-setup.md`](docs/permissions-setup.md).

---

## Layout

```
apps/orb/     Next.js 16 + Three.js — forked from Ultron (MIT)
brain/        Python (uv) — killswitch/, hands/allowlist; later stt, router, agent, tts
hands/        MCP server configs, app allowlist, probe/call scripts
daemon/       launchd service definition (Phase 5)
docs/         permissions-setup, attribution, demo-script
```

---

## Guardrails

Not polish — the difference between a portfolio piece and a story about the
time your assistant sent an email it shouldn't have.

- Kill switch built and tested before any device control existed
- **Allowlist, not blocklist** — Safari, Notes, Calendar, Finder
  ([`hands/allowlist.json`](hands/allowlist.json)). Unknown apps are denied by
  default; expanding the list is a deliberate act
- Destructive or externally visible actions (send, delete, purchase, submit)
  are spoken back and confirmed before they run
- `permission_mode` is **never** set to auto-approve
- Full timestamped JSONL action log of every tool call — gitignored, because it
  records everything the agent touched
- Wake-word audio that wasn't acted on is never logged or transmitted

---

## Attribution

The orb is forked from [ULTRON Orb UI](https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds)
by **Sagar Tamang**, MIT licensed, at commit `a65306f`. Full credits and the
other building blocks: [`docs/attribution.md`](docs/attribution.md).
