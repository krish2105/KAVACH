# Using KAVACH

Everything is already installed and running on this machine. Three launchd
agents start at login and stay up:

| agent | what it does |
|---|---|
| `com.krishna.kavach` | the voice loop — mic, STT, router, TTS, the API |
| `com.krishna.kavach.overlay` | the orb, the HUD, the global hotkeys |
| `com.krishna.kavach.observe` | watches Claude Code, runs health checks, narrates |

Nothing needs starting by hand. If something is wrong, `uv run kavach-doctor`
checks the lot.

---

## Talking to it

**Hold `⌃⌥⌘Space`, then speak.** You have **6 seconds** to start talking after
you press — that lead-in exists because the turn used to close after 1.05s and
you would be mid-breath. Once you *have* spoken, 700ms of quiet ends the turn,
so it does not feel slow.

The wake word does not work. Six approaches have failed on this microphone;
push-to-talk has never failed. Use the key.

### What it answers locally, with no model at all

| you say | what happens |
|---|---|
| "what time is it" | the system clock — never a language model |
| "open Notes" · "open Chrome" | AppleScript, ~250ms |
| "quit Safari" | same |
| "volume up" · "mute" | system volume |
| "battery" | the real percentage |

Any installed app works, not a fixed list. Say the app's name as you would say
it — "chrome" resolves to "Google Chrome".

### What goes to the model

Anything that needs judgement, tools, or the disk. "Find my tax document",
"what's in my Downloads folder", "summarise this", "search the web for X".

### The two other hotkeys

| key | what |
|---|---|
| `⌃⌥⌘K` | **kill switch.** Halts everything in flight and latches — it does not auto-recover |
| `⌃⌥⌘H` | minimise the orb |
| `⌃⌥⌘F` | full screen |

---

## When it asks you something

Destructive or externally-visible actions are read back before they run:

> *"That would delete the note called draft. Say confirm if you want me to."*

**Press `⌃⌥⌘Space` again and say "confirm".** It does not listen automatically
after asking — you have to press.

A confirmation expires after **120 seconds**, and an expiry is a **no**. If you
ignore it, nothing happens.

Shell commands are read back **every single time**, whatever they are. That is
deliberate: a shell command names no app, so nothing can classify it as safe.

---

## Your phone

The API is on your tailnet with a real certificate:

```
https://krishnas-macbook-pro.tailec3d44.ts.net
```

Set that as `$KAVACH` in Shortcuts. The token is in `brain/.env` — the
`KAVACH_API_TOKEN` line. **Do not paste it anywhere else.**

Full shortcut recipes are in [iphone-setup.md](iphone-setup.md). Four screens:

| shortcut | what |
|---|---|
| **Ask KAVACH** | speak or type a command from the phone |
| **Menu → Status** | what it is doing right now |
| **Menu → Pending** | a §7 confirmation waiting. **Expires in 120s** |
| **Menu → Queue** | PROPOSE-tier actions. Sits for a week, never runs on its own |

**Pending and Queue look alike and are not.** Pending means KAVACH is
*blocked*, waiting on you, and will give up. Queue means it queued instead of
interrupting you, and will wait.

To cut the phone off entirely:

```bash
tailscale serve --https=443 off
```

---

## Autonomy — how much it does without asking

```bash
uv run kavach-autonomy
```

Three tiers. Everything starts at **ALWAYS_ASK** and stays there until you
change it.

| tier | behaviour |
|---|---|
| `always_ask` | interrupts you. The default |
| `propose` | queues for batch review. **Never runs on its own** |
| `auto` | runs unattended |

```bash
uv run kavach-autonomy set read_file auto        # stop asking about reads
uv run kavach-autonomy set write_file propose    # queue writes instead
uv run kavach-autonomy demote write_file         # back to asking, instantly
uv run kavach-autonomy approve <id> <id>         # batch-approve queued items
```

**Anything that sends, deletes, buys or changes a system setting can never be
`auto`.** Not by config, not by editing the file, not after a hundred
approvals. The CLI will refuse and tell you why.

After 5 consistent approvals of one action type, KAVACH offers to stop asking.
It **offers** — the orb shows it, and you accept or ignore it.

---

## Privacy controls

```bash
uv run kavach-ghost on      # mic AND camera off; perception logging suppressed
uv run kavach-speaker on    # only your voice is acted on (see below)
uv run kavach-export        # what it has heard recently
```

Ghost mode suppresses what KAVACH **saw**, never what it **did** — every action
stays in the log.

### The speaker gate is currently in shadow mode

It scores every turn and **rejects nothing**. This is on purpose: three
thresholds were set from samples collected deliberately, and all three rejected
you. The only representative sample of how you talk to KAVACH is you talking to
KAVACH.

```bash
uv run kavach-speaker scores    # what your real turns actually score
```

Needs ~10+ turns before it means anything. Then it can be turned on properly.

**Until then, any voice in the room can command KAVACH** — and since Full Disk
Access is granted, that includes asking it to read your Messages.

---

## When something is wrong

```bash
uv run kavach-doctor                      # checks everything
tail -f ~/.kavach/logs/voice.log          # what the voice loop is doing
tail -20 ~/.kavach/logs/actions.jsonl     # every action, every argument
```

**"It didn't hear me."** Look for `no speech in the clip` in `voice.log`. If the
clip is ~1.2s you pressed and paused too long — but that should be fixed now;
tell me if it happens.

**"It said it did something but didn't."** That is the one failure this project
treats as unacceptable. The action log has every tool call and its verdict —
send me the last ten lines.

**Restart the voice loop:**

```bash
launchctl kickstart -k gui/$UID/com.krishna.kavach
```

**Stop the narrator** if it gets annoying:

```bash
launchctl bootout gui/$UID/com.krishna.kavach.observe
```
