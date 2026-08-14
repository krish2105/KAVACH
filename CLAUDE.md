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

**Verified live on 2026-08-14**, push-to-talk, both agents started by launchd:

```
heard  "What is the time right now?"
said   "It's 8:35 p.m."          (correct — it was 8:37 when checked)
route  local · conf 0.92 · intent clock · "simple intent (clock)"
stt 1699ms · respond 7ms · tts 4941ms · perceived 6648ms
```

`respond_ms = 7` is the point: the clock answer comes from the system clock,
never a language model. **TTS is 74% of the wait** — if latency is ever worth
attacking, Kokoro is where it lives, not STT.

The turn before it was discarded: `no speech in the clip (4/61 voiced frames,
rms 0.0045, 1.2s)`. The turn ends on silence, so clicking TALK and *then*
gathering your thoughts closes it before you speak.

**Wake word v2** (2026-08-13): AUT 0.0040, FPPH 0.00, recall 83.8%, measured
optimum **0.20**. **Calibration on the user's voice failed on 2026-08-14** —
`kavach-waketune` refused to save, correctly: no separation between the wake
takes and ordinary speech.

The model is not the broken part. Scored through the same detector on Kokoro
speech: `KAVACH` 0.858 / 0.812 / 0.301 across three voices, the four negative
phrases 0.002–0.013, margin **+0.288, separated**. So ordinary speech is near
zero and the overlap came from the *wake takes scoring low* — the v1 failure
shape again, on a model trained to avoid it. Note even a synthetic British
voice managed only 0.301, so voice and accent move this a great deal.

`FLOOR = 0.30` clamps any saved threshold, so takes scoring below 0.30 cannot
be rescued by calibrating — re-running is not a remedy for that case, and
`diagnose()` now says which side failed instead of always blaming the
negatives. **Push-to-talk remains the default and the honest one.**

**Root cause (2026-08-14): the model does not survive a real microphone.**
Played KAVACH's own TTS through the speakers and recorded it back:

| | |
|---|---|
| the file, straight into the detector | **0.858** |
| the same utterance recorded through the mic | **0.019** |
| whisper on that same recording | `"Kavec, Kavec, testing 1, 2, 3."` |

Whisper reads it perfectly at rms 0.09, so the audio is present, intelligible
and correctly captured. The wake model scores it at noise level. The user's own
takes (0.041–0.089) are that same number, not a quirk of their voice.

It is a domain gap: v2 was trained entirely on synthesised speech, and scores
0.858 / 0.812 / 0.301 across three synthetic voices while scoring ~0.02 on
anything that has been through a speaker, a room and a microphone.
**Retraining needs real recorded audio or convincing augmentation (room
impulse responses, mic colouring, noise) — not more synthetic voices.**

**v3 (2026-08-14) — the augmentation was never switched on.** The trainer
downloads MIT RIRs and MUSAN noise into `data_dir`, then reads them from
`augmentation.rir_paths`, which defaults to `./data/rirs` — derived
independently of `data_dir`. v2 never set it, so **270 impulse responses and
465 noise files sat unused** and `apply_rir()` returned every clip unchanged,
silently. v2's own log says `No background noise files found, skipping`.

`wakeword/kavach-v3.yaml` sets both paths and doubles the rounds.
`tests/test_wakeword_config.py` fails the build if either resolves to zero
files, which is the only symptom that bug ever had.

Measured, 30 wake clips and 30 others played through the speakers and recorded
back (`wakeword/realmic_eval.py`, which scores every model on the *same*
recordings so the room is held constant):

| model | wake worst→best (median) | best other | margin |
|---|---|---|---|
| kavach (v1) | 0.257 → 0.978 (0.690) | 0.638 | −0.381 |
| kavach_v2 | 0.010 → 0.648 (0.162) | 0.244 | −0.234 |
| kavach_v3 | 0.014 → 0.901 (0.173) | **0.148** | **−0.135** |

And on clean files, v3 gave nothing up — the weakest voice nearly doubled:
`af_heart` 0.857, `am_michael` 0.844, `bf_emma` **0.590** (v2: 0.301), worst
negative 0.006 (v2: 0.013).

**So v3 is strictly better and still not usable.** All three overlap through a
microphone; the median wake clip scores 0.173, well under the 0.30 floor.
Channel augmentation was a real bug and a real improvement, and it was not
sufficient on its own.

Caveat on that table: playing TTS through a laptop speaker puts the audio
through **two** channels (speaker colouring, then mic), where a human voice
goes through one. It is a harsher test than reality, so treat it as a lower
bound.

**The decisive measurement, run 2026-08-14 21:50 — v3 does not help the user's
voice.** `kavach-waketune` against v3, recorded in
`~/.kavach/wake-calibration-history.jsonl`:

```
wake takes  0.0071  0.1152  0.0416  0.0462  0.0054
others      0.0224  0.0066  0.0272  0.0145
margin      -0.022   overlaps, not saved
```

Against v2's 0.041–0.089 that is flat: best take up a little (0.089 → 0.115),
worst take down (0.041 → 0.005), median unchanged. **Everything scores near
zero — the negatives too.** The model is not confusing the wake word with
ordinary speech; it is blind to this microphone's audio in general, and the
best real take is still 7× below the 0.858 it gives a clean synthetic file.

So channel augmentation was a genuine bug and a genuine improvement — bf_emma
0.301 → 0.590, false alarms halved, real-mic margin −0.234 → −0.135 — and it is
**not what stands between this user and a working wake word.**

What remains is that **every training clip is synthesised.** The model has
never heard a human being. Closing that needs real recorded speech in the
training set, and 100 takes against 3000 synthetic clips is 3% — likely too
diluted to matter without oversampling or a fine-tune of the final layer. That
is a bigger piece of work than either retrain so far, and **push-to-talk works
today**, so it is the user's call rather than an obvious next step.

One observation for whoever picks this up: the five takes spanned 0.005 to
0.115, a 20× spread. `TAKE_SECONDS = 2.6` with a sliding 2s window means a take
that starts late is scored on a clipped word, so some of that spread may be
timing rather than voice.

Two hypotheses were tested and **refuted** on the way, both worth not
repeating:

* *the mic path aliases* — the naive `block[::3]` decimator scores **0.857**
  against 0.857 for a proper filtered resampler on the same audio. It was
  replaced anyway, on correctness grounds, but it fixed nothing.
* *the user's accent* — the collapse reproduces with KAVACH's own American
  TTS voice, which scores 0.858 as a file. v1's headline recall was higher (99.15%) and *meaningless* —
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
| 12 — Speaker ID | **complete** — `kavach-speaker on/off`. `Voiceprint.gating` (enrolled AND enabled) is what the loop asks; `is_enrolled` conflated "we know your voice" with "we are checking it", leaving `forget()` as the only way to stop checking. Enrolled defaults to ON so an upgrade cannot silently drop the gate, the setting persists, and both directions are logged. Eagle comparison needs a **paid Picovoice contract — ask before signing up** |
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

### A model with no hands must never answer a request that needs them

The Hands layer had **never executed a single tool** — the action log's
complete event list was router/voice/killswitch/ghost/api/web only. Not because
the gate was broken, but because requests never reached it.

`_SIMPLE_PATTERNS` treated *simple to understand* as *simple to answer*, so
`open|launch|quit|close X`, volume, mute and media control were routed LOCAL —
to a 3B chat model with no tools. Measured through the running system:

```
POST /command  "open Notes"
→ route=local · "simple intent (app control)"
→ reply: "Notes are now open."
→ Notes was not open.
```

A confident claim to have done something it never did — the worst failure this
project can produce. `_ACTIONABLE_INTENTS` now sends those to the tool route.

**The same fault sat behind the §7 confirmation, which is worse.** "delete the
note called X" matched no regex, so the local classifier called it simple and
its verdict was returned unchanged. The confirmation fired correctly and
blocked it; once approved, the resumed turn routed local *again* and answered
"There is no note called KAVACH" with no tool call. Nothing was deleted — and
the same path could as easily have said "Deleted." The classifier branch now
overrides a LOCAL verdict whenever `confirm` is set, which the short-utterance
branch below it had always done.

Proven live afterwards, first tool calls this project has ever made:

```
Bash                                  DENY   'Bash' is not a recognised MCP tool
ToolSearch                            allow  loads tool schemas only
mcp__macos-automator__execute_script  allow  → Notes actually opened
```

And on the destructive path: `router → claude (needs tools to act)`, then the
agent spoke the action back and waited a second time before calling anything.
Two independent gates, both holding.

### …and code with hands beats a model with hands

`reasoning/actions.py`. Escalating `open Notes` to the Claude route fixed the
lie, at the price of a model call, an MCP server and a subprocess for something
`osascript` does directly. `MacActions` runs it locally through the same four
gates, in this order — **kill switch → allowlist → script → log**:

```
'open Notes'              → ok=True  confirm=False  140.9ms  "Opened Notes."
'set the volume to 69'    → ok=True  confirm=False  198.2ms  "Volume 69."
'open Mail'               → ok=False confirm=True     0.2ms  "Mail isn't on your allowlist…"
'what did I do yesterday' → declined (escalates)       0.0ms
kill switch latched       → "The kill switch is latched, so I'm not doing anything."
```

Notes actually opened, and the log carries `action.app_open`, `action.volume`,
`action.refused` and `killswitch.blocked` with their arguments. **140ms, not
the ~50ms predicted** — `osascript` process spawn is the floor, and it is still
a different order of magnitude from a model round trip, and offline.

Two properties are load-bearing:

* **A recognised action is always answered, even to say it failed.** Anything
  `handle()` returns None for falls through to the local 3B model — the
  tool-less narrator this whole path exists to keep away from action requests.
  A timeout counts as failure: "did it work?" is not answered by "we stopped
  waiting".
* **The transcript never reaches AppleScript.** The name in the script is
  `Allowlist.canonical_name()`'s spelling, so `open notes` runs
  `tell application "Notes"`. Injection is ruled out by construction, not by
  escaping — and the name charset excludes `"`, `\` and `;`, so those make a
  string *not an app name* rather than something to escape and run.

**Deviation from the plan, deliberate.** The plan said `_ACTIONABLE_INTENTS`
would shrink to only what has no local handler. It cannot:
`test_router_actionable.py` asserts `open Notes` reaches the tool route, and
shrinking the set turns that test red. §B says never modify a test to make code
pass. So the router is **unchanged**, and the action path runs inside
`respond()` beside `_handle_music` — which is the established shape for
"deterministic action, no model". Anything `MacActions` declines still escalates
to the agent, so the tested guarantee is kept *and* the fast path exists.

**Media control was already local** (`music.py`), so it is not duplicated here —
two paths for one intent is how one of them goes stale. One fix was needed
though: with no player running, `_handle_music` fell back to an
installed-but-not-running app, so **"volume up" launched Apple Music in order to
turn its volume up**. Volume commands now decline when nothing is playing and
reach the system volume instead.

#### Widening the allowlist by voice — and the decision it leaves you

The user chose "ask to add it" over a flat refusal for an unlisted app. It reuses
the existing pending-confirmation machinery (no second consent path), the
bundle-id lookup doubles as an existence check so a mis-transcription cannot
leave a permanent grant, and the entry records `added by voice, <date>`.

**Speaker verification is load-bearing here in a way it is not elsewhere.**
Without it the allowlist is widenable by anything that can produce speech in the
room. `Voiceprint.gating` must be on or the add is refused and logged — a
missing voiceprint is a refusal, not a bypass.

**Unresolved, and yours to decide:** `test_allowlist.py::test_nothing_is_allowed_that_was_not_approved`
reads the real file and fails on any app not listed in its `APPROVED` dict. So
the first app you add by voice turns the suite red until you add a line there.
That is arguably the test doing its job — an addition that arrives unnoticed is
exactly what it guards against — but a red suite as a notification mechanism is
a choice, not an accident. **The test was not touched.**

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
