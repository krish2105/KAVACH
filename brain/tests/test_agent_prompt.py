"""The agent's system prompt must not carry its own copy of the rules.

This is the bug that started the whole change. From `~/.kavach/logs/actions.jsonl`
on 2026-08-15::

    12:30:33  router.decision  route=claude · "needs tools to act (app control)"
                               utterance: "Open google chrome and type youtube."
              ← no tool.decision between these two lines →
    12:30:54  voice.turn       said: "I can only act on Safari, Notes, Calendar
                               and Finder, so Chrome is off limits for me."

No `tool.decision` was recorded, so the gate never ran. Chrome had been on the
allowlist since 2026-08-13 and would have been permitted. The refusal came from
`agent.py:34`, which hardcoded the list in `SYSTEM_PROMPT` and had drifted from
the file that actually decided.

KAVACH asserted a limitation it did not have — the same class of failure as
claiming work it never did, and the same root cause both times: two sources of
truth for one fact.

This file forbids the **shape** of that bug, not the instance. `voice/__main__.py`
got the same treatment when it hardcoded an Ollama model name and silently
overrode the switch to llama3.2:3b.
"""

import kavach.reasoning.agent as agent
from kavach.hands.policy import Policy

from ._sourcecheck import code_text

#: Every app that has ever been on the allowlist, plus ones that never were.
#: If any of them appears in agent.py, someone has written a list by hand.
APPS = [
    "Safari", "Notes", "Calendar", "Finder", "Music", "Spotify",
    "Google Chrome", "Chrome", "Terminal", "Mail", "Messages",
]


def test_no_app_name_is_hardcoded_anywhere_in_the_module():
    source = code_text(agent)
    for app in APPS:
        assert app not in source, (
            f"{app!r} appears in agent.py. The prompt drifted from "
            f"allowlist.json once and made KAVACH refuse an app it was "
            f"allowed to drive — ask Policy instead of remembering."
        )


def test_the_prompt_is_generated_from_the_policy():
    """Not merely 'contains no app names' — it must come from the one place
    that decides, so it cannot be right today and stale tomorrow."""
    assert Policy().describe_capabilities() in agent.SYSTEM_PROMPT


def test_the_prompt_still_forbids_claiming_unfinished_work():
    """Load-bearing and separately verified: KAVACH once said "Notes are now
    open" when Notes was not open."""
    assert "never claim" in agent.SYSTEM_PROMPT.lower()


def test_the_prompt_still_says_replies_are_spoken():
    """Without this the model answers in markdown and KAVACH reads asterisks
    out loud."""
    assert "spoken" in agent.SYSTEM_PROMPT.lower()


def test_permission_mode_is_still_not_auto_approve():
    """§C: 'Never set permission_mode to auto-approve. This is not a tunable.'
    Widening the app policy does not widen this."""
    assert agent.PERMISSION_MODE != "bypassPermissions"
    assert agent.PERMISSION_MODE in ("default", "ask")
