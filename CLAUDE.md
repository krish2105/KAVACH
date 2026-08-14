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
| 2 — Local voice loop (wake word → Whisper → Kokoro → orb) | **complete**; wake word **v2** in use — see below |
| 3 — Brain + router | **complete** (tag `phase-3`) |
| 4 — Hands + guardrail enforcement (gate, allowlist, spoken confirm) | **complete** (tag `phase-4`) |
| 5 — Integration + demo | **complete** (tag `phase-5`); demo video is yours to record |

**Wake word v2** (2026-08-13): AUT 0.0040, FPPH 0.00, recall 83.8%, measured
optimum **0.20**. v1's headline recall was higher (99.15%) and *meaningless* —
trained on one American voice, it scored the user's real utterances 0.027–0.571
against 0.789 for an unrelated phrase. v2 uses accent-diverse VoxCPM synthesis.
`find_wake_model()` now takes the newest export, and a calibration carries a
content hash of the model it measured — a threshold from a different model is
refused, not applied. **Still uncalibrated**: `uv run kavach-waketune` needs the
user's voice.

### Reach phases (the user's second numbering — restarts at 6)

Tags are `reach-N`, because `phase-6/7/8` were already taken by the earlier
expansion numbering. **Do not re-propose anything marked cut or blocked.**

| Phase | State |
|---|---|
| 6 — Local API surface | **complete** (tag `reach-6`) — FastAPI on 127.0.0.1:8770, bearer token, pending-confirmation flow |
| 7 — The phone commands KAVACH | **complete** (tag `reach-7`) — two Apple Shortcuts, `POST /kill`, Tailscale Serve |
| 8 — Apple Watch | **CUT — the user owns no Apple Watch.** Also: Tailscale has **no watchOS app** (iOS/iPadOS/tvOS/visionOS only), and `Get Contents of URL` is unreliable on watchOS. A Watch app would need the iPhone as a WatchConnectivity relay, hence Xcode |
| 9 — Remote access | in progress — mostly delivered by Phase 7's transport; needs the tailnet leg proven |
| 10 — Tiered memory | mostly built (`kavach/memory/store.py`, sqlite-vec). **Never index screen content or ambient audio** — the user cut that explicitly as a privacy/storage liability |
| 11 — Smart home | **CUT — the user owns no smart-home devices** |
| 12 — Speaker ID | built (Resemblyzer, threshold 0.613, margin 0.196). The **config toggle** is the missing piece. Eagle comparison needs a **paid Picovoice contract — ask before signing up** |
| 13 — Explainability | **complete** — `reason`/`intent` in the snapshot and the STATUS panel. `respond()` now publishes its decision; before, an API turn computed and logged a route the HUD never saw |
| 21 — Hinglish speech | **complete** — `kavach-stt` registry + GGML conversion. Stock stays default |
| 14 — Ghost mode | **complete** — `kavach/privacy/ghost.py`. Stops mic **and** camera, suppresses *perception* logging only |
| 15 — Meeting-aware muting | **complete** — `kavach/privacy/meetings.py`. Window-title detection; needs a real call to finish verifying |
| 16 — Session recorder | **complete** — `kavach/memory/session.py`. Rolling 15 min, `kavach-export`, no network path |
| 17 — Desktop widget | **complete as a menubar** — WidgetKit needs Xcode. `status_title()` in `presence/controls.py` |
| 18 — Multi-Mac awareness | **complete** — `kavach/single.py`. Lock + heartbeat, ownership per lock *object* not per PID |
| 19 — Dynamic Island / Live Activities | **BLOCKED — needs a native app, which needs Xcode.** Also needs push for remote updates, which needs the paid membership |
| 20 — Garage mode | not started; depends on 9. Get the user's definition before planning |

**CarPlay was cut by the user and must not be built.**

### launchd cannot read this project — measured, not assumed

`~/Desktop` is TCC-protected and **a launch agent has no grant for it**:

```
$ launchctl bootstrap gui/$UID <a job that cats one file>
DESKTOP DENIED
HOME READABLE
```

Nothing fails on that denial. It **hangs**, with the job reported healthy:

* a `next start` agent — 100% of samples in
  `GetNearestParentPackageJSONType → TraverseParent → open`, port never bound,
  zero bytes logged
* the overlay agent pointed at `open -W -a KAVACH.app` — 100% of samples in
  `_PyCodecRegistry_Init → os_listdir → open$NOCANCEL`, i.e. Python blocked on
  the first import in the process

`uv run kavach-overlay` **does** hold the grant here, and gets the camera with
it — the agent-started process logs live `pinch ENGAGED` lines. So the overlay
agent runs the CLI, and the bundle stays the manual/`open -a` route. **Do not
switch the agent to the bundle without granting KAVACH.app Full Disk Access
first** — a silent hang is worse than the refusal it was meant to fix.

**The orb page is served by the overlay process** (`presence/pageserver.py`),
not by an agent, for the same reason. It starts `next start` as a child, adopts
an already-listening port rather than fighting it, and stops it on exit. Before
this, the page came from a hand-typed `next start`: closing that terminal left
an empty transparent window — no error, nothing to click, indistinguishable
from an idle orb. That is what "nothing was visible" was.

**Install agents with `uv run kavach-daemons install`, never `cp`.** The files
in `daemon/` are templates full of `__UV__`-style placeholders; SETUP.md used to
say `cp`, so the installed copies were hand-edited and then diverged from git.
`kavach-daemons status` reports **stale** when they differ, which is the only
symptom that failure has.

Also: launchd's `StandardOutPath` must not be `overlay.log` — the process
already puts a `FileHandler` there, and pointing both at one file logged every
line twice.

### `uv run` in a launch agent costs you every TCC grant

TCC attributes permission to the **responsible process**, and for a launchd job
that is the binary launchd starts. With `uv run kavach-overlay` that binary is
`uv`, so a grant given to `python3.12` is never consulted. Measured from inside
a launchd job, the only place the difference is visible:

```
uv run python      → CGPreflightListenEventAccess() False
venv python direct → CGPreflightListenEventAccess() True
```

The symptom was `hotkeys BLOCKED` in the log while System Settings showed Input
Monitoring switched **on** — the grant was real and simply not being asked.
Both agents run `.venv/bin/…` directly now, and a test walks every plist in
`daemon/` asserting none of them goes through uv. This matters more for the
voice loop than the overlay: the same mistake would put the **microphone**
behind it.

Measured before installing the voice agent, rather than assumed from the camera
result:

```
mic TCC status: AUTHORIZED
mic opened: OK
```

So the microphone and the camera do **not** behave alike for a bare process —
the camera needs the app bundle (`presence/appbundle.py`), the mic does not.

**The voice agent is `KeepAlive={SuccessfulExit: False}`** — it comes back from
a crash, but a clean stop stays stopped. That is deliberate for a process
holding the microphone: `launchctl bootout gui/$UID/com.krishna.kavach` turns it
off and it stays off until you load it again.

Ollama is started at login by `brew services start ollama`, not by KAVACH. The
router silently falls back without it.

### One instance, enforced by a file (§18 extended)

`InstanceLock(name)` in `kavach/single.py`; `WakeWordLock` is a thin alias.
The overlay takes `~/.kavach/overlay.lock` and a second one **exits 1** naming
the holder.

Added after four overlay processes drew two panels on top of each other. Every
duplicate-instance bug in this project has come from `pgrep`/`pkill` patterns
not matching the process name — **do not add another one.** A lock the process
takes itself does not care what the command line looks like.

**A LaunchAgent respawns the overlay.** `com.krishna.kavach.overlay` is loaded
with KeepAlive, so killing the overlay brings it straight back and a manual
`kavach-overlay` stacks on top of it — that is where the duplicate panels
actually came from. To test a variant, `launchctl bootout
gui/$UID/com.krishna.kavach.overlay` first and bootstrap it back afterwards.
**Never `rm ~/.kavach/overlay.lock` while an instance is running** — that is
the one action that defeats the guard.

### The 🛡 menu must survive the smallest panel

At Small (280pt) the menu was 384px tall in a 280px window and **eight of its
twelve items could not be clicked** — `Medium`/`Large`/`Huge` sat under the
GESTURES and ±/RESET buttons, five more fell off the bottom. Choosing Small
removed every control that could undo it, and the global hotkeys are dead
without Input Monitoring (`hotkeys BLOCKED` in the log), so there was no second
way out. The one item still clickable was `Full screen` — which is exactly what
the user clicked four seconds later.

The z-index looked right and did nothing. `.shield-root` lived inside a `.hud`
that is `position: fixed; z-index: 20`, which **opens a stacking context**, so
`z-index: 40` only ranked it against its own siblings and the later `.hud`
painted over it regardless. It renders through a **portal to `document.body`**
now, plus `max-height`/`overflow-y` so it scrolls instead of overflowing.

Verified by hit-testing every item with `elementFromPoint` at 280/400/560/760 —
0 unreachable at all four. Looking at a screenshot cannot see this bug.

### Full screen (`⌃⌥⌘F`, or `kavach-overlay --fullscreen`)

Full screen is opaque, not a bigger transparent panel. Three things had to
change together, and all three were separately invisible as bugs:

* `html.kv-overlay.kv-fullscreen` restores an opaque black ground and the
  vignette/grain/scanline layers overlay mode strips. CSS alone is not enough —
  the NSWindow is also set opaque, or the desktop composites through anything
  not fully opaque, which is most of a glowing orb.
* The camera drops `PANEL_MARGIN` (1.12). That pull-back exists because the
  floating panel is *square* and crops the orb; full screen is 1800x1169 and
  the margin just left the orb small.
* **`bloomScale` is recomputed on resize.** It was a `const` fixed at scene
  creation, so a 760pt panel going full screen kept a bloom tuned for 760pt —
  the orb filled the display with the glow of a thumbnail.

Related: `.kv-overlay *` sets `-webkit-user-drag: none`. Dragging the TALK
button out of the WKWebView wrote 1.3 MB `.textClipping` files to the Desktop.
`user-select: none` does not prevent this and was only on `.hud` anyway.

`OverlayWindow.probe()` also checks whether the page is *styled* and reloads
(max 3) if not — a page whose CSS 404'd renders as unstyled text and WKWebView
keeps showing it forever.

### Hand control — pinch to rotate and zoom

`gestures/pinch.py`. Separate from the five held gestures on purpose: those
fire once after 0.8s, this reacts every frame, so the design is all about a
clean engage/release rather than accuracy.

* The pinch is measured **against the hand's own span** (wrist→index knuckle),
  not in raw normalised coordinates — otherwise it silently demands a tighter
  pinch the further back you sit.
* **Engaging reports zero movement**, so re-pinching elsewhere in frame cannot
  fling the orb by the distance your hand travelled while released.
* **Hysteresis** (0.45 engage / 0.62 release) stops a hand on the boundary
  chattering.
* **Disabled entirely while a confirmation is pending** — a hand moving near an
  approve/deny prompt is how a thumbs-up gets misread, and §7 consent must be
  deliberate.

Delivered straight into the WebView via `evaluateJavaScript` from the presence
process (`window.__kavachControl`), coalesced on the overlay's existing tick.
Not routed through the brain: the camera and the WebView are the same process,
and a websocket hop per frame feels like lag in your hand.

**Only the orb so far.** Driving the frontmost app needs synthesised scroll/zoom
events and Accessibility permission, and lands under §7 gating — not built.

### Hand control of other apps — the first thing that acts outside KAVACH

`gestures/appcontrol.py`. Scroll and zoom only; **zoom is ⌘+scroll**, the idiom
Safari/Preview/Maps already understand, so no keystroke synthesis and no
private API. **Rotation is impossible** — macOS has no public rotate-gesture
API — so it drives the orb and nothing else, and the HUD says which.

Verified: `CGPreflightPostEventAccess()` is the correct permission for *posting*
events (not the Accessibility AX API), and `pyobjc-framework-ApplicationServices`
is **not needed**. `NSWorkspace.frontmostApplication()` needs no permission.

**Six gates, denial default at each**: armed explicitly (off every launch, and
a test greps the module for persistence so it stays that way), post-event
access, the frontmost app on `hands/allowlist.json`, no confirmation pending,
kill switch armed, not ghost. Each has its own test — a single "it works" test
would pass with five of six in place.

The presence process has no `KillSwitch` object, so it **observes** the latch
from the snapshot it already receives and writes to the same `ActionLog` file
(opened per write with `O_APPEND`, safe across processes).

Sessions log `appcontrol.start`/`.end`, not frames. Both are absent from
`SUPPRESSED_IN_GHOST` — ghost hides what KAVACH *saw*, never what it *did*.

**Chrome was added on 2026-08-13 at the user's request.** The allowlist now
holds Safari, Notes, Calendar, Finder, Music, Spotify, Google Chrome.

One list gates **both** paths, deliberately: adding an app for hand control
also grants KAVACH's MCP tools access to it. Two lists is how one of them goes
stale. `tests/test_allowlist.py` requires a recorded reason per entry, so an
app cannot arrive without someone having asked.

### Ghost mode — the boundary that matters (§14)

Ghost suppresses **perception**, never **action**. `ActionLog.SUPPRESSED_IN_GHOST`
lists only `router.decision`, `voice.turn`, `voice.rejected` — the events
carrying what KAVACH heard or saw.

It was originally the other way round (suspend everything, exempt a few), and
**live testing found a typed API command reaching a tool with no log record at
all**. §7 says every tool call and argument is recorded; ghost mode does not get
to suspend that. A deny-list is also the right shape here: with an allowlist, a
*new* event defaults to hidden, and the events most worth adding are the ones
recording KAVACH doing something.

**The camera lives in the presence process, not the brain.** `GhostMode` in the
voice loop can only stop the mic; `privacy/camera_gate.py` closes it from the
other side, driven by the same snapshot stream the menu bar uses. The unit
tests missed this for a while because they attached a *fake* tracker in-process
— green tests, live camera.

### Two defaults for the local model is one too many

`local.DEFAULT_MODEL` is the **only** place the Ollama model is named.
`voice/__main__.py` used to hardcode `--local-model default="qwen3:4b"`, which
silently overrode the switch to `llama3.2:3b` — so the running assistant kept
using the model that **narrates its reasoning as prose**, and KAVACH spoke 581
characters of "Hmm, the user is asking..." out loud. A test greps
`voice/__main__.py` for a model name so the two cannot diverge again.

### The clock must never reach a language model

A model with no clock does not decline — it guesses. KAVACH answered "twenty
past four" at 8 p.m. because a transposed "what time it is" missed the regex
and fell through. The patterns in `router.py` are deliberately generous about
phrasing and still anchored on "the time"/"time is" so they cannot swallow
"how much time do I have". Tests cover nine phrasings and three near-misses.

### Camera / gestures — why they do not run

`camera_status()` returns 0 (not determined) and `request_camera()` is refused
in ~100 ms. Not a bug in `permission.py`: macOS **will not show a camera prompt
to a process with no app bundle**, and the overlay runs as a bare Python
process — `bundleIdentifier` is None and `NSCameraUsageDescription` is missing.

**Fixed by `kavach-app`** (`presence/appbundle.py`), which builds
`~/Applications/KAVACH.app`. Three things were each individually necessary and
none of them is obvious:

1. The **interpreter is copied into `Contents/MacOS/`** — TCC attributes
   permission to the bundle containing the running executable, and a symlink
   is resolved away.
2. **Nothing may point out of the bundle.** The first version linked
   `Contents/lib` at the venv; `codesign` then reported *"invalid destination
   for symbolic link in bundle"*, the signature never validated, and **TCC
   refuses an unverifiable bundle without showing a prompt** — the same ~100 ms
   refusal that looks like broken hardware. Packages come via `PYTHONPATH`
   instead, which is not part of the seal.
3. **`PYTHONHOME` is required.** uv's interpreter has a compiled-in prefix of
   `/install`, so copied out of its venv it cannot find `encodings` and dies
   before running a line. `pyvenv.cfg`'s `home` is the answer.

The bundle is **ad-hoc signed** (`codesign --sign -`) — no certificate, no
Apple account — and `build()` refuses to return a bundle that does not verify.

**It must be launched by Launch Services — `open -a ~/Applications/KAVACH.app`.**
This is the part that wasted the most time. TCC attributes a request to the
*responsible process*, and a bundle started from a shell inherits the shell's
identity, so the app's own grant is ignored and the request dies in ~100 ms.
Launched with `open -a`, the bundle is responsible for itself and the same
grant is honoured — verified: the request took **4.2 s** and returned
`camera granted`, versus 101 ms and refused from a terminal.

Two traps found while proving it, both silent:

* **`PYTHONPATH` must include the project root.** `kavach` is the source tree,
  not a site-packages install, so it resolves from a shell only because the cwd
  is on `sys.path`. Launch Services starts at `/` and the app died with
  `No module named 'kavach'`.
* **Launch Services discards stdout and stderr**, so that failure left no trace
  and looked exactly like the app starting and doing nothing. The launcher now
  redirects to `~/.kavach/logs/overlay.out` — read that first when the app
  "does not start".

Confirmed working: `kavach.gestures.tracker: hand tracking live`, MediaPipe on
the M4 Pro GPU. Until it is allowed, gestures stay off and the code path is
otherwise complete:
MediaPipe HandLandmarker in `gestures/tracker.py`, six gestures in
`recognise.py` (confirm, deny, stop, point, peace, none), fed to the brain over
the bridge.

### Language detection — how the reply language is chosen

`SpeechToText.transcribe()` **must** pass `language="auto"`. Left unset,
whisper.cpp pins the decoder to English and Hindi returns as a mistranslation
("today my meeting is how many hours are"). That was the state until
2026-08-13, and it silently disabled every multilingual reply Phase 8 built.

The reply language is read from the **script of the transcribed text**
(`language_of_script`), not from whisper.cpp. Two reasons:

* `get_params()["language"]` returns what we *configured*, not what was heard —
  it answered `en` for every turn, which is the bug above.
* `auto_detect_language()` does give the truth (Hindi measured 0.853) but
  **re-runs the encoder: 597 ms against a 609 ms transcribe**, nearly doubling
  every turn. Available as `KAVACH_DETECT_LANGUAGE=full` for the Latin-script
  languages a script test cannot separate.

Script detection is exact for Hindi, Japanese and Mandarin and deliberately
returns None for Latin — it cannot tell English from Spanish, and a romanised
Hinglish transliteration (what Apex and Swift return) must stay English-voiced.

### Speech models verified 2026-08-13 (§21 — do not re-derive)

Read off the Hugging Face file listings, not estimated. **The name tells you
nothing about the size** — that was the whole point of verifying.

| Model | Params | Download | Pulls | Licence | Base |
|---|---|---|---|---|---|
| stock `large-v3-turbo` | 809 M | ~1.6 GB | — | MIT | — |
| `Oriserve/…-Apex` | 809 M | 1.62 GB | 156 K | apache-2.0 | **`large-v3-turbo`** |
| `Oriserve/…-Prime` | 1543 M | 6.17 GB | 395 K | apache-2.0 | large-v3 |
| `Oriserve/…-Swift` | 72.6 M | 290 MB | 24.6 K | apache-2.0 | whisper-base |
| `Trelis/whisper-hinglish-preview` | 1543 M | 6.17 GB | 6.8 K | apache-2.0 | vaani-hindi |

**None of them ship GGML** — all transformers checkpoints, and KAVACH runs
whisper.cpp. That gap is invisible on the model cards and is why
`voice/convert_ggml.py` exists.

**Apex is the one to recommend**: a fine-tune of the exact model KAVACH already
runs, same size. Prime's own README says Apex supersedes it.

`gripened/auderly-whisper-hinglish-ggml` is a ready-made 1.08 GB GGML of the
Trelis model and is **deliberately not used**: 0 downloads, and its licence
reads `other` while the weights it derives from are apache-2.0. A test asserts
every registry entry declares a permissive licence, so it cannot be added by
forgetting a field.

The converter is whisper.cpp's own `convert-h5-to-ggml.py`, pinned at v1.8.2
and **SHA-256 checked before it runs** (`9cc282df…`, 7891 bytes, byte-identical
across v1.7.6–v1.8.2). Do not swap it for a hand-written GGML serialiser.

### Apple constraints verified 2026-08-13 (do not re-derive)

* **No Xcode on this machine** — Command Line Tools only, no Simulator. Any
  native iOS/watchOS work starts with a ~15GB install. The user declined it.
* **Free personal team**: 7-day provisioning profiles, 3 apps, 10 App IDs/week,
  and Xcode **refuses App Groups, Push Notifications and iCloud** outright.
* **No push ⇒ phone approvals are pull-only.** Nothing can wake the phone when
  KAVACH needs an answer, and confirmations expire after 120 s.
* `tailscale serve --bg 8770` — auto TLS, no admin console setup, survives
  reboots, works on the Mac App Store build.

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

### Hands layer notes (Phase 4) — READ BEFORE TOUCHING THE GATE

- **The enforcement point is the `PreToolUse` hook, NOT `can_use_tool`.**
  Verified live: a wildcard in `allowed_tools` auto-approves before
  `can_use_tool` (the SDK raises `CanUseToolShadowedWarning`), and with
  `allowed_tools` empty the CLI's interactive prompt intercepts instead and
  dies with "AbortError: Stream closed" in a headless loop. The hook fires in
  both cases. **A test asserts the hook is wired for all tools — do not
  remove it.**
- `NEVER_ALLOWED_TOOLS` (`Shell`, `agent`, `Desktop`) are refused outright and
  not exposed. A shell command names no app, so the allowlist cannot check it
  — it is a complete bypass of §7, not an edge case. Confirmation is never
  offered for these.
- Denial is the default at every step: unknown server, unidentifiable target
  app, missing confirmer, unclear spoken answer, timeout. Consent must be
  given, never merely not-withheld.

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
