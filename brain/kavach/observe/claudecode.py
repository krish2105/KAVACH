"""Phase 31 — watch Claude Code, never touch it.

**The mechanism was verified before this was written** (§A), because the spec
asked for that specifically and because guessing here would produce something
fragile that looked fine.

Three candidates existed:

* **Hooks.** Real, and they would give clean structured events. But
  configuring them means writing to ``~/.claude/settings.json`` — and a
  read-only observer whose first act is editing the thing it observes has
  already broken its own rule. Disqualified by the spec, not by capability.
* **Session logs.** ``~/.claude/projects/<slug>/<uuid>.jsonl``, one JSON
  object per line. Confirmed on this machine: 3834 lines in the active
  session, with ``tool_use`` blocks carrying the tool name and input, and
  ``tool_result`` blocks carrying the output. **Both halves are there**, so
  outcomes can be reported rather than only "something ran".
* **File-watching.** How the above gets delivered.

Session logs win for a reason worth stating: they are written whether KAVACH
looks or not, so watching them changes Claude Code's behaviour by exactly
nothing.

**Read-only is enforced, not intended.** Nothing here opens a file for
writing, and `test_the_module_never_opens_anything_for_writing` greps this
module for every write mode and fails the build if one appears.

**Silence beats a guess.** A result that cannot be parsed produces no
observation. "Your tests finished" with no idea whether they passed is worse
than saying nothing, because it teaches the user to stop checking — the same
reason `MacActions` refuses to narrate an action it did not take.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("kavach.observe.claudecode")

#: Where Claude Code keeps its per-project session transcripts.
SESSIONS_ROOT = Path.home() / ".claude" / "projects"

#: pytest's summary line. Anchored on the counts so the word "passed" in
#: ordinary prose cannot trip it — "passed the salt" is not a test result.
_PASSED = re.compile(r"\b(\d+)\s+passed\b")
_FAILED = re.compile(r"\b(\d+)\s+failed\b")
_ERRORS = re.compile(r"\b(\d+)?\s*ERROR", re.IGNORECASE)


@dataclass
class Observation:
    """Something that happened, that KAVACH is confident enough to say."""

    kind: str
    ok: bool
    detail: str


def session_files(root: Path | str = SESSIONS_ROOT) -> list[Path]:
    """Every session transcript under `root`, newest last.

    Creates nothing — not the directory, not an index. A watcher that has to
    set something up first is a watcher that changed what it watches.
    """
    base = Path(root)
    if not base.exists():
        return []
    found: list[Path] = []
    for project in sorted(base.iterdir()):
        if project.is_dir():
            found.extend(sorted(project.glob("*.jsonl")))
    return found


def _result_text(payload: dict) -> str | None:
    """The text of a `tool_result` block, or None if this line has none."""
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None

    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        body = block.get("content")
        if isinstance(body, list):
            return " ".join(
                str(part.get("text", "")) for part in body
                if isinstance(part, dict)
            )
        if body is not None:
            return str(body)
    return None


def observe_line(raw: str) -> Observation | None:
    """Read one transcript line. Returns an observation, or None.

    None is the common case and the safe one: most lines are prose, edits or
    reads, and none of those is worth interrupting someone about.
    """
    if not raw or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        # A partially-written line is normal when tailing a file being
        # appended to. Not an error, and definitely not a guess.
        return None
    if not isinstance(payload, dict):
        return None

    text = _result_text(payload)
    if not text:
        return None

    failed = _FAILED.search(text)
    passed = _PASSED.search(text)
    errored = "ERROR" in text and ("collect" in text or "Traceback" in text)

    if failed:
        return Observation("tests", False, f"{failed.group(1)} failed")
    if errored:
        return Observation("tests", False, "errored before running")
    if passed:
        return Observation("tests", True, f"{passed.group(1)} passed")
    return None


#: How each kind is spoken. A dict rather than branches so an unknown kind
#: falls through to None instead of to a default sentence — the same reason
#: `handlers.py` returns None for intents it does not have.
_NARRATION = {
    ("tests", True): "Your tests passed — {detail}.",
    ("tests", False): "Heads up, your tests are failing: {detail}.",
    ("build", True): "Your build finished.",
    ("build", False): "Your build failed: {detail}.",
}


def describe(observation: Observation | None) -> str | None:
    """One short spoken sentence, or None if there is nothing certain to say.

    Deliberately short. TTS is 60-75% of every turn's latency in this project,
    measured three times, and a narration nobody asked for must not cost eight
    seconds of speaking.
    """
    if observation is None:
        return None
    template = _NARRATION.get((observation.kind, observation.ok))
    if template is None:
        return None
    return template.format(detail=observation.detail)


__all__ = ["Observation", "observe_line", "describe", "session_files",
           "SESSIONS_ROOT"]
