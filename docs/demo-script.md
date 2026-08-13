# Demo script

For the portfolio video. Every beat below is real and runnable today — no
mock-ups, no "imagine if". Where something is still a stand-in it says so.

§11's guidance: lead with the **tool-call visualization**, be explicit about
the **guardrails**, and name the real building blocks rather than saying "AI
assistant".

**Total runtime: about 3 minutes.**

---

## Setup (before recording)

```bash
# one terminal, visible in frame — the action log is part of the story
tail -f ~/.kavach/logs/actions.jsonl

# another, off-frame
cd brain && uv run python -m kavach.voice
```

Open the orb at `localhost:3000`. Wait for **BRAIN LIVE** in the header — if
it says `DEMO (MOCK)` the Brain isn't connected and nothing you show is real.

---

## Beat 0 — the leash, before the capability *(20s)*

Open here, not with the orb. Most agent demos show capability and leave you
wondering who's holding the leash.

> "Before I show you what it can do, here's how I stop it."

Press **⌃⌥⌘K** mid-sentence while it's speaking.

On screen: audio cuts instantly, the orb's core **extinguishes and freezes**,
the badge flips to **⛔ DISARMED**, pending tool calls go to `⊘`.

> "It latches. There's no auto-recovery — an ambiguous state stays stopped,
> and only an explicit re-arm brings it back. I built this before any of the
> device-control code existed."

Re-arm from the menu bar 🛡.

---

## Beat 1 — presence *(15s)*

Reload to catch the **suit-up sequence**: shells assemble from scattered
fragments, core ignites last. Press `G` for hand tracking, spin the orb with a
pinch.

> "The orb is a fork of an open-source Three.js shell — MIT licensed, credited
> in the repo. Everything behind it is mine."

---

## Beat 2 — a local round trip *(25s)*

Hold **Space**: *"what time is it"*

> "That never left the machine, and it never touched a language model — the
> router has a deterministic tier for things like the clock. Asked directly,
> the 4B model spends three and a half seconds explaining it can't read a
> clock. This answers in a fifth of a millisecond."

Point at the **ROUTE: LOCAL** tag and the confidence ring.

---

## Beat 3 — escalation, made visible *(30s)*

Hold **Space**: *"using your macOS tools, what's the name of my home folder?"*

Watch the HUD: `LISTENING → THINKING → ACTING`, the **ROUTE tag flips to
CLAUDE**, and a glowing packet travels from the orb's core out to the tool-call
panel and back.

> "That's a real MCP tool call — AppleScript against Finder, through
> Anthropic's Agent SDK. The packet isn't decoration; it's driven by the actual
> tool event."

The panel shows `macos-automator · execute_script`, and the tailed log shows
the matching `tool.decision` line with `verdict: allow`.

---

## Beat 4 — the guardrail, on camera *(45s)*

**This is the beat that matters.** Two takes, both real.

**4a — outside the allowlist.** Hold **Space**: *"open Mail"*

> "Mail isn't on the allowlist, so it never runs."

Show the log line:

```
DENY  app='Mail'  mcp__macos-automator__execute_script
      'Mail' is not on the KAVACH allowlist. Allowed: Calendar, Finder, Notes, Safari.
```

> "That's not the model deciding to be careful — it's a PreToolUse hook
> refusing before the call executes. I know the difference because I shipped
> the wrong version first: a wildcard in `allowed_tools` auto-approved
> everything before my gate ran. The SDK warned me. Every guardrail was dead
> code and the demo still looked perfect."

**4b — destructive, inside the allowlist.** Hold **Space**:
*"delete the draft in Notes"*

KAVACH **speaks the action back** and waits. Say **"no"**.

> "Only an unambiguous yes counts. Silence, a timeout, a misheard word — all
> denials. 'Sure' and 'okay' deliberately aren't accepted; they show up too
> easily in ordinary speech to authorise deleting something."

---

## Beat 5 — the closing line *(15s)*

Show `hands/allowlist.json` and the tailed log side by side.

> "Four apps. Every tool call logged with its arguments. A kill switch on five
> surfaces. It's local-first — the wake word, speech-to-text and the voice are
> all on-device; only the hard reasoning goes to Claude, and only when the
> router decides it has to."

---

## Recording notes

- Keep the action log tailing on screen the whole time. The audit trail is the
  pitch, not a footnote.
- **Record Beat 0 and Beat 4 in unbroken takes.** A cut there reads as a
  reshoot until it worked.
- Don't fake latency. Say the real number.
- Warm the models before rolling — the first turn includes load time and is
  several seconds slower than every one after it.
- If the wake word is trained by then, open with *"KAVACH"* instead of Space.
  If not, say push-to-talk is the trigger and move on; don't hide it.

## What to say if asked "what was hardest?"

The routing split, honestly — but the better answer is the `allowed_tools`
bug. A guardrail that silently doesn't run is worse than no guardrail, because
you stop looking. The only reason I caught it was that the SDK emitted a
warning and I read it instead of filtering it out.
