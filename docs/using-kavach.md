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

**Or just say "hey there".** The wake word works now — say it, pause, then
your command:

> **"Hey there."** … *"what time is it?"*

It also works in one breath: *"Hey there, what time is it?"*

Measured 5/5 recognised and 0 false wakes across eight sentences built to
trip it ("hey, are you there?", "is there anything else", "they were there
yesterday"). Both words must be **adjacent and in order**, which is what
keeps ordinary speech quiet.

If it stops firing, this shows exactly what it heard and stores nothing:

```bash
uv run --directory brain kavach-wakecheck
```

**It used to be "Kavach", and that never worked.** Seven attempts, four
trained models. Whisper has no representation for the Sanskrit word — it
dropped it 7 times out of 7 inside a sentence, and wrote it as 'cabbage',
'go watch', 'coverage' or 'कवच' on its own. Changing the phrase fixed in one
step what five fixes to the machinery could not. Push-to-talk still works
and is still the reliable one.

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

## It remembers what you asked it

Ask about something you said earlier and it answers from an index of your own
turns, not from a model's guess:

```
you    "what did I ask you about the battery"
KAVACH "You asked about the battery level, and I said it was at 41%."

you    "what did I ask you about last March"
KAVACH "I don't have anything about that."
```

The second one matters as much as the first. A model with no index does not
decline — it invents a plausible Friday. KAVACH answers only when what it found
clearly beats everything else it found, and says it has nothing otherwise.

Every voice turn and every command from your phone is remembered. **Files are
not** — you name those:

```bash
uv run kavach-memory index ~/Documents/notes   # a folder you name, nothing else
uv run kavach-memory status                    # how much is stored, by kind
uv run kavach-memory sources                   # every file it has read
uv run kavach-memory search "the roof quote"   # look without asking out loud
uv run kavach-memory forget files              # delete a kind
uv run kavach-memory forget                    # delete all of it
```

It can also index **what it did** — every tool call it has made:

```bash
uv run kavach-memory index-actions
```

Then *"when did I open Notes"* answers from the action log, with the time the
thing actually happened rather than the time it was indexed.

### Messages

Messages needs Full Disk Access, and **your terminal does not have it — the
daemon does.** macOS attributes the grant to whichever process is responsible,
so `kavach-memory index-messages` is refused from a shell while the daemon
reads the same file happily. That is macOS working correctly. Ask the daemon
instead:

```bash
TOKEN=$(grep -h '^KAVACH_API_TOKEN=' ~/Desktop/KAVACH/brain/.env | cut -d= -f2) && curl -s -X POST http://127.0.0.1:8770/index-messages -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"limit":200}'
```

Two things that both bit on the way here. `TOKEN` must be set on the same
line, or you get `{"detail":"Bad or missing token."}` — which looks like a
broken endpoint and is an unset shell variable. And the path to `.env` is
**absolute**, because a relative `brain/.env` only works from the repo root
and fails with `No such file or directory` from inside `brain/` itself,
which is where you already are.

```bash
uv run kavach-memory forget messages     # removes every one of them
```

**Think before running this one.** It copies real conversations into
`~/.kavach/memory.db`, which is not encrypted. Everything below about
FileVault applies to it with more force than it does to your notes.

There is no flag that indexes everything — a sweep of the disk was cut, and a
test fails the build if one is ever added. Screen contents and ambient audio
have no indexer at all, for the same reason.

**The index is not encrypted at rest.** It is a SQLite file at
`~/.kavach/memory.db`, and anything indexed into it is readable by anything
that can read your home directory. FileVault is the right layer for that — if
your Mac has it on, this is covered; if not, that is the thing to turn on. Do
not index a folder you would not want sitting in a plain file.

---

## Privacy controls

```bash
uv run kavach-ghost on      # mic AND camera off; perception logging suppressed
uv run kavach-speaker on    # only your voice is acted on (see below)
uv run kavach-export        # what it has heard recently
```

Ghost mode suppresses what KAVACH **saw**, never what it **did** — every action
stays in the log.

### The speaker gate is ON (2026-08-16)

Only your voice is acted on. Threshold **0.300**, enrolled from the 42
recordings of you made for the wake word — real audio through this exact
microphone, which is what the three earlier attempts were missing.

Measured before switching it on: your voice **0.345–0.634**, sixteen other
voices **−0.182 … +0.097**. Nothing but you gets in, with a 0.239 margin.

```bash
uv run kavach-speaker status    # threshold and state
uv run kavach-speaker off       # reverse it; the voiceprint is kept
```

**The one thing to watch.** A clip under 3 seconds cannot be speaker-verified,
and with the gate on such a turn is discarded rather than guessed at. If short
commands start vanishing, that is the cause:

```bash
grep voice.rejected ~/.kavach/logs/actions.jsonl | tail -5
```

A `clip too short to verify` reason means the gate, not the microphone. Tell
me if you see it — the fix is a design decision, not a knob.

Spoken **confirmations** are deliberately not speaker-checked: a one-word
"confirm" is too short to verify at any threshold. So someone else in the room
could answer a confirmation prompt within its 120-second window, though they
could not start the action.

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

**"It refuses everything and says the kill switch is latched."** Then it is.
The latch survives a restart and a reboot — that is what "does not auto-recover"
means, and the fix is to say so deliberately:

```bash
uv run kavach rearm --reason "know why it fired"
```

`~/.kavach/logs/actions.jsonl` records why it fired and everything it refused
while latched. Until this was fixed the latch lived only in the running
daemon's memory, so a CLI started afterwards would happily read your disk while
the system was supposedly halted.

**Restart the voice loop:**

```bash
launchctl kickstart -k gui/$UID/com.krishna.kavach
```

**Stop the narrator** if it gets annoying:

```bash
launchctl bootout gui/$UID/com.krishna.kavach.observe
```
