# Setup — the steps that need you

Everything in KAVACH is built and tested. These are the things that could not
be done while you were asleep, because each needs your voice, your hands, your
phone, or a permission dialog only you can click.

They are ordered by value. **Nothing here is required for the core loop** —
wake word, speech, routing, tools and guardrails all work already.

---

## 1 · Enrol your voiceprint  *(2 minutes — do this one first)*

Until you do this, the confirmation gate checks *what* was said but not *who
said it*. Anyone within earshot of the mic can approve a delete.

```bash
cd brain && uv run kavach-enrol
```

Five phrases, 3.5 s each. Speak normally, at the distance and volume you'd
actually use — the threshold is calibrated from these clips, so enrolling in a
whisper and then talking normally will cause rejections.

```bash
uv run kavach-enrol --status    # check it took
uv run kavach-enrol --forget    # delete the profile (it is biometric)
```

**If it ever refuses you wrongly** — a cold, a bad mic, a noisy room — every
attempt is logged with its similarity score:

```bash
grep identity ~/.kavach/logs/actions.jsonl | tail -5
```

Compare the scores against the threshold and re-enrol if the gap is
consistently small. Don't lower the threshold blind.

---

## 2 · Try the gesture confirmation  *(1 minute)*

Press `G` in the orb to enable hand tracking, then hold a **thumbs-up** for
0.8 s. A ring fills as you hold it; let go early and nothing happens.

This is the point of it: you can approve a destructive action without saying
"yes" out loud, and a bystander cannot supply a gesture from across the room.

Thumbs-down denies. Two hands on screen is ignored — that's the pinch-zoom
control, and a delete shouldn't be authorised as a side effect of zooming.

---

## 3 · iPhone control  *(5 minutes)*

`mirroir-mcp` drives a real iPhone through macOS iPhone Mirroring. Checked
while you were asleep, it correctly reported what's missing:

```
[FAIL] 'iphone' process is not running
[FAIL] Screen capture failed — grant Screen Recording permission
```

To fix both:

1. Open **iPhone Mirroring** on the Mac and pair your iPhone (same Apple ID,
   iPhone locked and nearby, Bluetooth + Wi-Fi on)
2. **System Settings → Privacy & Security → Screen Recording** → enable it for
   whatever launches KAVACH (your terminal, or the Launch Agent)
3. Verify:

```bash
python3 hands/call_mcp.py mirroir check_health '{}'
```

**Read this before you rely on it.** The plan called for a per-app iPhone
allowlist (Notes, Music, Maps, Messages). mirroir's tools turn out to be
*device*-scoped, not app-scoped — `screenshot` and `describe_screen` capture
whatever is on the phone screen and none of them take an app name. So a
per-app iPhone allowlist is **not enforceable** through this server and would
have been security theatre. What ships instead:

- 8 read-only tools allowed (status, screenshot, describe_screen, …)
- `start_recording`, `stop_recording`, `calibrate_component` need confirmation
- anything else denied by default
- the whole device is off with one edit: `devices.iphone.enabled: false` in
  `hands/allowlist.json`

---

## 4 · Index some notes  *(2 minutes)*

KAVACH never reads your disk on its own. You name a folder:

```bash
cd brain
uv run kavach-memory index ~/Documents/notes
uv run kavach-memory sources                    # exactly what was read
uv run kavach-memory search "what did I decide about X"
uv run kavach-memory forget files               # revoke all of it
```

Only `.md`, `.txt`, `.markdown`, `.rst` and `.org` are indexed; hidden
directories (`.git`, `.venv`) are skipped.

---

## 5 · Try a non-English reply  *(1 minute)*

Say something in Hindi, French, Spanish, Italian, Portuguese, Japanese or
Mandarin — KAVACH replies in the same language. Verified by synthesising real
audio in each.

**The wake word is English-only.** It was trained on English renderings of
"KAVACH" and its 99.15% recall describes English speech. Say the wake word in
English, then continue in whatever language you like.

---

## 6 · Command KAVACH from your iPhone  *(10 minutes)*

The reverse of §3: your phone telling KAVACH what to do, rather than KAVACH
driving your phone. Two Apple Shortcuts — **no Xcode, no Apple Developer
account, nothing to re-sign every seven days.**

The API is bound to `127.0.0.1` and stays that way. Tailscale Serve proxies
from your private tailnet to that localhost port, so nothing is exposed to your
Wi-Fi or the internet — this is Tailscale's own recommended shape, not a
workaround.

**This needs a free Tailscale account, which is a new account** — you approved
it, but it is yours to create; I won't sign you up for anything.

```bash
brew install --cask tailscale
```

Then open Tailscale, sign in, and install it on your iPhone from the App Store
signed into the same account. Once both show as connected:

```bash
tailscale serve --bg 8770
```

`--bg` survives reboots, and TLS certificates are provisioned automatically —
there is nothing to configure in the admin console.

Check the Mac side:

```bash
cd brain && uv run kavach-doctor          # the REACH section
```

Then follow [`docs/iphone-setup.md`](docs/iphone-setup.md) for the two
shortcuts. It gives you a `curl` command for each one, so you can prove the
server works before building anything on the phone.

**One limitation, stated up front:** without push notifications — which need
the $99/year Apple Developer Program — your phone cannot be *woken* when
KAVACH needs an approval. Approvals are pull-only: you open the shortcut and
look. Since confirmations expire after 120 seconds, this works for "I sent a
command from my phone, now I approve it" and not for "KAVACH needs me and I'm
in another room."

---

## 7 · Run it at login *(optional)*

```bash
cp daemon/com.krishna.kavach.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.krishna.kavach.plist
```

**A Launch Agent is its own process as far as macOS permissions are
concerned** — the grants you gave your terminal do not carry over. It will
hear you but not be able to act until you grant Accessibility, Screen
Recording and Input Monitoring to it separately.

Deliberately not installed by default: a background process that listens to
your microphone and can drive your Mac should start by a decision, not a build
step.

To stop it: `launchctl unload ~/Library/LaunchAgents/com.krishna.kavach.plist`

---

## What is NOT built

**Meeting capture (Phase 9)** was skipped at your instruction. The research
stands if you want it later: `pyobjc-framework-CoreAudio` 12.2.2 does expose
`AudioHardwareCreateProcessTap` and `CATapDescription`, so no Swift helper
should be needed, and Core Audio Taps needs only the narrow "System Audio
Recording" permission rather than full Screen Recording.

**Proactive briefings** were part of the same phase and are also not built.

---

## Verifying the whole thing

```bash
cd brain && uv run pytest -q          # 250 tests
cd apps/orb && npx vitest run         # 13 tests
python3 hands/probe_mcp.py            # all 4 MCP servers
cd brain && uv run kavach status      # kill switch
```
