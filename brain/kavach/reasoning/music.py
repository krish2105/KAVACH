"""Spoken music control for Spotify and Apple Music.

Both apps ship AppleScript dictionaries with the same shape, so one vocabulary
drives either — and neither needs an API key, an OAuth flow, or a developer
account. The Spotify Web API would offer catalogue search, at the cost of
credentials living in the project; that trade is not worth it for "skip this".

Handled deterministically, before the model, for the same reason the clock is:
"pause" should never cost a language model call or wait on one.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class MusicAction(str, Enum):
    PLAY = "play"
    PAUSE = "pause"
    NEXT = "next"
    PREVIOUS = "previous"
    NOW_PLAYING = "now_playing"
    PLAY_ITEM = "play_item"
    VOLUME = "volume"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"


class Player(str, Enum):
    SPOTIFY = "Spotify"
    MUSIC = "Music"


@dataclass
class MusicCommand:
    action: MusicAction
    query: str | None = None
    level: int | None = None


#: Order matters: "stop the music" must not be read as a play request because
#: it contains neither "pause" nor a leading "stop".
_PATTERNS: list[tuple[re.Pattern, MusicAction]] = [
    (re.compile(r"\b(what'?s|what is)\s+(playing|this|the song|on)\b"), MusicAction.NOW_PLAYING),
    (re.compile(r"\b(pause|stop)\b"), MusicAction.PAUSE),
    (re.compile(r"\b(skip|next)\b"), MusicAction.NEXT),
    (re.compile(r"\b(previous|go back|last track|last song)\b"), MusicAction.PREVIOUS),
]

#: The minus is matched deliberately. Speech recognition produces "-20" and
#: "minus twenty"; rejecting the sign made the whole command unparseable, so
#: it fell through to the model instead of clamping to zero.
_VOLUME_LEVEL = re.compile(
    r"\bvolume\s+(?:to\s+)?(-?\d{1,3})\b|\bset volume to (-?\d{1,3})\b"
)
_VOLUME_UP = re.compile(r"\b(turn it up|louder|volume up)\b")
_VOLUME_DOWN = re.compile(r"\b(turn it down|quieter|softer|volume down)\b")
_PLAY_ITEM = re.compile(r"\bplay\s+(?:the\s+)?(?:song|track|album|playlist|artist\s+)?(.+)$")
_BARE_PLAY = re.compile(r"^\s*(play|resume|unpause)(\s+(the\s+)?music)?\s*$")

#: Words that mean "music", not a thing to search for.
_NOT_A_QUERY = {"music", "it", "something", "the music", "songs"}


def parse_music_command(said: str | None) -> MusicCommand | None:
    """Recognise a music request, or return None and let the model have it.

    Deliberately conservative: this runs before the model, so anything it
    claims wrongly is answered with a music action instead of an answer.
    """
    if not said or not said.strip():
        return None
    text = said.strip().lower().rstrip("?.!")

    match = _VOLUME_LEVEL.search(text)
    if match:
        level = int(next(g for g in match.groups() if g is not None))
        return MusicCommand(MusicAction.VOLUME, level=level)
    if _VOLUME_UP.search(text):
        return MusicCommand(MusicAction.VOLUME_UP)
    if _VOLUME_DOWN.search(text):
        return MusicCommand(MusicAction.VOLUME_DOWN)

    for pattern, action in _PATTERNS:
        if pattern.search(text):
            return MusicCommand(action)

    if _BARE_PLAY.match(text):
        return MusicCommand(MusicAction.PLAY)

    match = _PLAY_ITEM.search(text)
    if match:
        query = match.group(1).strip()
        if query in _NOT_A_QUERY or not query:
            return MusicCommand(MusicAction.PLAY)
        # Preserve the original casing for the search, not the lowered copy.
        original = said.strip().rstrip("?.!")
        start = original.lower().rfind(query)
        return MusicCommand(MusicAction.PLAY_ITEM,
                            query=original[start:] if start >= 0 else query)

    return None


def _escape(value: str) -> str:
    """Make a transcribed string safe inside an AppleScript literal.

    Track names come from speech recognition and go straight into a script; an
    unescaped quote would close the string and run whatever followed.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_script(command: MusicCommand, player: Player) -> str:
    """The AppleScript for one command against one player."""
    app = player.value

    if command.action is MusicAction.PAUSE:
        body = "pause"
    elif command.action is MusicAction.PLAY:
        body = "play"
    elif command.action is MusicAction.NEXT:
        body = "next track"
    elif command.action is MusicAction.PREVIOUS:
        body = "previous track"
    elif command.action is MusicAction.NOW_PLAYING:
        # Read-only by construction.
        body = (
            'if player state is playing then\n'
            '    return (name of current track) & " by " & (artist of current track)\n'
            'else\n'
            '    return "nothing"\n'
            'end if'
        )
    elif command.action is MusicAction.VOLUME:
        level = max(0, min(100, command.level or 0))
        # The *app's* volume, never the system's — system volume would turn
        # KAVACH's own voice down too, mid-sentence.
        body = f"set sound volume to {level}"
    elif command.action is MusicAction.VOLUME_UP:
        body = "set sound volume to (sound volume + 15)"
    elif command.action is MusicAction.VOLUME_DOWN:
        body = "set sound volume to (sound volume - 15)"
    elif command.action is MusicAction.PLAY_ITEM:
        query = _escape(command.query or "")
        if player is Player.SPOTIFY:
            # Spotify's dictionary cannot search; it plays a URI. Searching
            # needs the Web API, so this is honest about the limit rather than
            # silently playing something else.
            body = f'play track "{query}"'
        else:
            body = (
                f'set results to (every track whose name contains "{query}" '
                f'or album contains "{query}")\n'
                'if results is not {} then\n'
                '    play item 1 of results\n'
                'else\n'
                '    return "not found"\n'
                'end if'
            )
    else:
        body = "return \"unsupported\""

    return f'tell application "{app}"\n{body}\nend tell'


def running_player() -> Player | None:
    """Whichever player is already running, preferring Spotify.

    Starting a music app because someone said "skip this" would be a surprise;
    controlling the one already playing is what was meant.
    """
    for player in (Player.SPOTIFY, Player.MUSIC):
        check = f'tell application "System Events" to (name of processes) contains "{player.value}"'
        try:
            out = subprocess.run(["osascript", "-e", check],
                                 capture_output=True, text=True, timeout=5)
            if out.stdout.strip() == "true":
                return player
        except Exception:
            continue
    return None


def installed(player: Player) -> bool:
    if player is Player.MUSIC:
        return Path("/System/Applications/Music.app").exists()
    return Path("/Applications/Spotify.app").exists()
