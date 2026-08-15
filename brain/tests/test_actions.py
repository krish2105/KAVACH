"""Local macOS actions — the second thing in KAVACH that acts (spec §5, §7).

Measured, through the running system, before this existed:

    POST /command  "open Notes"
    → route=claude · "needs tools to act (app control)"
    → Notes opened, via an MCP server, a subprocess and a model call

That works, and it is the wrong price for `open Notes`. The router escalates
these because a 3B chat model with no tools would otherwise *narrate* having
done it — a real bug, fixed by escalating. But escalation is not the only
answer: code can open an app in ~50ms, offline, and be *checked* while doing it.

So this module executes the three intent families the user named — app
control, system sound, and (already, in music.py) media — directly, through the
same guardrails an MCP tool call passes:

    kill switch  →  allowlist  →  osascript  →  action log

Most of these tests are refusals, for the same reason `test_appcontrol.py`'s
are: each gate is an independent way for a transcript to reach something it
should not, and one "it works" test would pass with any three of the four in
place.

**Nothing here opens an app or moves your volume.** The runner is faked
everywhere except section 8, which drives the real `osascript` on scripts that
change nothing — otherwise the one layer that actually runs things would be the
only untested part of the module.
"""

import json

import pytest

from kavach.hands.allowlist import Allowlist
from kavach.killswitch.core import KillSwitch
from kavach.killswitch.log import ActionLog
from kavach.reasoning.actions import (
    ActionKind,
    MacActions,
    OsascriptRunner,
    ScriptResult,
    parse,
)


# ═══ fixtures ═══

class FakeRunner:
    """Stands in for osascript. Records rather than acts."""

    def __init__(self, ok: bool = True, out: str = "", err: str = "boom"):
        self.scripts: list[str] = []
        self.ok = ok
        self.out = out
        self.err = err

    def __call__(self, script: str) -> ScriptResult:
        self.scripts.append(script)
        return ScriptResult(ok=self.ok, out=self.out, err="" if self.ok else self.err)


class FakeVoiceprint:
    def __init__(self, gating: bool = True):
        self.gating = gating


ALLOWLIST_FILE = {
    "version": 2,
    "devices": {
        "mac": {
            "enabled": True,
            "allowed": [
                {"name": "Safari", "bundle_id": "com.apple.Safari"},
                {"name": "Notes", "bundle_id": "com.apple.Notes"},
            ],
        },
        "iphone": {"enabled": True, "read_only_tools": ["screenshot"]},
    },
    "confirm_always": ["delete", "send"],
}


@pytest.fixture
def allowlist_path(tmp_path):
    path = tmp_path / "allowlist.json"
    path.write_text(json.dumps(ALLOWLIST_FILE, indent=2))
    return path


@pytest.fixture
def allowlist(allowlist_path):
    return Allowlist(allowlist_path)


@pytest.fixture
def log(tmp_path):
    return ActionLog(tmp_path / "actions.jsonl")


@pytest.fixture
def kill_switch(log):
    return KillSwitch(log=log)


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def actions(allowlist, kill_switch, runner):
    return MacActions(
        allowlist=allowlist,
        kill_switch=kill_switch,
        runner=runner,
        voiceprint=FakeVoiceprint(gating=True),
    )


# ═══ 1. understanding — conservative, because this runs before the model ═══

@pytest.mark.parametrize("said,kind,app", [
    ("open Notes", ActionKind.OPEN, "Notes"),
    ("open the Notes app", ActionKind.OPEN, "Notes"),
    ("launch Safari", ActionKind.OPEN, "Safari"),
    ("start Google Chrome", ActionKind.OPEN, "Google Chrome"),
    ("quit Music", ActionKind.QUIT, "Music"),
    ("close Finder", ActionKind.QUIT, "Finder"),
])
def test_app_commands_are_recognised(said, kind, app):
    action = parse(said)
    assert action is not None, said
    assert action.kind is kind
    assert action.app == app


@pytest.mark.parametrize("said,kind,level", [
    ("mute", ActionKind.MUTE, None),
    ("mute the sound", ActionKind.MUTE, None),
    ("unmute", ActionKind.UNMUTE, None),
    ("volume up", ActionKind.VOLUME_UP, None),
    ("turn the volume down", ActionKind.VOLUME_DOWN, None),
    ("set the volume to 30", ActionKind.VOLUME_SET, 30),
    ("volume 40", ActionKind.VOLUME_SET, 40),
])
def test_sound_commands_are_recognised(said, kind, level):
    action = parse(said)
    assert action is not None, said
    assert action.kind is kind
    assert action.level == level


@pytest.mark.parametrize("said", [
    "what time is it",
    "delete the note called KAVACH scratch",
    "summarise my last three notes",
    "hello",
    "",
    None,
    # Not an app — a request to act on something inside one.
    "close that",
    "open it",
])
def test_non_actions_are_not_claimed(said):
    """This runs before the model. Over-claiming answers an unrelated request
    with an app launch — the same over-reach `parse_music_command` avoids."""
    assert parse(said) is None


def test_a_trailing_clause_is_not_an_app_name():
    """"open Safari and search for flights" is a multi-step request. Parsing it
    as `open "Safari and search for flights"` would swallow the second half and
    silently drop it; leaving it alone sends the whole thing to the agent."""
    assert parse("open Safari and search for flights") is None


def test_a_name_that_could_escape_the_script_is_not_parsed():
    """App names come from speech recognition and end up inside an AppleScript
    string literal. The charset is the defence: a quote or a backslash makes it
    *not an app name*, rather than something to escape and run anyway."""
    for said in ['open Notes" and do shell script "rm -rf ~',
                 "open Notes\\", "open Notes; rm -rf /"]:
        assert parse(said) is None, said


# ═══ 2. the kill switch (§C — guard gates every action path) ═══

def test_a_latched_kill_switch_runs_nothing(actions, kill_switch, runner):
    kill_switch.trigger(source="test", reason="latched before the action")

    result = actions.handle("open Notes")

    assert result is not None
    assert result.ok is False
    assert runner.scripts == [], "a latched kill switch still ran a script"
    assert "kill switch" in result.reply.lower()


def test_a_blocked_action_is_recorded(actions, kill_switch, log):
    """After a kill, what the agent *tried* to do next is exactly what you want
    to be able to read back."""
    kill_switch.trigger(source="test", reason="")
    actions.handle("open Notes")

    assert any(r["event"] == "killswitch.blocked" for r in log.read_all())


# ═══ 3. the allowlist (§7 — an app nobody approved is denied) ═══

def test_an_allowlisted_app_is_opened(actions, runner):
    result = actions.handle("open Notes")

    assert result.ok is True
    assert len(runner.scripts) == 1
    assert 'tell application "Notes"' in runner.scripts[0]
    assert "Notes" in result.reply




def test_the_name_in_the_script_is_the_allowlisted_spelling(actions, runner):
    """The transcript never reaches AppleScript.

    `open notes` is matched case-insensitively against the allowlist, and the
    string that goes into the script is the *file's* spelling. Injection is
    therefore impossible by construction rather than by escaping: anything that
    is not already an approved name never gets as far as a script.
    """
    actions.handle("open notes")

    assert 'tell application "Notes"' in runner.scripts[0]
    assert "notes" not in runner.scripts[0].replace("Notes", "")


# ═══ 4. widening the allowlist by voice (§7 — asked for, and gated) ═══













# ═══ 5. honesty — the failure this project cannot have ═══

def test_a_failed_script_is_never_reported_as_success(actions):
    actions.runner.ok = False

    result = actions.handle("open Notes")

    assert result.ok is False
    assert "opened" not in result.reply.lower()
    assert "notes is now open" not in result.reply.lower()


def test_a_recognised_action_always_answers(actions):
    """The load-bearing property.

    In `VoiceLoop.respond`, anything this returns None for falls through to the
    local 3B model — which has no hands and will describe having done it. So
    once an action is *recognised*, this must answer it, even to say it failed.
    """
    actions.runner.ok = False
    for said in ["open Notes", "open Mail", "mute", "volume up",
                 "set the volume to 30"]:
        result = actions.handle(said)
        assert result is not None, said
        assert result.reply.strip(), said


def test_an_unrecognised_utterance_is_declined_so_it_can_escalate(actions):
    """The other half: this must not claim what it cannot do."""
    assert actions.handle("summarise my notes") is None


# ═══ 6. the action log (§7 — every call, every argument) ═══

def test_every_action_is_logged_with_its_arguments(actions, log):
    actions.handle("open Notes")
    actions.handle("mute")
    actions.handle("set the volume to 30")

    events = {r["event"]: r for r in log.read_all()}
    assert events["action.app_open"]["app"] == "Notes"
    assert events["action.mute"]["muted"] is True
    assert events["action.volume"]["level"] == 30




# ═══ 7. system sound ═══

def test_mute_targets_the_system_output(actions, runner):
    actions.handle("mute")
    assert "output muted" in runner.scripts[0]
    assert "true" in runner.scripts[0]


def test_volume_is_clamped_to_a_sane_range(actions, runner):
    for said, expected in (("set the volume to 300", 100),
                           ("set the volume to -20", 0)):
        runner.scripts.clear()
        actions.handle(said)
        assert f"output volume {expected}" in runner.scripts[0]


# ═══ 8. the runner, against the real osascript ═══
#
# Everything above fakes the runner so the suite never touches your Mac. These
# two use the real one, on scripts that change nothing — otherwise the layer
# that actually runs things would be the only untested part of the module.

def test_the_runner_reports_a_real_result():
    result = OsascriptRunner()("return \"hello\"")
    assert result.ok is True
    assert result.out == "hello"


def test_the_runner_reports_a_real_failure():
    result = OsascriptRunner()('id of app "Definitely Not An App"')
    assert result.ok is False
    assert result.err, "a failure with no message is impossible to debug"


def test_a_timeout_is_a_failure_not_a_success():
    """"Did it work?" cannot be answered by "we stopped waiting". An action
    that hangs must read as failed, or KAVACH says it opened something it
    never waited to see open."""
    result = OsascriptRunner(timeout=0.3)("delay 5")

    assert result.ok is False
    assert "timed out" in result.err


def test_volume_needs_no_allowlist_entry(actions):
    """Volume and mute are the system, not an app. Requiring an allowlisted app
    for them would be a check that means nothing."""
    assert actions.handle("mute").ok is True
    assert actions.handle("volume up").ok is True


# ═══ 9. inside the turn ═══
#
# The gates above are worth nothing if `respond()` does not consult them, and
# the failure mode is specific: anything `handle()` declines falls through to
# the local 3B model, which has no hands and will describe having acted. These
# drive the real `VoiceLoop.respond` — the method under test, not a copy.

@pytest.fixture
def loop(allowlist, kill_switch, actions):
    from kavach.api.confirm import PendingRegistry
    from tests.test_api import make_loop

    registry = PendingRegistry()
    loop = make_loop(registry, kill_switch)
    loop.actions = actions
    loop.registry = registry
    return loop


def test_an_app_command_never_reaches_the_model(loop):
    """The whole point. `open Notes` is answered by code that opened Notes."""
    reply = loop.respond("open Notes")

    assert loop.local.ran == [], \
        "the model with no hands was asked to open an app"
    assert "Notes" in reply
    assert loop.actions.runner.scripts, "nothing actually ran"


def test_the_hud_is_told_which_route_acted(loop):
    """§13. The router said 'stub'; a local action answered. Showing the
    router's guess beside an action it did not take is the same class of
    mistake as showing a stale reason."""
    loop.respond("open Notes")

    assert loop.state.route == "action"
    assert "no model" in loop.state.reason








def test_what_actions_decline_still_reaches_the_model(loop):
    """The other half of the seam: this must not swallow ordinary requests."""
    loop.respond("tell me about the Notes app")

    assert loop.local.ran == ["tell me about the Notes app"]


# ═══ 8. an app that is not installed (spec §9a) ═══
#
# This section replaces the "widen the allowlist by voice" tests. That flow
# existed to add an app to `hands/allowlist.json` after a spoken confirmation,
# with speaker verification required because the allowlist was a permission
# boundary. It is not one any more: every installed app is reachable, so
# there is nothing to widen and nothing to protect from widening.
#
# What is left is the case Launch Services cannot resolve — a mis-transcribed
# name, or an app that genuinely is not on this Mac. Adding it to a file would
# not make it exist.

def test_an_app_that_is_not_installed_is_refused_out_loud(actions):
    """Not None. Returning None sends the request on to the local model —
    the tool-less narrator that answers "Opened it" without opening
    anything, which is the failure this whole module exists to prevent."""
    result = actions.handle("open Nonesuch")

    assert result is not None, "an app request fell through to the model"
    assert result.ok is False
    assert "Nonesuch" in result.reply


def test_the_refusal_says_it_cannot_find_it_not_that_it_is_forbidden(actions):
    """The reason has to be true. "Nonesuch isn't on your allowlist" would be
    a lie about why, and would send the user looking for a setting to change."""
    reply = actions.handle("open Nonesuch").reply.lower()

    assert "can't find" in reply or "cannot find" in reply
    assert "allowlist" not in reply


def test_a_refusal_is_still_logged(actions, log):
    """§7 does not stop applying because the answer was no."""
    actions.handle("open Nonesuch")

    refusals = [e for e in log.read_all() if e["event"] == "action.refused"]
    assert refusals, "a refused action left no record"
    assert refusals[0]["app"] == "Nonesuch"
    assert refusals[0]["reason"] == "not installed"


def test_nothing_is_written_to_the_allowlist_by_speaking(actions, allowlist):
    """The voice-add path is gone. `hands/allowlist.json` is not modified by
    anything the user says — a file that no longer gates should also no
    longer grow."""
    before = allowlist.path.read_text()
    actions.handle("open Nonesuch", confirmed=True)
    actions.handle("open Terminal")

    assert allowlist.path.read_text() == before
