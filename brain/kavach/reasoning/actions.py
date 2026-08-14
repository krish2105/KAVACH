"""Local macOS actions — app control and system sound (spec §5, §7).

The router sends `open Notes` to the Claude route, and that was a real fix: a
3B chat model with no tools does not decline, it *narrates* having opened it.
Measured, before that fix:

    POST /command  "open Notes"
    → route=local · "simple intent (app control)"
    → reply: "Notes are now open."
    → Notes was not open.

Escalating solved the honesty problem at the price of a model call, an MCP
server and a subprocess for something code can do in ~50ms, offline. This
module is the third answer: **execute it here, through the same guardrails an
MCP tool call passes.**

    kill switch  →  allowlist  →  osascript  →  action log

Two properties are load-bearing, and both are about what happens when
something goes wrong:

* **Once an action is recognised, it is answered** — even to say it failed.
  :meth:`MacActions.handle` returning None sends the utterance onward to the
  local model, which is the tool-less narrator this whole module exists to keep
  away from action requests.
* **The transcript never reaches AppleScript.** The name in the script is the
  *allowlist's* spelling, resolved by :meth:`Allowlist.canonical_name`. A name
  that is not already approved cannot reach a script at all, so injection is
  ruled out by construction rather than by escaping.

Media control is deliberately absent: `music.py` already does it locally, for
Spotify and Apple Music, with an app-scoped volume that does not turn KAVACH's
own voice down mid-sentence. Two paths for one intent is how one of them goes
stale.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from enum import Enum

from ..killswitch.core import KillSwitchDisarmed

log = logging.getLogger("kavach.reasoning.actions")


class ActionKind(str, Enum):
    OPEN = "open"
    QUIT = "quit"
    MUTE = "mute"
    UNMUTE = "unmute"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    VOLUME_SET = "volume_set"


#: The kinds that name an app, and so must pass the allowlist.
_APP_KINDS = frozenset({ActionKind.OPEN, ActionKind.QUIT})

#: How far one "volume up" moves the system volume.
VOLUME_STEP = 12


@dataclass
class Action:
    kind: ActionKind
    app: str | None = None
    level: int | None = None


@dataclass
class ActionResult:
    """What to say, and whether anything actually happened."""

    reply: str
    ok: bool
    #: True when `reply` is a question rather than an answer — nothing has run
    #: and nothing will until it is confirmed.
    needs_confirmation: bool = False


@dataclass
class ScriptResult:
    ok: bool
    out: str = ""
    err: str = ""


# ═══ running AppleScript ═══

class OsascriptRunner:
    """Runs one AppleScript and reports what happened.

    Injected rather than called directly so the test suite can exercise every
    gate without opening apps or moving your system volume — the same shape as
    `QuartzPoster`/`FakePoster` in `gestures/appcontrol.py`.

    Every failure mode returns ``ok=False``. A timeout in particular must never
    read as success: "did it work?" cannot be answered by "we stopped waiting".
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def __call__(self, script: str) -> ScriptResult:
        try:
            proc = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            log.warning("osascript timed out after %.1fs", self.timeout)
            return ScriptResult(ok=False, err=f"timed out after {self.timeout}s")
        except Exception as exc:
            log.exception("osascript failed to start")
            return ScriptResult(ok=False, err=repr(exc))

        return ScriptResult(
            ok=proc.returncode == 0,
            out=(proc.stdout or "").strip(),
            err=(proc.stderr or "").strip(),
        )


# ═══ understanding ═══
#
# Conservative on purpose: this runs *before* the model, so anything it claims
# wrongly is answered with an app launch instead of an answer.

#: An app name is letters, digits, and the punctuation real app names contain.
#: A quote, a backslash or a semicolon therefore makes a string **not an app
#: name** — it is rejected here rather than escaped and run, which is why
#: nothing downstream has to think about AppleScript quoting.
#:
#: Lazy, so that the optional " app" suffix below wins over the name: greedy
#: matching swallowed it and asked the allowlist about "Notes app".
_APP_NAME = r"(?P<app>[A-Za-z][A-Za-z0-9 .&'\-]{0,38}?)"

_OPEN_RE = re.compile(
    rf"\b(?:open|launch|start)\s+(?:up\s+)?(?:the\s+)?{_APP_NAME}"
    rf"(?:\s+app)?\s*[.!?]?\s*$", re.I,
)
_QUIT_RE = re.compile(
    rf"\b(?:quit|close|exit)\s+(?:the\s+)?{_APP_NAME}"
    rf"(?:\s+app)?\s*[.!?]?\s*$", re.I,
)

#: Words that mean the sentence carried on. "open Safari and search for
#: flights" is a two-step request; parsing it as an app called
#: "Safari and search for flights" would silently drop the half that mattered.
_CONNECTIVES = {"and", "then", "or", "but", "with", "for", "to", "in", "on",
                "after", "before", "while", "so", "plus"}

#: Not apps. "close that" is about a window, "open it" about whatever you were
#: just looking at — both need the judgement this module does not have.
_NOT_AN_APP = {"it", "that", "this", "them", "these", "those", "everything",
               "all", "window", "windows", "tab", "tabs", "file", "folder",
               "door", "up", "down", "one", "something", "anything"}

#: At most three words: "Visual Studio Code" is an app, a clause is not.
_MAX_APP_WORDS = 3

_UNMUTE_RE = re.compile(r"\bun-?mute\b", re.I)
_MUTE_RE = re.compile(r"\bmute\b", re.I)
_VOLUME_SET_RE = re.compile(
    r"\b(?:set\s+)?(?:the\s+)?(?:system\s+)?volume\s+(?:to\s+|at\s+)?(-?\d{1,3})\b",
    re.I,
)
_VOLUME_UP_RE = re.compile(
    r"\b(?:volume|sound)\s+up\b|\bturn\s+(?:the\s+)?(?:volume|sound)\s+up\b"
    r"|\blouder\b", re.I,
)
_VOLUME_DOWN_RE = re.compile(
    r"\b(?:volume|sound)\s+down\b|\bturn\s+(?:the\s+)?(?:volume|sound)\s+down\b"
    r"|\bquieter\b", re.I,
)


def _app_from(match: re.Match | None) -> str | None:
    if match is None:
        return None
    name = " ".join(match.group("app").split())
    words = name.split()
    if len(words) > _MAX_APP_WORDS:
        return None
    lowered = {w.casefold() for w in words}
    if lowered & _CONNECTIVES or name.casefold() in _NOT_AN_APP:
        return None
    return name


def parse(said: str | None) -> Action | None:
    """Recognise a local action, or return None and let the request move on.

    None is the safe answer: it means "not mine", and the utterance carries on
    to search, to the local model, or to the agent with its tools.
    """
    if not said or not said.strip():
        return None
    text = said.strip()

    # Sound first — "unmute" contains "mute", and a volume *level* is a
    # different request from a volume *direction*.
    if _UNMUTE_RE.search(text):
        return Action(ActionKind.UNMUTE)
    if _MUTE_RE.search(text):
        return Action(ActionKind.MUTE)

    level = _VOLUME_SET_RE.search(text)
    if level:
        return Action(ActionKind.VOLUME_SET, level=int(level.group(1)))
    if _VOLUME_UP_RE.search(text):
        return Action(ActionKind.VOLUME_UP)
    if _VOLUME_DOWN_RE.search(text):
        return Action(ActionKind.VOLUME_DOWN)

    app = _app_from(_OPEN_RE.search(text))
    if app:
        return Action(ActionKind.OPEN, app=app)
    app = _app_from(_QUIT_RE.search(text))
    if app:
        return Action(ActionKind.QUIT, app=app)

    return None


# ═══ doing ═══

class MacActions:
    """Executes a parsed action, or refuses and says why.

    Every path through here ends in an :class:`ActionResult`, and the four
    steps run in this order for all of them: kill switch, allowlist, script,
    log. Order matters — the allowlist check is worth nothing if the script has
    already run, and the log is worth nothing if it records intentions rather
    than outcomes, so `ok` is read off the script's exit status.
    """

    def __init__(self, allowlist, kill_switch, runner=None, voiceprint=None,
                 timeout: float = 10.0) -> None:
        self.allowlist = allowlist
        self.ks = kill_switch
        self.runner = runner if runner is not None else OsascriptRunner(timeout)
        #: Widening the allowlist by voice requires this to be gating — see
        #: `_add_then_run`. None means no verification, which means no adding.
        self.voiceprint = voiceprint

    @property
    def log(self):
        return self.ks.log

    def handle(self, said: str, confirmed: bool = False) -> ActionResult | None:
        """Act on an utterance, or None if it is not a local action."""
        action = parse(said)
        if action is None:
            return None
        return self.run(action, confirmed=confirmed)

    def run(self, action: Action, confirmed: bool = False) -> ActionResult:
        try:
            self.ks.guard(f"action.{action.kind.value}")
        except KillSwitchDisarmed:
            # Refused, not swallowed: nothing runs, the attempt is already in
            # the log (guard writes `killswitch.blocked`), and the reply says
            # so rather than pretending the request was never made.
            return ActionResult(
                "The kill switch is latched, so I'm not doing anything.",
                ok=False,
            )

        if action.kind in _APP_KINDS:
            return self._app_control(action, confirmed=confirmed)
        return self._system_control(action)

    # ——— apps ———

    def _app_control(self, action: Action, confirmed: bool) -> ActionResult:
        name = self.allowlist.canonical_name(action.app)
        if name is None:
            return self._not_allowed(action, confirmed=confirmed)

        opening = action.kind is ActionKind.OPEN
        # `name` is the allowlist's spelling, not the transcript's — see the
        # module docstring. Nothing user-supplied reaches this string.
        body = "activate" if opening else "quit"
        result = self.runner(f'tell application "{name}" to {body}')

        self.log.append(
            "action.app_open" if opening else "action.app_quit",
            app=name, requested=action.app, ok=result.ok,
            error=result.err or None,
        )

        if not result.ok:
            log.warning("could not %s %s: %s", body, name, result.err)
            return ActionResult(f"I couldn't {body} {name}.", ok=False)
        return ActionResult(f"{'Opened' if opening else 'Quit'} {name}.", ok=True)

    def _not_allowed(self, action: Action, confirmed: bool) -> ActionResult:
        """An app nobody has approved. Ask, then add — never act quietly.

        The user chose "ask to add it" over a flat refusal, which means the
        allowlist is now widenable by voice. Three things keep §7 intact:
        the existing confirmation machinery does the asking (no second consent
        path), speaker verification must be on, and the entry records who asked
        and when.
        """
        app = action.app
        verb = "open" if action.kind is ActionKind.OPEN else "quit"

        if not confirmed:
            self.log.append("action.refused", app=app, kind=action.kind.value,
                            reason="not on the allowlist")
            return ActionResult(
                f"{app} isn't on your allowlist. "
                f"Say confirm and I'll add it and {verb} it.",
                ok=False, needs_confirmation=True,
            )

        return self._add_then_run(action, verb=verb)

    def _add_then_run(self, action: Action, verb: str) -> ActionResult:
        app = action.app

        # Speaker verification is load-bearing here in a way it is not
        # elsewhere: without it the allowlist can be widened by anything that
        # can produce speech in the room — a video, a colleague, a smart
        # speaker. The §7 confirmation proves *someone* answered; this is what
        # proves it was you.
        if self.voiceprint is None or not getattr(self.voiceprint, "gating", False):
            self.log.append("allowlist.add_refused", app=app,
                            reason="speaker verification is not gating")
            return ActionResult(
                "Adding an app to the allowlist needs speaker verification on. "
                "Turn it on with kavach-speaker on, then ask me again.",
                ok=False,
            )

        # The bundle id is the app's real identity, and looking it up is also
        # an existence check: a mis-transcribed name must not leave a permanent
        # grant behind for an app that does not exist.
        lookup = self.runner(f'id of app "{app}"')
        if not lookup.ok or not lookup.out:
            self.log.append("allowlist.add_refused", app=app,
                            reason="no such app on this Mac")
            return ActionResult(f"I couldn't find an app called {app}.", ok=False)

        reason = f"added by voice, {date.today().isoformat()}"
        try:
            # The stored name is the spoken one; the bundle id, which is what
            # actually identifies the app, comes from the system. AppleScript
            # and `is_allowed` both match names case-insensitively, so the
            # spelling is cosmetic and the identifier is not.
            entry = self.allowlist.add(app, lookup.out, reason=reason)
        except (ValueError, OSError) as exc:
            log.exception("could not write the allowlist")
            self.log.append("allowlist.add_refused", app=app, reason=repr(exc))
            return ActionResult(f"I couldn't add {app} to the allowlist.", ok=False)

        self.log.append("allowlist.add", app=entry["name"],
                        bundle_id=entry["bundle_id"], reason=reason,
                        via="voice")
        log.info("allowlist widened by voice: %s (%s)",
                 entry["name"], entry["bundle_id"])

        return self._app_control(action, confirmed=True)

    # ——— system sound ———

    def _system_control(self, action: Action) -> ActionResult:
        if action.kind in (ActionKind.MUTE, ActionKind.UNMUTE):
            return self._mute(action.kind is ActionKind.MUTE)
        return self._volume(action)

    def _mute(self, muted: bool) -> ActionResult:
        result = self.runner(f"set volume output muted {str(muted).lower()}")
        self.log.append("action.mute", muted=muted, ok=result.ok,
                        error=result.err or None)
        if not result.ok:
            return ActionResult("I couldn't change the sound.", ok=False)
        # Worth knowing: after a mute, this reply is spoken into a muted
        # output. It is still returned, because the orb shows it and the log
        # records it — the acknowledgement is not the action.
        return ActionResult("Muted." if muted else "Unmuted.", ok=True)

    def _volume(self, action: Action) -> ActionResult:
        if action.kind is ActionKind.VOLUME_SET:
            level = max(0, min(100, action.level or 0))
            script = f"set volume output volume {level}\nreturn {level}"
        else:
            step = VOLUME_STEP if action.kind is ActionKind.VOLUME_UP else -VOLUME_STEP
            level = None
            # Read, clamp and set in one script: AppleScript errors on a level
            # outside 0–100, and a second round trip to find out where we
            # started would double the cost of "volume up".
            script = (
                f"set v to (output volume of (get volume settings)) + {step}\n"
                "if v > 100 then set v to 100\n"
                "if v < 0 then set v to 0\n"
                "set volume output volume v\n"
                "return v"
            )

        result = self.runner(script)
        if result.ok and level is None:
            try:
                level = int(result.out)
            except (TypeError, ValueError):
                level = None

        self.log.append("action.volume", level=level, kind=action.kind.value,
                        ok=result.ok, error=result.err or None)

        if not result.ok:
            return ActionResult("I couldn't change the volume.", ok=False)
        if level is None:
            return ActionResult("Done.", ok=True)
        return ActionResult(f"Volume {level}.", ok=True)
