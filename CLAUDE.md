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

### v4 (2026-08-15) — it learned the voice and did not learn to hear

The first model trained on real human speech. 42 takes recorded with
`kavach-wakerecord`, injected as clean round-0 clips by `kavach/voice/
wakeinject.py` and oversampled 25× to 26% of the positive set. Trained by
`kavach-waketrain`, which clones v3's generated corpus rather than re-running
VoxCPM.

**It fit the voice, and it did not generalise:**

| v4 scoring | median |
|---|---|
| the 42 takes it trained on | **0.830** |
| fresh utterances it had never heard | **0.034** |

Calibration on new speech: wake `0.0229 0.0187 0.1895 0.4643 0.0340`, others
`0.0351 0.0278 0.1165 0.0169`, **margin −0.0978, not saved** — worse than v3's
−0.0221, because a negative reached 0.117.

That 24× train/fresh gap is overfitting, and it is *informative*. v3 could not
fit this voice even on training-adjacent audio (median 0.041); v4 fits it at
0.830. **So the architecture, the channel augmentation and the injection
pipeline all work — the missing ingredient is purely the number of UNIQUE
utterances.** 42 is not enough and oversampling cannot fix it: more copies of
the same 42 deepens the memorisation. A few hundred distinct takes, across
days and positions, is what this would need.

**Do not re-run v4 with more copies. That is the one change guaranteed not to
help.**

### The Whisper wake word — built, and not yet working either

`kavach/voice/wakewhisper.py`. VAD gates the mic; a burst of speech is
transcribed by a small local Whisper and the text fuzzy-matched. Motivated by
the one measurement that has always held: whisper reads this microphone
(`"Kavec, Kavec, testing 1, 2, 3."`) where every ONNX model scores 0.019.

The match threshold is measured, not chosen — `kavec` 0.727 (the real
transcript, must match) against `catch` 0.667 (must not). 0.70 is the only gap.

Measured against the 42 recorded takes:

| | recognised | median |
|---|---|---|
| swift (whisper-base), 1 word | 8/42 = 19% | 151ms |
| swift, 3 words of context | 13/42 = 31% | 127ms |
| large-v3-turbo, 1 word | 10/42 = 24% | **1725ms** |

large-v3-turbo is too slow to gate a microphone regardless of accuracy, and it
returned **empty strings** for most clips.

**That evaluation is not conclusive, and the reason matters.** Those clips are
tight ~1s *single words*, trimmed that way because the ONNX trainer needs it —
and an isolated single word is whisper's worst case. Real use is "hey kavach,
what time is it", a sentence with context, which is the shape of the transcript
that did work. **The live path has not been tested.** Judge it with the real
microphone and natural speech before concluding anything.

### The wake word is "hey there" now, and it works (2026-08-16)

**The user changed it, after seven attempts at "Kavach".** That was the
right call and the evidence was overwhelming by then:

```
"Kavach" inside a sentence   dropped 7/7 by whisper
"Kavach" on its own          17–24 of 42, and often ''
whisper's spellings          'cabbage', 'go watch', 'coverage', 'कवच'
```

Whisper has no representation for a Sanskrit word, so it drops it,
transliterates it inconsistently, or writes it in another script. **Four
trained ONNX models and five separate fixes to the machinery were all
treating symptoms of that one fact.**

Measured with the new phrase, same rig, same room:

| | "Kavach" | "hey there" |
|---|---|---|
| recall (synthetic) | 4/4 | **5/5** |
| recall (user, real) | 0/8 live | — |
| false wakes | 0 | **0/8** |
| median transcribe | 272ms | **179ms** |

The negatives were built to trip it — *"hey, are you there?"*, *"is there
anything else"*, *"they were there yesterday"*, *"put it over there"*, *"hey
Sam, how are you"*. Zero fired, on file and again through the speakers.

**Two common words need stricter matching than one rare one.** "kavach" had
only nonsense neighbours, so 0.70 fuzzy was safe. "hey" sits beside "they"
(0.86) and "there" beside "where" (0.80). So: `MATCH_RATIO = 0.85`, a small
explicit homophone list per position (`their`, `they're`, `hay`), and —
carrying the whole defence — **the words must be adjacent and in order**.
Both words scattered in a sentence is not the phrase.

`WAKE_PHRASE` is one constant. The banner reads from it (`say "hey there" or
hold Space`) rather than repeating it, because a banner naming a different
wake word than the matcher is this project's most repeated defect.

**The lesson worth keeping:** five fixes went into the machinery — model
size, language pinning, hang timing (twice, in opposite directions),
pre-roll, minimum burst length — and every one was a real bug found by real
measurement. None of them was the problem. **The input was unreadable, and
no amount of correct plumbing fixes an unreadable input.**

Those five fixes all still apply and all still matter; they are why "hey
there" gets 5/5 rather than 3/5. But the order was wrong, and the question
"is this word transcribable at all?" should have been asked at attempt one.

### The wake word works (2026-08-16) — two bugs, neither of them the model

**It fires.** Through the speakers into the real microphone, which is the
harsher two-channel test:

```
03:19:52  kavach.voice.wakewhisper: wake word heard (2.0s)
03:19:58  no speech in the clip, discarding turn      ← nobody said a command
```

Four bursts of ordinary speech afterwards, **zero false wakes**.

**Bug 1 — the whisper detector was never switched on.** `VoiceLoop` has
taken `wake_backend` for a while and `voice/__main__.py` never passed it, so
the daemon built the ONNX detector every time. The ONNX detector refuses to
load without a calibration that has never once succeeded on this voice, so
the machine had **no wake word at all** while a working one sat in the tree.
Eleventh built-but-unwired instance, and the most expensive by elapsed time:
**four ONNX models were trained after the alternative was already written.**

**Bug 2 — the model could not hear the word.** `DEFAULT_MODEL` was `swift`,
a Hinglish fine-tune of whisper-*base*. On a clean file, no microphone:

```
swift           "Kavach, what time is it?" → "Have a nice day. What time is it?"
base.en                                    → "What time is it?"   (dropped it)
small.en                                   → "Kavac, what time is it?"    ✓
small                                      → "Kavach, what time is it?"   ✓
large-v3-turbo                             → "Kavach, what time is it?"   ✓
```

A base-sized model has no representation for a rare proper noun, so the
matcher downstream never had anything to match. **That also invalidates the
earlier 19–31% recall figure** — it measured a base model on tight 1s clips,
its two worst conditions at once.

Across four wake phrases and four ordinary sentences:

| model | recall | false | median |
|---|---|---|---|
| `small.en` | 3/4 | 0/4 | 256ms |
| **`small`** | **4/4** | **0/4** | **272ms** |
| `large-v3-turbo` | 4/4 | 0/4 | 1188ms |

`small` multilingual is large-v3-turbo's accuracy at 4.4x the speed.
`small.en` heard a bare "Kavach." as **"Cabbage."** — English-only is wrong
for this word and for this user's Hinglish. `FALLBACK_MODEL` moved off
`base.en` for the same reason: falling back to a model that cannot hear the
word is falling back to silence, which looks exactly like a broken mic.

**The project ruled out large-v3-turbo as too slow — correctly — and went
from base straight to large. Nothing in between was ever tried.** Seven
attempts, four trained models, and the answer was a model size.

**It did not fire for the user, and there was no way to find out why.** §7
means the daemon never logs a non-matching burst's transcript, so "it didn't
work" was the entire body of evidence — and that cannot separate *the model
never heard you* from *the model heard you and spelled it unusually*. Those
have opposite fixes, and guessing between them is how four ONNX models got
trained.

Transcribing the 42 real recordings with `small` gave the answer:

```
20x  ''            ← 1s isolated clips, whisper's worst case
 8x  'go watch.'   match ✓        2x  'ковыч!'   match ✗   ← Cyrillic
 1x  'कवच'         match ✗        1x  'kovach.'  match ✓   ← Devanagari, CORRECT
```

**The multilingual model writes this user's wake word in Devanagari and
Cyrillic, and `matches_wake` extracts `[a-z']+`** — so a transcription that
was exactly right got thrown away by the matcher. Pinning the wake
transcription to `language="en"` makes whisper transliterate instead:
**12/42 → 17/42**, and no non-Latin at all. `SpeechToText.transcribe()` now
takes an optional `language`; ordinary turns still pass `auto`, which is
load-bearing for Hindi and must not change.

`uv run kavach-wakecheck` is the missing diagnostic — opt-in, printed,
**stores nothing** (a test greps the module for every write path). It shows
the transcript and the closest near-miss per burst, which is how every
spelling in `WAKE_TARGETS` was found:

```
heard  'Kavach, what time is it?'            ✓ WAKE  (2.1s)
heard  'That was too short for me to check.' ✗ closest: 'that'→'kavatch' 0.36
```

#### The pause after "Kavach," was ending the burst (2026-08-16)

The user's own `kavach-wakecheck` run, saying "Kavach, what time is it?":

```
heard  'What time is it?'    ✗ closest 'what'→'kawach' 0.40  (1.1s)
heard  'cabbage, cabbage'    ✗ closest 'cabbage'→'gavaj' 0.33 (1.9s)
heard  'What time is it?'    ✗                             (1.0s)
0/8 burst(s) woke it.
```

**The wake word is missing from the front and the bursts are 1.0–1.2s** —
too short to hold both the word and the sentence. `HANG_S` was 0.35s and a
natural comma pause is longer, so "Kavach," closed its own burst and the
command opened a new one. An isolated one-second word is whisper's worst
case, which is the same reason 20 of 42 one-second recordings transcribed
to nothing. **The matcher was being handed audio with the word removed.**

`HANG_S = 0.7`. Verified on a file, which isolates it from the microphone:
one burst of 2.56s → `'Kavach, what time is it?'` → match.

This is the **third** explanation offered for a missing first word here, and
the first with a measurement behind it. The earlier note — "the segmenter
keeps the onset block at every offset, do not re-investigate" — was true and
about something else: the onset of a burst is kept, and the burst started
too late.

`cabbage` is an **exact** target, not a fuzzy one: it is what this
microphone writes for an isolated "Kavach" (seen in wakecheck and in the 42
recordings), and it is also an English word, so at 0.70 it would drag in
`garbage` (0.714) and sit near `carriage` (0.667). `EXACT_TARGETS` exists
for spellings that are real words.

A test in `test_wake_model_choice.py` had asserted `"Cabbage."` must **not**
wake it, written on the belief that it was `small.en` failing. The user's
run overturned that. Recorded in place rather than deleted: the test was not
wrong to exist, it was wrong about the world.

**Do not measure this with `say` through the speakers while `kavach-wakecheck`
is also running** — that is two processes on one microphone plus a
two-channel path, and it produced `'I know.'` for a phrase the same detector
reads perfectly from a file. Judge it on the user's voice.

One trap worth not repeating: a live `say` test that "did not fire" was the
**speaker volume**, not the wake word. At 80% it fires; quieter, no burst
reaches the segmenter at all. Check `Transcribing` counts before concluding
anything about matching.

Caveat: those transcripts are macOS `say` voices. They establish a floor and
the ordering between models, which is what a default depends on; the live
number on the user's own voice is still theirs to produce. The speaker gate
at 0.300 now sits behind the wake word, so a false wake still has to pass
voice verification before anything acts.

### Reach phases (the user's second numbering — restarts at 6)

Tags are `reach-N`, because `phase-6/7/8` were already taken by the earlier
expansion numbering. **Do not re-propose anything marked cut or blocked.**

| Phase | State |
|---|---|
| 6 — Local API surface | **complete** (tag `reach-6`) — FastAPI on 127.0.0.1:8770, bearer token, pending-confirmation flow |
| 7 — The phone commands KAVACH | **complete** (tag `reach-7`) — two Apple Shortcuts, `POST /kill`, Tailscale Serve |
| 8 — Apple Watch | **CUT — the user owns no Apple Watch.** Also: Tailscale has **no watchOS app** (iOS/iPadOS/tvOS/visionOS only), and `Get Contents of URL` is unreliable on watchOS. A Watch app would need the iPhone as a WatchConnectivity relay, hence Xcode |
| 9 — Remote access | **complete** (2026-08-16) — the tailnet leg is proven; see below |
| 10 — Tiered memory | **complete** (tag `recall`) — see *Recall* below. **Never index screen content or ambient audio** — the user cut that explicitly as a privacy/storage liability |
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

### Recall (§10) — and the two bugs that only appear once something calls you

`kavach/memory/recall.py`, `sources.py`, wired in `voice/loop.py`. Working:

```
"what did I ask you about the battery"
→ route=recall → "You asked about the battery level, and I said it was at 41%."
"what did I ask you about last March"
→ route=recall → "I don't have anything about that."
```

The refusal is half the feature. A model with no index does not decline, it
invents a plausible Friday — the clock lesson in different clothes.

**The threshold is a measured gap, not a chosen number.** `MIN_SCORE = 0.35`
in the plan would have rejected every result: the real scale here is
0.037–0.054. The replacement `MIN_LEAD = 0.25` then missed 2 of 4 true
matches. Measured properly, a match leads the field by 0.184–0.610 and
nonsense by 0.007–0.046, so **0.115 is the midpoint of a measured gap** —
same method as `choose_threshold`. Fourth time this project has been bitten
by a threshold set from an assumed scale.

**Eighth built-but-unwired instance:** `MemoryStore` and `SessionRecorder`
were built and tested, and constructed by nothing. `VoiceLoop.memory` was
always None, so every turn hit `if self.memory is not None:` and skipped.
`tests/test_memory_wired.py` asserts the daemon constructs one.

Wiring it surfaced a bug nobody could have hit: the sqlite connection was
opened on the constructing thread, and `remember()` runs on the voice loop's
thread *and* the API's `asyncio.to_thread` worker — `ProgrammingError:
SQLite objects created in a thread can only be used in that same thread`.
`check_same_thread=False` plus a lock, and the write is under **one** lock,
because splitting insert from vector leaves a torn row that no error
announces.

**It surfaced as silence**, which cost three wrong theories (stale daemon,
deleted-inode ghost file, early return): `remember_turn` swallowed the
exception at `debug` while the daemon logs at `info`. It logs at `warning`
now. A guard that eats an error must still say it ate one.

**API turns are remembered too**, through a shared `remember_turn()`. The
inline copy in the voice path is exactly how this write came to exist for
spoken turns only — a command from the phone happened, was logged, and left
nothing to recall.

**A question about the past must not become part of the past.** Found live
after everything above was green:

```
"what is the battery level"                 → "Battery is at 37%."
"what did I just ask you about the battery" → "I don't have anything about that."
```

The write was fine — 11 turns, the answer among them. The **field** was the
problem. Every recall question had itself been stored, and a recall question
is worded almost exactly like the turn it hunts for, so it scores near the
top and flattens the lead. Because the margin is *relative*, near-duplicates
do not raise the winner — they raise everything. Two prior questions were
enough to push the real answer under 0.115.

`remember_turn(..., route="recall")` now returns without storing. Nothing is
lost: a recall turn's content is either a fact already indexed or the
absence of one. Measured after: same question three times running, all three
answered, `turns: 3` — the four recall questions added no rows.

`route` defaults to `""` so an un-updated caller keeps storing rather than
silently stopping, which means a caller that forgets it pollutes quietly —
so an `ast` test asserts both call sites pass it. Its first version split on
`")"` and failed against correctly wired code, because
`getattr(loop, "memory", None)` carries an inner paren. **String surgery on
source is how a grep test reports a defect that is really a bug in the
grep** — the second time that happened in this phase.

Router note: recall patterns already existed in `_COMPLEX_PATTERNS`. A
duplicate set was added to `_SIMPLE_PATTERNS` without checking — **ninth
one-fact-in-two-places, and the first self-inflicted one.** The real bug was
smaller: the complex branch computed a label and discarded it, so `intent`
was `""` for every recall question.

### The kill switch latched in one process only

Found by wiring `kavach-memory index` to the gate and then testing it **from
a shell** rather than in-process:

```
$ uv run kavach kill                 ✓ latched
$ python -c "print(KillSwitch().state)"
  State.ARMED                        ← a NEW process disagreed
$ uv run kavach-memory index <dir>
  ✓ 1 file(s) indexed                ← read the disk while halted
```

`__init__` set `State.ARMED` unconditionally and `trigger()` only mutated
memory. The daemon was gated because every path in it shares one object;
**every separately launched CLI started armed.** The unit test passed
throughout because it shared a `KillSwitch` with the code it tested.

State is now a file beside `actions.jsonl`, written atomically. No file →
ARMED (a fresh install must not ship dead); `disarmed` → latched; **a file
that will not parse → DISARMED**, because §C says an ambiguous state stays
stopped. `_write_state` never raises — losing the ability to *record* a stop
must not become a failure to *stop*.

**A latch now survives a reboot.** That is "no auto-recovery" applied
honestly; `kavach rearm` is the only way out.

The first live re-test still indexed, because **the running daemon held the
old module** — `launchctl kickstart -k gui/$UID/com.krishna.kavach` before
believing any measurement of daemon behaviour.

Three defects led there, all from `cli.py` and `store.index_folder`
predating `memory/sources.py`:

* `index_folder` read the disk with `Path.read_text()` — no kill-switch
  check, no `file.read` in the §7 log. It lives in `sources.py` now and
  reads through `FileTools`; a test fails the build if `read_text` reappears
  in `store.py`.
* `forget` accepted only `turns` and `files` while `SOURCES` held four, so
  `forget actions` — the collection recording what KAVACH *did* — died in
  argparse. `test_memory_sources.py` asserted every source is purgeable and
  passed the whole time, because it reads the dict and the CLI did not.
* `status` under-reported for the same reason. Both derive from `SOURCES`.

One new test was written as `"read_text(" not in code_text(...)` and
**passed against the bug it was aimed at**: `code_text` emits bare
identifiers, so no token carries a paren and that assertion could not fail
for any input. A test that cannot go red reports a guarantee nobody
provides. Check a grep test against the real module before trusting its
colour.

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
'open Notes'  (was closed)  → ok=True   950.9ms  "Opened Notes."
'open Notes'  (already up)  → ok=True   249.5ms  "Opened Notes."
'open Mail'   (unlisted)    → ok=False    0.2ms  "Mail isn't on your allowlist…"
'what did I do yesterday'   → declined    0.0ms   (escalates)
kill switch latched         → nothing ran, killswitch.blocked recorded
```

Notes actually opened, and the log carries `action.app_open`, `action.volume`,
`action.refused` and `killswitch.blocked` with their arguments.

**Not the ~50ms the plan predicted, and the first number measured here — 140ms —
was wrong for the reason this project keeps finding.** It was taken against an
app that was *already running*, so it measured `osascript` spawn and not the
work. A cold launch is ~950ms because AppleScript's `activate` blocks until the
app is ready; ~250ms is the honest floor for an app already up, and 0.2ms for a
refusal, which never spawns anything. Still no model, no MCP server, no network.

**Verified by voice on 2026-08-14 22:42**, push-to-talk, through the daemon:

```
heard  "Open notes."
said   "Opened Notes."
route  claude (router) → action (what actually ran)
log    action.app_open  app="Notes"  requested="notes"  ok=true
stt 2195ms · respond 1810ms · tts 4310ms · perceived 8315ms   (Notes was closed)
```

`requested="notes"` beside `app="Notes"` is the injection defence working in
production: Whisper returned lowercase, the allowlist's spelling is what reached
AppleScript. Nothing transcribed is ever interpolated into a script.

Two notes from that run, neither a bug in this code:

* **The speaker gate rejected the user's own voice twice** before the turn that
  worked — similarity 0.528 and 0.361 against a 0.613 threshold — plus three
  clips discarded as no-speech. Six attempts for one command. §12's threshold is
  worth re-examining, and `kavach-enrol` is the lever.
* **TTS is 52% of the wait again.** The action itself is not what makes a spoken
  turn feel slow, which is the same conclusion the clock turn reached.

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

### Total access (2026-08-15) — and the string literal that was never the gate

KAVACH refused "open Google Chrome and search YouTube", saying it could only
act on Safari, Notes, Calendar and Finder. **Chrome had been on the allowlist
since 2026-08-13.** The log shows no `tool.decision` between the route and the
refusal — the gate never ran. `agent.py:34` hardcoded the list in its system
prompt and had drifted from the file that decided.

This is the third time this codebase has produced the same bug: the startup
banner (fixed by `Allowlist.app_names()`), the Ollama model name (fixed by a
grep test), and now the agent prompt. **A fact written in two places is the
recurring defect here.** Tests now forbid the *shape* — `tests/_sourcecheck.py`
parses with `ast` so a module may still *explain* the literal it must not
*contain*.

A second bug sat beside it: `parse()` matched only a bare `open X`, so
"Open notes for me." fell to the Claude route at **27,286ms** against the
250ms local path built for it — 109x, for two words of politeness.

**Neither was a permission problem, and Full Disk Access would have fixed
neither.** FDA governs files, not apps; KAVACH has no file tools, so granting
it changes nothing but the blast radius. It is deferred.

#### What replaced the allowlist

`hands/policy.py` — every installed app is allowed, and the question is which
**verb**. `hands/allowlist.json` survives for `confirm_always` and the iPhone's
per-tool policy; its mac `allowed` array no longer gates.

```
kill switch → Shell/agent (always confirm) → irreversible verb → allow
```

**The shell gets no classification, because none survives contact with a
shell.** Measured: `rm -rf`, `dd`, `git push --force`, `killall`, `> ~/.ssh/…`,
`chmod -R 777` and `curl|sh` **all** cleared the existing English-text check;
only the sentence "delete the note called X" tripped it. A pattern blocklist is
defeated by one line of `python -c "shutil.rmtree(…)"`, so it would look like a
gate and stop nothing. `test_policy.py` fails the build if one appears.

**A deleted test found a complete bypass.** `test_action_with_no_identifiable_app_is_denied`
used `do shell script "rm -rf ~/Documents"` and went red for an unrelated
reason. The new policy **allowed** it — the tool is named `execute_script`, not
`Shell`. The old gate had blocked it incidentally (no identifiable app), and
removing the allowlist removed that side effect. `reaches_shell()` checks the
payload as well as the name now. **This is §B's "never modify a test to make it
pass" paying for itself in the most literal way available.**

The confirmation prompt was equally wrong — "act on something (via
execute_script)". Anything reaching a shell is quoted **verbatim**; approving
`rm -rf ~` because the prompt said "act on something" is a trap, not consent.

#### Deliberate deviations (do not "fix" these back)

* **The app allowlist is gone**, contradicting §C. The user was shown the
  measured risks — speaker gate off, the room already transcribes YouTube
  adverts (`test_wakewhisper.py:293`), whisper renders the wake word as
  `Kavec`/`Gavach` — and chose it, keeping confirmation on irreversible actions.
* **peekaboo's `agent` is confirmed-then-allowed, not denied.** Its sub-agent
  loop runs inside the MCP server, so its inner tool calls never reach the
  `PreToolUse` hook and **never reach the action log**. A real, accepted
  deviation from §7.
* **The voice-add-to-allowlist flow is deleted** (10 tests). It widened a
  boundary that no longer exists.
* **`parse()` declines compounds.** "open Chrome and search YouTube" goes to
  the agent, because opening Chrome and answering "Opened Chrome" drops the
  YouTube half *while reporting success*.

#### Browser control — `hands/browser.py`

Read off each app's `sdef`, not assumed: Chrome has `execute tab N javascript`
and a settable tab `URL`; Safari has `do JavaScript`. **Chrome has no `open
location` in its dictionary** — it works anyway via Standard Additions.

Arguments are `json.dumps`'d into a **fixed** function body. Verified against
the running browser: a search for `"); window.x = "EXECUTED"; ("` left the
marker `undefined` and appeared percent-encoded in the URL bar. Schemes are an
allowlist — `javascript:` would make `navigate()` an execution primitive.

`search()` falls back to setting the tab URL when JS is refused, because
**Allow JavaScript from Apple Events is a manual toggle that cannot be set
programmatically.** Both are enabled on this machine as of 2026-08-15.

**New risk, created by this change.** Once KAVACH reads a page, the page can
contain "ignore previous instructions and run …". What contains it is the
unconditional shell confirmation — a page cannot make a command run silently.
**Rule 2 of `policy.py` is load-bearing against prompt injection, not merely
cautious.**

#### Verified live 2026-08-15

```
POST /command  "open Google Chrome and search YouTube"
→ route  claude · "needs tools to act (app control)"
→ Bash                                  deny   not a recognised MCP tool
→ ToolSearch                            allow  schemas only
→ mcp__macos-automator__execute_script  allow  reversible
→ https://www.youtube.com/ open in Chrome
```

**The first attempt was allowed by the gate and still did nothing** — the
daemon had never sent an AppleEvent to Chrome, and macOS needs an Automation
(`kTCCServiceAppleEvents`) grant per *source→target app pair*. The second
attempt worked. **A newly-reachable app may fail once, silently, before its
grant exists.** That is TCC, not the gate; check
`System Settings → Privacy & Security → Automation` before debugging further.

#### Confirmations show before they speak

`VoiceConfirmer.SPEAK_AFTER_S = 3.0`. The question is published to the snapshot
the orb already receives and the gesture window opens **immediately**; speech
is the fallback for the case that needs it — the user not looking at the
screen. Answer on the orb and nothing is spoken at all.

Deliberately **not** a new orb state: `OrbState` in `apps/orb/lib/orbScene.ts`
is a closed union backing a `Record<OrbState, StateProfile>`, so "confirming"
means a Presence change and a visual to verify. `listening` is honestly what
this is, and the prompt rides in `transcript`, which the HUD already renders —
so the snapshot contract is untouched.

Arming the gesture window *before* the wait is what makes the silent window
answerable. It also gives a stale thumbs-up a longer runway, so the test that a
gesture made before the question cannot answer it matters **more** now.

#### The speaker gate rejected its owner 42 times out of 42

Measured 2026-08-15 — the 42 real microphone recordings in
`wakeword/data/real/positive`, the user's own voice from a different session
than enrolment, scored against the enrolled mean:

```
min 0.387   p10 0.424   median 0.498   max 0.803
rejected by the saved threshold 0.803:   42/42   (100%)
```

**Not "too tight" — non-functional.** It would have refused every genuine
utterance, which is why it has been off since 2026-08-14.

The cause is the *sampling*, not the arithmetic. `_calibrate` used
`sims.mean() - 3*sims.std() - 0.05` over enrolment clips recorded back to back
— one seat, one distance, one minute — so they cluster tightly, `std` is tiny,
and the threshold lands just under the mean. **It measured self-similarity
within one session and used it as a proxy for across sessions.** Those are
different distributions, and tight clustering at enrolment is evidence the
clips were recorded together, nothing more.

`MIN_THRESHOLD` was **0.55**, above the user's real minimum of 0.387 — so even
a perfectly clamped calibration would still have locked them out. A floor
catches a broken measurement; it must not overrule a correct one. Now 0.30.

`choose_threshold(genuine, others)` places the threshold **halfway through the
measured gap** and returns `(None, why)` when they overlap — `waketune`'s rule,
reached the same way — naming the side that failed, because being told the
wrong half is broken sends you re-recording the wrong thing. `_calibrate` now
reports `calibrated=False`, since a threshold from one sitting should not claim
to be calibrated.

**The gate stays OFF.** Turning it on with an unverified number is precisely
what produced this. It needs `uv run kavach-enrol` from a second session —
different distance, different time of day — and then `choose_threshold` against
at least one other voice.

#### The speaker gate: three diagnoses, and only the third was right

Worth reading as a sequence, because the first two were confident and wrong.

**1. "The threshold is miscalibrated."** True but not the cause. 0.803 rejected
42 of 42 real recordings.

**2. "One second of audio is too short."** Also true, also not the cause. The
same audio scored 0.423 at 0.8s and 0.816 at 13.8s, and strangers plateaued at
0.53 — which looked decisive.

**3. The encoder was the fault.** Same data, same held-out split:

| audio | resemblyzer: you / **strangers** | ECAPA: you / **strangers** |
|---|---|---|
| 1s | 0.741–0.954 / **0.559** | 0.220–0.728 / **0.107** |
| 3s | 0.563–0.587 / **0.586** ← overlap | 0.314–0.406 / **0.136** |
| 7s | 0.562–0.618 / **0.552** | 0.334–0.403 / **0.102** |

resemblyzer's worst genuine (0.542) sits **below** its best stranger (0.586).
**No threshold existed at any duration** — which is exactly why two rounds of
tuning could not find one, and why both earlier diagnoses looked so good.

Now `speechbrain/spkrec-ecapa-voxceleb` (Apache-2.0; torch was already a dep).
`ENCODER` is stored in the profile and **a profile from a different encoder is
refused, not migrated** — embeddings are not portable, and accepting one gives
you a gate that "works" and verifies nothing.

Caveat on those numbers: enrolment and test came from the *same* recording
session, so genuine scores are optimistic for both. The comparison between
encoders is fair; the absolutes are not a field estimate.

`test_an_imposter_is_rejected` is **xfail-flagged, not fixed** — `synth_voice`
is a sum of sine waves, resemblyzer separated two tones, ECAPA does not. The
honest fix is real audio from gitignored dirs, which makes the test
machine-dependent. **The user's call.**

#### The gate is ON as of 2026-08-16 — threshold 0.300, and how it got there

**The enrolment was the broken part all along, not the encoder or the
arithmetic.** Shadow mode had collected four real turns and every one of them
would have been refused:

```
real turns   -0.042  +0.017  +0.128  +0.145     against threshold 0.383
```

Scored against the *old* profile, twelve synthetic voices landed at −0.10 to
+0.10 — **the user's own live speech scored inside the stranger range.** No
threshold existed. That profile was read-aloud sentences from one sitting;
it did not resemble how this person sounds to this microphone.

The fix used audio that was already on disk: **the 42 real-microphone
recordings in `wakeword/data/real/positive`**, made for the wake word and
never used for anything else. Re-enrolling on them moved the median from
~0.07 to **0.417** with strangers unchanged.

**Evaluated at 1s it still overlapped and `choose_threshold` refused** —
two held-out takes scored 0.024 and 0.031 against a best stranger of 0.106.
That refusal was correct *and* the wrong question: `verify()` never sees a
clip under `MIN_VERIFY_SECONDS` (3.0), so a threshold read off 1s audio
describes a regime that cannot occur. CLAUDE.md already recorded the reason
— the same audio scores 0.42 at 0.8s and 0.82 at 13.8s.

Re-measured at the length the gate actually judges (held-out takes joined
into 3.8s clips — real voice, real mic, realistic duration):

| | range | median |
|---|---|---|
| the user, 5 held-out clips | +0.345 … +0.634 | **+0.553** |
| 12 other voices | −0.174 … +0.106 | −0.040 |

`choose_threshold` → **0.300**, gap 0.239. Final profile enrolled on all 42
clips (44.0s) and checked in the safety-critical direction against it:
**0/16 other voices admitted** (−0.182 … +0.097).

`enrol()`'s own `_calibrate` is discarded explicitly — it measures
self-similarity within one sitting, which is the method that produced 0.803
and locked the user out. The threshold is set from the held-out measurement
and `calibrated=True` is only claimed because that measurement happened.

**The known failure mode to watch:** with gating on, a clip under 3.0s is
refused as `clip too short to verify` and **the turn is discarded**. All four
shadow-scored turns cleared 3s, so the endpointer's padding appears to carry
ordinary commands over the floor — but that is four samples. If short
commands start vanishing, `grep voice.rejected ~/.kavach/logs/actions.jsonl`
shows the reason, and `kavach-speaker off` reverses it. The confirmation path
already skips verification for this exact reason (`test_confirm_speaker.py`).

The previous profile is backed up at `~/.kavach/voiceprint.npz.bak-*`.

**Caveat, stated rather than buried:** the negatives are synthesised Kokoro
voices, not real people in this room, and the genuine clips are joined
single words rather than sentences. The channel is real and the separation
is wide (0.239), but a real second speaker has not been tested against this
profile.

### Three faults the first hour of the live gate exposed (2026-08-16)

Worth reading together: none was found by tests, all three by reading the
action log after the user actually spoke.

**1. A refused turn said nothing at all.**

```
20:30:14  similarity 0.6154  voice matches                     ✓
20:30:21  similarity 0.0     clip too short to verify (2.16s)  → DISCARDED
20:30:30  similarity 0.3177  voice matches                     ✓
```

The middle turn produced no speech, no orb change, no transcript — from the
room, indistinguishable from a dead microphone or a hotkey that never
registered. `rejection_message()` now answers, with **different words per
reason**: a clip under `MIN_VERIFY_SECONDS` is not a verdict about the voice
(the encoder never ran), so "I didn't recognise you" would send someone to
re-enrol over a duration problem. The turn is still discarded, still never
transcribed, still logged — only the silence changed.

**2. "search wwe on youtube" was answered by a model with no browser.**

```
router.decision  route=local · "local model classified as simple"
voice.turn       said="I'm unable to access external links or websites."
```

Four words, so the short-utterance shortcut claimed it. The turn *before* it
worked — "open Chrome and search YouTube" matched `app control` — but a bare
search names no app and matched nothing. Fourth instance of
`_SIMPLE_PATTERNS` treating *simple to understand* as *simple to answer*,
after `open Notes`, `find the master prompt`, and recall. **The mildest and
most instructive**, because the model refused honestly instead of inventing;
`hands/browser.py` had been reachable and working the whole time.

`web search` is now an intent in `_ACTIONABLE_INTENTS`, anchored on a **named
destination or explicit web verb** — never the bare word "search", because
"search my notes" is recall and "find my tax document" is the filesystem.
Those patterns sit above it and claim their phrasings first;
`test_router_websearch.py` fixes that ordering as the specification.

**3. The API reported a route belonging to a different turn.**

```
20:36:54  api.command      "search wwe on youtube"
20:36:54  router.decision  route=claude · web search
20:37:04  router.decision  route=local     ← a SPOKEN turn, interleaved
```

The response said `"route":"local"` for a turn that drove a browser. `respond()`
records the route on `self.state`, and the API read that field *after*
`asyncio.to_thread` returned. **Cosmetic in the response and not cosmetic in
memory** — `remember_turn` skips on `recall`, so an interleaved turn could
suppress a write or store one that should have been dropped, silently.
`respond(text, out=d)` fills a dict the caller owns.

The test for it was first written with two different-sounding utterances that
**both returned `local`** — the fixture has no agent, so everything falls
back and the test could not have distinguished a fix from the bug. The routes
are forced now.

#### The threshold has almost no headroom

Real turns since the gate went on: **0.6154, 0.3177, 0.3943, 0.3004** against
**0.300**. The last cleared by 0.0004. The joined-clip proxy predicted a worst
case of 0.345; real sentences score lower, exactly the caveat recorded above.

Strangers top out at **+0.106**, so the measured midpoint would be ~0.21 —
but `MIN_THRESHOLD = 0.30` clamps it. **The floor is now overruling a correct
measurement**, which is the mistake it was lowered from 0.55 to avoid. Left
alone deliberately: two real turns is not enough to move a security floor, and
every turn is being scored. Revisit with a week of `voice.score` data.

### Files — and the three-link chain that made them reachable (2026-08-15)

`hands/files.py` + `hands/file_server.py`. Read/list/search free; **writes and
deletes confirm**; deletes go to the **Trash, never `unlink`**; paths resolve
*before* anything checks them (`~/Documents/../../etc` is `/etc`).

**The shape matters more than the code.** Regex intents in `MacActions` is what
app control does and is wrong here — `open Notes` is a pattern, "find my tax
document from last year" is not. An in-process SDK MCP server gives the agent
real tools that arrive as `mcp__kavach-files__*` and therefore pass through the
**same `PreToolUse` hook**. Kill switch, §7 confirmation and the action log all
applied with **no new permission code**. A second permission path is how one of
them goes stale — this repo has now found that defect four times.

Wiring it exposed a real hole: `Policy.action_text` used the tool name only as
a **fallback**, so `delete_file` with `{"path": "/tmp/x"}` matched nothing —
the arguments are a path with no English verb — and **a delete was classed
reversible.** Any tool naming its verb would have slipped through.

Three fixes were needed, and each was only revealed by the previous one
working:

```
1. nothing told the model file tools existed → it reached for built-in Read
2. the denial said only "not a recognised MCP tool name"
                                             → it gave up, never searched
3. the gate did not trust the in-process server
                                             → it searched, found, called,
                                               and was refused at the door
```

**A denial the model cannot recover from is a dead end wearing a reason.**
Built-in file tools (`Read`, `Write`, `Glob`…) stay denied — they bypass
`FileTools`' gates and the §7 log — but the message now names the alternative.

Verified live:

```
Read                          deny   'use the kavach-files tools instead'
ToolSearch                    allow
mcp__kavach-files__read_file  allow  reversible
file.read  /private/tmp/kavach_read_test.txt  28 bytes
```

`confirmed_upstream=True` exists because the gate already confirms
`delete_file`; asking again is two prompts for one delete, and **a user asked
twice learns to say yes twice.** Not the default — a `FileTools` with neither a
confirmer nor the flag refuses.

**Full Disk Access is not granted.** File tools work everywhere else; protected
paths raise `PermissionError` carrying the Settings path, because "no mail
found" would be a lie about the cause.

### Wake word, attempt seven — one hypothesis refuted, no number earned

**Do not report a recall figure from `/tmp/variance.py`-style harnesses without
checking the transcripts first.** A 30-trial run returned `RECALL 0/30`, and
**27 of the 30 transcripts were empty** — while the *same* harness had cleanly
produced `"Kavak, what time is it?"` twenty minutes earlier. That is a
measurement of the harness, not of the wake word.

One signal is consistent across every run, though: **when a transcript appears
at all, the first word is missing.**

```
"Kavach, open Notes."          → "Open news."
"Jarvis, what time is it?"     → "What time is it?"
"Sentinel, what time is it?"   → "What time is it?"
```

**Hypothesis tested and REFUTED: the segmenter is not eating it.** `Segmenter`
keeps the onset block at every offset — word starting 10%, 30%, 50%, 70% or
90% into a block all yield the same 1.4s burst. There is no pre-roll gap. Do
not re-investigate this.

So the missing first word comes from whisper itself or from the capture path,
and which of those it is remains **unknown**. Also unresolved: playing `say`
through the speakers puts audio through **two** channels where a human voice
goes through one, so every one of these numbers is a lower bound.

**Never stop the voice daemon for a measurement without a guaranteed restart.**
It was stopped to avoid two whisper instances competing (a real contamination
recorded above), the measurement outlived its wrapper, and the user's assistant
sat down while they were out. Bootstrap it back *before* diagnosing anything.

### Hardening pass (2026-08-15) — enumerate, don't reason

The method that found things: **list every tool the agent can reach, call
`Policy.decide` on each with plausible arguments, and read off which return
ALLOW.** Two holes fell out immediately that no amount of reasoning about the
design had surfaced.

**`write_file` ran silently.** `action_text` was `"write_file /path content"` —
no English destructive verb, nothing in `confirm_always` — so it was allowed.
And the agent's `FileTools` is built `confirmed_upstream=True` on the premise
that the gate asks, so **nothing in the chain asked at all.** An overwrite is
worse than a delete here: delete goes to the Trash, an overwrite is gone.

**`Type`, `Click` and `Key` ran silently.** `Type` fills whatever has keyboard
focus, which may be a password field; `Key` answers a dialog the user never
saw; `Click` lands anywhere on a screen they are not looking at. Identical
danger to the browser's `click_text`/`fill_field` — the only difference was
which MCP server they arrived from, which is not a security property.

Both are the same mistake as the `do shell script` bypass: **the verb was not
in the place the check was looking.**

`ALWAYS_CONFIRM_TOOLS` is now `Shell, agent, click_text, fill_field,
write_file, Type, Click, Key`. **Reads stay silent, deliberately** — if
everything confirms then nothing does, the user stops reading the prompts and
the guardrail becomes a keystroke.

**Checked and found sound**, so do not re-derive: the shell-escape regex holds
against extra whitespace, tabs, newlines, `do script`, and `script(` with no
space; the URL scheme allowlist rejects `javascript:` in any casing and with
leading whitespace; `resolve_path` resolves symlinks and `..` to the real
target *before* the confirmation, so the prompt shows the truth; reads
truncate at 2 MB and **say so**.

**A grep for who imports each module found `hands/browser.py` imported by
nothing** — built, tested, and recorded as "web control, verified live" for a
day. What had been verified was calling it by hand. Run that grep before
believing any note in this file.

### Autonomy — Phases 30–34 (2026-08-15), and the ceiling

`kavach/autonomy/`. Three tiers: **AUTO** runs unattended, **PROPOSE** queues
for batch review, **ALWAYS_ASK** interrupts. Everything defaults to ALWAYS_ASK,
including action types nobody has classified — the case most likely to be
dangerous, because nobody has thought about it yet.

**Manage it with `uv run kavach-autonomy`.** Wiring it changed nothing on its
own: with no tier set, behaviour is byte-for-byte what shipped.

#### The ceiling is enforced in three places, because one is one too few

Anything that sends, deletes, buys or changes a system setting can **never** be
AUTO. Verified end to end against the running stack:

```
set_tier('delete_file', AUTO)           → refused, with the reason
100 approvals, then accept()            → offered PROPOSE, never AUTO
hand-edit the config to "auto", reload  → read back as ALWAYS_ASK
PROPOSE tier + delete payload           → queued · 0 executed · 0 asked
```

| where | why it is not redundant |
|---|---|
| `set_tier()` | raises rather than silently downgrading — a caller told it got AUTO would report the wrong thing |
| `_load()` | **the obvious way around a code rule is the file the code reads.** A hand-edited `auto`, or one written by a future phase or an agent that read a hostile page, is refused on load |
| the gate | re-checks rather than trusting assignment validated. A gate relying on someone else having checked is a gate with a second source of truth |

The gate check also reads the **payload**, not just the tool name:
`execute_script` is not a ceilinged word and its argument can be
`delete note 1`. Same hole `Policy.action_text` had one layer down.

#### Properties worth not undoing

* **No auto-execute timeout** (Phase 33). An unreviewed proposal sits or
  expires *unexecuted*. §7 already treats a timeout as a denial; a queue that
  ran on expiry would be the opposite rule living next door.
* **`EXPIRED` ≠ `REJECTED`.** "Nobody looked" is not "you said no", and Phase
  34 learns from approval history — counting a lapse as a rejection teaches it
  something that never happened.
* **Promotions are offered, never applied** (Phase 34). Silently lowering a
  gate because someone was agreeable five times is how a system ends up with
  permissions nobody chose.
* **A rejection resets the streak.** "Yes, yes, yes, no, yes" is not four
  approvals.
* **Demotion is instant, unconditional, and clears the streak** — without that
  the next approval re-offers at once and demoting means nothing.
* **A missing queue degrades PROPOSE to ALWAYS_ASK, never to AUTO.** A missing
  component must not make the system quieter.

#### Phase 31 — the mechanism, checked before building (§A)

`kavach/observe/`. **Hooks were rejected on the spec's own terms**: configuring
them means writing to `~/.claude/settings.json`, and a read-only observer whose
first act is editing the thing it observes has already broken its rule.

Session logs won: `~/.claude/projects/<slug>/<uuid>.jsonl`, with `tool_use`
*and* `tool_result` blocks, so outcomes are readable rather than only "something
ran". They are written whether KAVACH looks or not, so watching changes Claude
Code's behaviour by exactly nothing.

Verified against the real transcript, not fixtures: **178 observations** from
one session. Read-only is enforced by a test that greps the module for every
write mode. Ambiguous output produces **no** narration — "your tests finished"
with no idea whether they passed teaches the user to stop checking.

#### Two spec references that do not exist here

The Phase 30–34 brief says to continue from **Phase 29** and feed **Phase 23's
morning briefing**. Neither exists: this repo has Phases 0–5 and a second
numbering, reach 6–21. The three real dependencies (Phase 6 API, Phase 7 phone,
Phase 4 confirmations) were confirmed present first. Findings route to the
queue only — inventing a briefing to satisfy a reference is pouring a
foundation for a wall nobody asked for.

### Phase 9 — the tailnet leg, proven (2026-08-16)

```
https://krishnas-macbook-pro.tailec3d44.ts.net  →  proxy 127.0.0.1:8770
```

`tailscale serve --bg 8770`. **Serve needs enabling once on the tailnet**
through the admin console — the CLI cannot do it and says so, with a
node-specific URL. That is an account action, not a machine one.

**Serve is not optional here, it is the mechanism.** The API binds
`127.0.0.1` only (`DEFAULT_HOST`), and the tailnet IP was verified
unreachable without it. That loopback binding is the point: the API cannot be
reached from the network even by accident, and Serve is the controlled way
through — terminating TLS on the tailnet and proxying to loopback.

Verified end to end over TLS, not on loopback:

```
no token          → {"detail":"Bad or missing token."}
/status /pending /proposals  → all answer
/command          → "It's 12:35 a.m."
queue a write     → "write /tmp/kavach_phone_test.txt"
reject remotely   → {"decided":1}  ·  file never written
TLS               → HTTP 200 · ssl_verify_result 0
```

The last two lines are the ones worth keeping: the queue **held from the
tailnet** — a proposal rejected remotely never ran — and the description read
properly rather than "act on something (via write_file)".

**What this changed about the posture.** The API is now reachable from every
device on the tailnet, and Full Disk Access was granted the same evening. A
device holding the bearer token can ask KAVACH to read Messages and Mail. The
token is in `brain/.env`, mode 600. Reverse with
`tailscale serve --https=443 off`.

**FDA is attributed to the responsible process, and that is visible here.**
The daemon reads `~/Library/Messages` fine (18 entries, via the gated
`list_directory` → `file.list`), while the same venv python run from a shell
is denied — launchd is responsible for one, the terminal's parent for the
other. Both are correct. Do not "fix" the shell one by granting more.

### The defect this codebase keeps producing

**A fact written in two places, where one copy quietly stops being true.**
Found five times now, each time as a different-looking bug:

| where | symptom |
|---|---|
| startup banner | printed 4 apps while the file held 7 |
| `voice/__main__.py` | hardcoded model name overrode the switch to llama3.2 |
| `agent.py:34` | refused Chrome for two days after it was permitted |
| `voiceprint.py` | two `MIN_VERIFY_SECONDS` (0.8 and 3.0); the later won |
| gate ↔ agent | server lists disagreed; a found tool was refused at the door |

`tests/_sourcecheck.py` parses with `ast` so a module can **explain** a literal
it must not **contain** — grep cannot tell prose from code, and the modules
that must not hold a fact are exactly the ones whose comments must say why.

**The speaker gate is ON as of 2026-08-16** (threshold 0.300, enrolled from
the 42 real-microphone recordings — see above). Until then any voice in the
room was acted on, which mattered a great deal with no app allowlist and the
shell reachable; the unconditional shell confirmation was what stood in for
it, and still backs it up.

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
