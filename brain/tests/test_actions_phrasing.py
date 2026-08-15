"""The fast path must hear the sentence people actually say.

Measured 2026-08-15 from `~/.kavach/logs/actions.jsonl`::

    'open notes'                       → Action(OPEN, 'notes')      250ms
    'Open notes for me.'               → None                    27,286ms
    'Open Safari and search Google.'   → None
    'Open google chrome and type youtube.' → None

`MacActions` exists to answer app control locally in ~250ms without a model,
an MCP server or a subprocess. Two words of politeness dropped the utterance
to the Claude route instead — **109x slower** — for a request the local path
was built to serve.

The interesting part of the fix is what decides whether a candidate is an app.
The old code guessed with word lists (`_CONNECTIVES`, `_NOT_AN_APP`). Launch
Services *knows*: `canonical_name()` resolves every installed app and returns
None for everything else. "open the door" is not refused because "door" is on
a list of non-apps; it is refused because no such application exists.
"""

import pytest

from kavach.reasoning.actions import ActionKind, parse


# ═══ politeness ═══

@pytest.mark.parametrize("said", [
    "open notes",
    "Open notes for me.",
    "Open notes please",
    "open notes now",
    "Open Notes, please.",
    "open notes for me please",
    "Open notes. Thanks!",
])
def test_trailing_politeness_does_not_cost_27_seconds(said):
    action = parse(said)
    assert action is not None, f"{said!r} fell through to the model"
    assert action.kind is ActionKind.OPEN
    assert action.app.casefold() == "notes"


# ═══ compounds ═══

@pytest.mark.parametrize("said", [
    "Open google chrome and type youtube.",
    "Open Safari and search Google.",
    "open Chrome and go to youtube",
    "launch Notes then write this down",
])
def test_a_compound_request_is_left_for_the_agent(said):
    """Corrected 2026-08-15. This file originally asserted the opposite —
    that "open X and <something>" should at least open X.

    `test_actions.py::test_a_trailing_clause_is_not_an_app_name` already made
    the counter-argument and it is the right one: opening Chrome and
    answering "Opened Chrome" drops the YouTube half **while reporting
    success**, which is the failure this project cannot have. The agent can
    do both steps; this module can only do one, so it declines and passes the
    whole sentence on.

    That is why "open Google Chrome and search YouTube" now works — not
    because the fast path learned to do it, but because it stopped pretending
    to.
    """
    assert parse(said) is None, said


# ═══ what must still be declined ═══

@pytest.mark.parametrize("said", [
    "open the door",
    "what did I open yesterday and close it",
    "I'm sorry.",
    "open it",
    "close that",
    "open something",
])
def test_it_still_declines_what_is_not_an_app(said):
    """None is the safe answer — the utterance carries on to the model. A
    wrong guess here would run AppleScript against a name nobody meant."""
    assert parse(said) is None, said


def test_an_uninstalled_app_is_refused_with_a_reason_not_silently():
    """Launch Services is the existence check that keeps a mis-transcription
    from reaching a script. It refuses *out loud* rather than falling through
    to the model, which would answer as if it had opened something."""
    from kavach.hands.appinfo import canonical_name
    assert canonical_name("Microsoft Excelsior") is None


@pytest.mark.parametrize("hostile", [
    'open Notes"; do shell script "rm -rf ~',
    "open Notes; rm -rf ~",
])
def test_a_hostile_transcript_never_becomes_an_action(hostile):
    """The name that reaches `tell application "…"` is Launch Services'
    spelling of a real app, or there is no action at all."""
    action = parse(hostile)
    assert action is None or '"' not in action.app


# ═══ the rest of the fast path is unchanged ═══

@pytest.mark.parametrize("said,kind", [
    ("mute", ActionKind.MUTE),
    ("unmute", ActionKind.UNMUTE),
    ("volume up", ActionKind.VOLUME_UP),
    ("turn the volume down", ActionKind.VOLUME_DOWN),
    ("quit Notes", ActionKind.QUIT),
    ("close Safari for me", ActionKind.QUIT),
])
def test_sound_and_quit_still_work(said, kind):
    action = parse(said)
    assert action is not None, said
    assert action.kind is kind


# ═══ the fast path reaches every installed app ═══

def _actions(tmp_path):
    from kavach.killswitch.core import KillSwitch
    from kavach.killswitch.log import ActionLog
    from kavach.hands.allowlist import Allowlist
    from kavach.reasoning.actions import MacActions, ScriptResult

    class FakeRunner:
        def __init__(self):
            self.scripts = []

        def __call__(self, script):
            self.scripts.append(script)
            return ScriptResult(ok=True)

    ks = KillSwitch(log=ActionLog(tmp_path / "actions.jsonl"))
    runner = FakeRunner()
    return MacActions(allowlist=Allowlist(), kill_switch=ks, runner=runner), runner


@pytest.mark.parametrize("said,expected", [
    ("open Terminal", "Terminal"),
    ("open Chrome", "Google Chrome"),
    ("open notes", "Notes"),
])
def test_any_installed_app_opens_without_being_on_a_list(tmp_path, said, expected):
    """`_app_control` resolved names through `Allowlist.canonical_name`, which
    answers None for anything not among the seven — so with the allowlist no
    longer gating, "open Terminal" would still have been refused by the fast
    path. Launch Services answers for every installed app."""
    actions, runner = _actions(tmp_path)
    result = actions.handle(said)

    assert result is not None, f"{said!r} fell through to the model"
    assert result.ok, result.reply
    assert f'tell application "{expected}"' in runner.scripts[-1]


def test_the_script_uses_launch_services_spelling_not_the_transcript(tmp_path):
    """The injection defence, restated for the new resolver: whisper returns
    lowercase and the script gets the real name."""
    actions, runner = _actions(tmp_path)
    actions.handle("open google chrome")

    assert 'tell application "Google Chrome"' in runner.scripts[-1]
