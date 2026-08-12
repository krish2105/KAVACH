# Demo script

For the Phase 5 portfolio video. Stub — beats get filled in as phases land.

Spec §11 guidance: lead with the **tool-call visualization**, be explicit about
the **guardrails**, and name the real building blocks (Claude Agent SDK, MCP,
Whisper.cpp, MediaPipe) rather than saying "AI assistant".

---

## Beat 0 — the kill switch (available now, Phase 0)

Open with this, not with the orb. Most agent demos show capability and leave
you wondering who's holding the leash; showing the leash first reframes
everything after it.

```bash
cd brain && uv run python -m kavach.killswitch.demo
```

On screen: a real child process and a real async task, both running. Then press
**⌃⌥⌘K**.

What the audience sees:
- the async task cancelled
- the child process exit code `-9` (SIGKILL) — genuinely dead, verifiable in `ps`
- state latched **DISARMED**, and `guard()` refusing the next action
- the JSONL audit record written with source and timestamp

The line worth saying out loud: *it latches. There's no auto-recovery — an
ambiguous state stays stopped, and only an explicit re-arm brings it back.*

---

## Beat 1 — presence *(Phase 1)*

Suit-up boot sequence, orb reacting to hand gestures, HUD panels.

## Beat 2 — voice round trip *(Phase 2)*

"KAVACH" → wake word fires → transcript appears in the HUD → spoken reply.
Show the latency number honestly.

## Beat 3 — the router *(Phase 3)*

Ask something trivial ("what time is it") — handled locally, sub-second, no
network. Then something that needs judgment — watch it escalate to Claude. The
confidence ring changes with the routing decision.

## Beat 4 — tool call made visible *(Phase 4)*

The money shot per §11. A glowing packet travels from the orb's core out along
a HUD line to the panel and back with the result. The agent loop, visible
instead of abstract.

## Beat 5 — the guardrail, on camera *(Phase 4)*

Ask for something destructive. KAVACH speaks the action back and **waits**.
Deny it, and show the action log line proving it never ran. Then try an app
that isn't on the allowlist and show the refusal.

---

## Recording notes

- Have the action log tailing in a visible pane the whole time — the audit
  trail is part of the pitch
- Do not fake latency; show the real number and say what it is
- Record the kill switch beat in one unbroken take, no cuts
