# KAVACH expansion — design

**Date:** 2026-08-13 · **Status:** awaiting review
**Baseline:** phases 0–5 complete, 179 tests passing, wake word trained
(recall 0.9915, FPPH 0.0)

---

## Context

KAVACH today hears you, decides how hard to think, and acts on four macOS apps
behind a kill switch, an app allowlist and a spoken confirmation gate. This
design adds **eight features across four phases**, extending it to a second
device, giving it memory, and — most importantly — making it able to tell
*who is speaking*.

Chosen by the user: iPhone as the cross-device target (no Android, no Home
Assistant), **breadth** over depth, speaker verification as a **hard block**,
and meeting capture **with detection**.

**Breadth is interpreted as: each feature complete but minimal.** KAVACH's
credibility rests on everything in it being tested and working; eight
half-finished features would destroy that. Every feature below ships with
tests and a working demo path, or it does not ship.

### Everything verified before planning (§A)

| Package | Version | Checked |
|---|---|---|
| `mirroir-mcp` | 0.37.0 (npm) | GitHub 175★, Apache-2.0, pushed 2026-08-12 |
| `resemblyzer` | 0.1.4 | stale (2023) but stable and tiny; torch already present |
| `sqlite-vec` | 0.1.9 | 2026-03-31 |
| `pyobjc-framework-CoreAudio` | 12.2.2 | 2026-08-11; `AudioHardwareCreateProcessTap`, `CATapDescription`, `AudioHardwareDestroyProcessTap` all exposed |
| `mediapipe` | 1.0.0 | already in the orb as `@mediapipe/tasks-vision` |

---

## Architectural principle

**Every new capability goes through the existing `ToolGate`.** Nothing added
here gets its own permission path. Concretely:

- A second device means the allowlist becomes **device-scoped**, not a flat
  list of app names. `Safari` on the Mac and `Safari` on the iPhone are
  different grants.
- Speaker verification is an *additional* gate inside `VoiceConfirmer`, not a
  replacement for it. Failing either denies.
- Meeting capture is a capability the gate can refuse like any other.

The one genuinely new surface is the **gesture confirmation path**, because
confirmation is currently voice-only and a gesture arrives from the browser.
That needs a new bridge command, and it must be held to the same standard:
only an unambiguous, deliberate gesture counts.

---

## Phase 6 — Identity and consent

Security first, mirroring how Phase 0 built the kill switch before anything it
guarded.

### F1 · Speaker verification (hard block)

**Problem:** the confirmation gate trusts whoever is in the room. Anyone
within earshot can answer "yes" to a delete prompt.

**Approach:** Resemblyzer produces a 256-d embedding from ~1.5s of speech.
Enrol once (~30s of your voice, several phrases), store the mean embedding.
At confirmation time, embed the answer audio and compare by cosine similarity
against a threshold.

- `brain/kavach/identity/voiceprint.py` — enrol, embed, verify
- Enrolment stored at `~/.kavach/voiceprint.npy` (gitignored — it is biometric)
- Wired into `VoiceConfirmer._ask`: the answer must be *both* affirmative
  **and** a voice match
- **Fails closed**: no enrolment, low similarity, an exception, or too little
  audio all deny. Consistent with every other branch in that module.
- Threshold calibrated during enrolment, not guessed — enrol, then score
  held-out clips of the user against imposter clips (Kokoro-synthesised) and
  pick the operating point, same discipline as the wake word's 0.18.

**Risk stated up front:** a cold, a bad mic, or background noise could lock the
user out of their own destructive actions. Mitigation: `kavach confirm --cli`
always works as an escape hatch (typing at the terminal proves physical
access), and every rejection is logged with its similarity score so the
threshold can be tuned from real data.

### F2 · Gesture confirmation

**Problem:** confirming out loud in an open office is awkward, and speaking
"yes" is exactly what an attacker in earshot can also do.

**Approach:** MediaPipe already runs in the orb. Add a gesture classifier for
**thumbs-up (confirm) / thumbs-down (deny)** and route it to the bridge as a
new `confirm` command.

- `apps/orb/lib/gestures.ts` — classify from existing hand landmarks
- Requires the gesture be **held for 800ms** — a passing hand shape must not
  authorise a delete
- The orb shows a filling ring while held, so the user sees the commitment
- Bridge command `{"cmd": "confirm", "answer": true|false}`
- `VoiceConfirmer` races voice against gesture; **first unambiguous answer
  wins**, timeout still denies

---

## Phase 7 — Reach

### F3 · iPhone control

**Approach:** `mirroir-mcp` drives a real iPhone through macOS iPhone
Mirroring — screenshot, tap, swipe, type. No app on the phone.

- Added to `hands/mcp.config.json`, version-pinned like the others
- **`hands/allowlist.json` becomes device-scoped:**
  ```json
  { "devices": { "mac":  { "allowed": [Safari, Notes, Calendar, Finder] },
                 "iphone": { "allowed": [Messages, Notes, Music, Maps] } } }
  ```
- `ToolGate` resolves the device from the server name (`mirroir` → iphone) and
  checks the right list. **Existing Mac behaviour must not change** — the
  current allowlist tests stay green unmodified.
- iPhone actions are **externally visible by default**: a tap on a phone can
  send a message. Confirmation is required for anything that is not a
  screenshot or a read.

### F4 · Screen understanding

Peekaboo is already installed, permission-granted and gated — it is simply
never reached, because the router has no intent for "what's on my screen".

- Router intent `screen query` → Claude with Peekaboo's `see`/`image`
- Cheapest feature here by a wide margin: config and a router pattern, no new
  dependency

---

## Phase 8 — Memory

### F5 · Local memory + file search

**Approach:** `sqlite-vec` — a single file, no server, no daemon — plus a
local embedding model through Ollama (`nomic-embed-text`, ~0.3 GB).

- `brain/kavach/memory/store.py` — embed, upsert, search
- Two collections: **turns** (what was said, for continuity across a session)
  and **files** (an opt-in indexed folder, defaulting to nothing)
- Router gains a `recall` intent; the agent gets a `search_memory` tool
- **Indexing is explicit.** KAVACH does not silently read your disk: you name
  the folder, and the index is listable and deletable from the CLI.

### F6 · Multilingual

Whisper `large-v3-turbo` is already multilingual; Kokoro ships multiple
voices. This is mostly configuration plus honest handling of the failure mode.

- Auto-detect the spoken language, reply in the same one
- Voice selected per language, configurable
- The wake word stays English-only — it was trained on English phrases, and
  pretending otherwise would be a lie the metrics do not support

---

## Phase 9 — Ambient

### F7 · Meeting capture

**The most privacy-sensitive feature here, so it gets the most constraints.**

- **Core Audio Taps** (macOS 14.4+) via pyobjc — needs only the narrow
  "System Audio Recording" permission, not full Screen Recording
- **Detection never records.** KAVACH notices a call (a known conferencing app
  holding the mic) and *asks*. Recording begins only on an explicit
  affirmative — the same confirmation path as any destructive action,
  including the voiceprint check.
- The orb shows an unmissable recording indicator the entire time, and KAVACH
  speaks aloud when recording starts and stops
- Transcription is local; the summary is local unless the user escalates it
- Audio is deleted after transcription by default. The transcript is a file
  the user owns.

**Consent is explicitly the user's responsibility**, and the docs will say so:
recording other participants has legal implications that vary by jurisdiction.

### F8 · Proactive briefings

- Calendar-aware: "your next meeting is in 10 minutes" — spoken, unprompted
- **Quiet by default**: opt-in, with a Do Not Disturb window
- Never interrupts an in-progress turn; queues behind it

---

## Testing

Each feature ships with tests in the established style, and these in
particular are written test-first because they have clear pass/fail behaviour:

- **Voiceprint**: an imposter (a Kokoro-synthesised voice) must be rejected;
  the enrolled user accepted; no-enrolment denies; an exception denies
- **Device allowlist**: existing Mac tests pass **unmodified**; an iPhone app
  not on the iPhone list is denied; a Mac-allowed app does not become
  iPhone-allowed
- **Gesture**: a brief thumbs-up does not confirm; a held one does; an
  ambiguous shape never confirms
- **Meeting capture**: detection alone never starts recording

---

## Sequencing and honest cost

| Phase | Features | Risk |
|---|---|---|
| 6 | voiceprint, gesture confirm | Medium — threshold calibration is fiddly |
| 7 | iPhone, screen understanding | Low — mostly config + the device-scoped allowlist refactor |
| 8 | memory, multilingual | Medium — the indexer is the largest new subsystem |
| 9 | meeting capture, briefings | **High** — Core Audio Taps from Python is unproven here; the symbols exist but nobody has run this flow end to end on this machine |

Phase 9 is the one that could genuinely fail. If Core Audio Taps proves
unworkable from pyobjc, the fallback is a small Swift helper binary invoked
over a pipe — more moving parts, same permission scope. That will be reported
rather than silently substituted.
