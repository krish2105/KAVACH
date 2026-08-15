# Recall — one index, provenance on every answer

**Date:** 2026-08-16
**Scope:** the first of two sub-projects. Proactive (Phase 23-style briefing,
"you said you'd reply to X") is deliberately deferred — it has nothing to say
until this exists.

---

## 1 · Why this, and why first

### 1.1 · The research

Three findings shaped this, and the first one shaped it most.

**Voice assistant abandonment is task-specific, not total.**
[CHI 2023](https://dl.acm.org/doi/10.1145/3544548.3581152) found users build
"complex mental models of which tasks they can trust their voice assistants
with", and that a single failure — one truncated text message — ends that task
permanently. Notably, failures attributed to *language* caused wholesale
abandonment, which matters for a Hinglish speaker.

**The features people use daily are boring ones.** Finding old messages,
summarising threads, meeting notes, drafting follow-ups — [not novel
workflows](https://www.solidaitech.com/2026/05/daily-ai-habits-tools-routine.html).

**The local-first field is crowded and shallow.** Jarvis CLS, Willow, Mycroft,
VoiceInk are all STT → LLM → TTS. **None of them remembers anything**, and
none has an audit log or a kill switch.

Recall sits exactly where those three meet: it is a routine friction task, it
is what nobody else does, and its failure mode is "I don't have that" rather
than a confident wrong answer.

### 1.2 · KAVACH already has this and throws it away

```
memory entries: 0        (turns: 0, files: 0, notes: 0)
loop.py:745   self.memory.remember(...)     ← the write path exists
loop.py:222   memory=None                   ← the default
__main__.py   VoiceLoop(...)                ← never passes one
```

`MemoryStore` (sqlite-vec) and `SessionRecorder` are built and tested. Nothing
constructs them, so `self.memory` is always `None` and every turn skips the
write. **This is the seventh instance of built-but-unwired found in this
project**, after the startup banner, the Ollama model name, the agent prompt, a
duplicated constant, two disagreeing server lists, and endpointing logic fixed
in the copy that does not run.

The wiring is seven lines. Most of this spec is about what to do once it works.

---

## 2 · What it indexes

| source | how | why it is safe |
|---|---|---|
| **turns** | automatic | KAVACH's own history; already in the action log |
| **actions** | automatic | already logged, with arguments and timestamps |
| **files** | **on request** | you name a path or a folder |
| **Messages, Mail, Safari** | **on request** | per-source, explicitly |
| ~~screen content~~ | **never** | cut by the user; stays cut |
| ~~ambient audio~~ | **never** | §7 — audio not acted on leaves no trace |

**The line is passive versus asked.** Turns and actions are things KAVACH did
and already recorded. Everything else requires "index my Documents folder".

A note on the earlier decision, because it was nearly misread: CLAUDE.md cuts
*"screen content or ambient audio"* — continuous passive capture. Indexing a
file when asked is a different act, and the cut is unaffected by this spec.

---

## 3 · The privacy artifact

Indexing Messages creates **an embedded, searchable copy of your conversations**
in `~/.kavach/memory.db`. Naming that plainly is part of the design:

* mode `600`, gitignored, never leaves the machine
* `kavach-memory forget <source>` purges one source; `--all` purges everything
* the index records **what was indexed and when**, so it can be audited
* **it is not encrypted at rest.** If FileVault is off, neither is this.

That last line belongs in the user docs, not only here.

**Indexing is a PROPOSE-tier action** (Phase 30). Asking KAVACH to index your
Mail queues rather than running, because it is a large, slow, privacy-loaded
operation and the queue is exactly the surface for "are you sure".

---

## 4 · Provenance, and refusing

Every answer carries where it came from and when:

> *"On Friday at 8pm you asked me to open Chrome — that's from the action log."*
> *"Vatsal sent that link on the 3rd — from Messages."*

**No answer is ever synthesised from a weak match.** Below the similarity
threshold it says "I don't have that" and stops.

This is the CHI finding applied directly: a confident wrong answer ends the
task forever, and an honest miss does not. It is also the rule the rest of this
project already follows — the clock never reaches a language model, `MacActions`
declines rather than narrating, and a monitor that cannot read the battery
reports `unknown` rather than "all clear".

**The model never sees a retrieved document without its provenance attached**,
so it cannot summarise three sources into one unattributed claim.

---

## 5 · Shape

```
kavach/memory/
  store.py      exists — sqlite-vec, embed, search
  session.py    exists — rolling 15-minute window
  sources.py    NEW — one indexer per source type
  recall.py     NEW — question → answer with provenance, or refusal
```

**`sources.py`** — each source declares three things: what it reads, how it
names a result, and how it timestamps one. Turns and actions read the action
log; files read the disk through `FileTools` (so the kill switch and the §7 log
still apply); Messages reads `chat.db` read-only.

**`recall.py`** — embed the question, search, rank, attach provenance, refuse
if the best match is weak. One entry point: `recall(question) -> Answer | None`.

**Router** sends *"what did I…"*, *"when did I…"*, *"where is…"*, *"what did X
say about…"* to recall **before** the agent — the same shape as the clock
patterns, and for the same reason: a model with no index does not decline, it
invents.

**Wiring** — `MemoryStore` into `VoiceLoop`, which makes the existing write path
live.

---

## 6 · What is out of scope

| | why |
|---|---|
| Proactive briefing | the second sub-project; nothing to say until this works |
| Meeting notes | wants recording and diarisation; its own spec |
| Encryption at rest | FileVault is the right layer; say so rather than half-build it |
| Indexing Messages automatically | the user chose on-request; automatic is nearer the passive capture that was cut |

---

## 7 · Testing

Per §B, tests first, shown failing.

| area | test |
|---|---|
| wiring | a turn writes to memory; `memory=None` still works |
| provenance | every answer carries source and timestamp |
| refusal | a weak match returns None, never a synthesised answer |
| the cut | no indexer exists for screen content or ambient audio |
| purge | `forget <source>` removes exactly that source |
| gating | indexing is PROPOSE-tier; file reads go through `FileTools` |
| §7 | indexing is logged with the source and the count |
| router | "what did I do yesterday" reaches recall, not the agent |

Unchanged and not to be weakened: `permission_mode` stays `"default"`;
`VoiceState.as_dict()` stays field-identical to `KavachSnapshot`; the
`PreToolUse` hook stays wired for all tools.
