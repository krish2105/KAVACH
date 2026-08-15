"""Phase 31 — watching Claude Code, and never touching it.

**Mechanism verified before writing, not assumed** (§A). Three candidates:

* *Hooks* — real, but configuring them means writing to
  `~/.claude/settings.json`. The spec says zero write access, so a mechanism
  that starts by editing the thing it watches disqualifies itself.
* *Session logs* — `~/.claude/projects/<slug>/<uuid>.jsonl`, one line per
  event. Confirmed on this machine: 3834 lines, types assistant/user/
  last-prompt, `tool_use` blocks carrying name and input alongside
  `tool_result` blocks carrying the output. **Both halves are present**, so
  outcomes are readable rather than only invocations.
* *File-watching* — the delivery mechanism for the above.

Session logs win because they are written whether KAVACH looks or not.
Watching them adds nothing to Claude Code's behaviour.

**Read-only is a property of the code, not an intention.** A test greps the
module for any write mode.

**Nothing is fabricated when the signal is ambiguous.** A run whose outcome
cannot be parsed produces no narration at all — "your tests finished" with no
idea whether they passed is worse than silence, because it invites the user to
stop checking for themselves.
"""

import json

import pytest

from kavach.observe.claudecode import (
    Observation,
    describe,
    observe_line,
    session_files,
)


def line(**payload) -> str:
    return json.dumps(payload)


def tool_result(text):
    return line(type="user", message={
        "content": [{"type": "tool_result", "content": str(text)}]})


# ═══ read-only, enforced ═══

def test_the_module_never_opens_anything_for_writing():
    """Zero write access to any Claude Code session — stated in the spec as
    intent, asserted here as a property. This watches; it never intervenes."""
    import inspect

    from kavach.observe import claudecode

    from ._sourcecheck import code_text

    source = code_text(inspect.getmodule(claudecode))
    for forbidden in ('"w"', "'w'", '"a"', "'a'", '"r+"', "write_text",
                      "unlink", "rename", "mkdir", "os.remove", "rmtree"):
        assert forbidden not in source, (
            f"{forbidden} appears in claudecode.py — this module is read-only"
        )


def test_it_reads_sessions_without_creating_anything(tmp_path):
    found = session_files(tmp_path)

    assert found == []
    assert list(tmp_path.iterdir()) == [], "watching created something"


# ═══ what it can report ═══

def test_a_passing_test_run_is_recognised():
    observed = observe_line(tool_result("1151 passed, 2 skipped in 53.22s"))

    assert observed is not None
    assert observed.kind == "tests"
    assert observed.ok is True
    assert "1151" in observed.detail


def test_a_failing_test_run_is_recognised():
    observed = observe_line(
        tool_result("2 failed, 1149 passed, 2 skipped in 54.01s"))

    assert observed is not None
    assert observed.kind == "tests"
    assert observed.ok is False
    assert "2" in observed.detail


def test_an_error_during_collection_is_a_failure_not_a_pass():
    """"1 error" with no failures still means the suite did not run."""
    observed = observe_line(tool_result("ERROR collecting tests/test_x.py"))

    assert observed is not None and observed.ok is False


# ═══ nothing fabricated ═══

@pytest.mark.parametrize("text", [
    "some unrelated output",
    "",
    "Reading file contents 1 2 3",
    "passed the salt",          # the word, not a result
])
def test_ambiguous_output_produces_nothing(text):
    """"Your tests finished" with no idea whether they passed is worse than
    silence — it invites the user to stop checking for themselves."""
    assert observe_line(tool_result(text)) is None


def test_a_malformed_line_is_ignored_not_guessed():
    assert observe_line("{not json") is None
    assert observe_line("") is None


def test_a_line_with_no_message_is_ignored():
    assert observe_line(line(type="queue-operation")) is None


# ═══ what it says out loud ═══

def test_the_narration_says_what_happened():
    spoken = describe(Observation(kind="tests", ok=False, detail="2 failed"))

    assert "fail" in spoken.lower()
    assert "2" in spoken


def test_the_narration_is_short_enough_to_speak():
    """TTS is 60-75% of every turn's latency in this project. A narration
    nobody asked for must not cost eight seconds."""
    for ok in (True, False):
        spoken = describe(Observation(kind="tests", ok=ok, detail="1151 passed"))
        assert len(spoken) < 90, spoken


def test_nothing_is_narrated_for_an_unknown_kind():
    assert describe(Observation(kind="mystery", ok=True, detail="x")) is None
