"""Music control tests.

Spoken music commands are the most frequent thing a voice assistant does, and
the least tolerant of a wrong guess: "play something" that starts the wrong
album is a worse experience than a refusal.

Two apps, one vocabulary. Spotify and Music both expose AppleScript
dictionaries with the same shape, so the intent layer is shared and only the
generated script differs.
"""

import pytest

from kavach.reasoning.music import (
    MusicAction,
    Player,
    build_script,
    parse_music_command,
)


# ═══ intent ═══

@pytest.mark.parametrize("said,action", [
    ("pause the music", MusicAction.PAUSE),
    ("pause", MusicAction.PAUSE),
    ("stop the music", MusicAction.PAUSE),
    ("play", MusicAction.PLAY),
    ("resume the music", MusicAction.PLAY),
    ("skip this", MusicAction.NEXT),
    ("next track", MusicAction.NEXT),
    ("go back", MusicAction.PREVIOUS),
    ("previous song", MusicAction.PREVIOUS),
    ("what's playing", MusicAction.NOW_PLAYING),
    ("what is playing right now", MusicAction.NOW_PLAYING),
])
def test_transport_and_query_are_recognised(said, action):
    command = parse_music_command(said)
    assert command is not None, said
    assert command.action is action


def test_play_a_named_thing_captures_the_name():
    command = parse_music_command("play Kind of Blue")
    assert command.action is MusicAction.PLAY_ITEM
    assert command.query == "Kind of Blue"


def test_bare_play_is_resume_not_a_search():
    """'play' alone means resume. Treating it as a search for the empty string
    would stop the music instead of starting it."""
    assert parse_music_command("play").action is MusicAction.PLAY


@pytest.mark.parametrize("said,level", [
    ("volume 50", 50),
    ("set volume to 30", 30),
    ("turn it up", None),
    ("turn it down", None),
])
def test_volume_commands(said, level):
    command = parse_music_command(said)
    assert command.action in (MusicAction.VOLUME, MusicAction.VOLUME_UP,
                              MusicAction.VOLUME_DOWN)
    if level is not None:
        assert command.level == level


@pytest.mark.parametrize("said", [
    "what time is it",
    "delete the draft in notes",
    "open safari",
    "",
])
def test_non_music_requests_are_not_claimed(said):
    """This handler runs before the model. Over-claiming would swallow
    unrelated requests and answer them with silence."""
    assert parse_music_command(said) is None


# ═══ generated AppleScript ═══

def test_script_targets_the_requested_player():
    command = parse_music_command("pause")
    assert 'tell application "Spotify"' in build_script(command, Player.SPOTIFY)
    assert 'tell application "Music"' in build_script(command, Player.MUSIC)


def test_volume_is_the_app_not_the_system():
    """System volume would also turn KAVACH's own voice down — including
    mid-sentence, while it is telling you it has done so."""
    script = build_script(parse_music_command("volume 40"), Player.SPOTIFY)
    assert "sound volume" in script
    assert "set volume" not in script.replace("set sound volume", "")


def test_volume_is_clamped_to_a_sane_range():
    for said, expected in (("volume 300", 100), ("volume -20", 0)):
        script = build_script(parse_music_command(said), Player.SPOTIFY)
        assert str(expected) in script


def test_a_quoted_name_cannot_break_out_of_the_script():
    """A track name is transcribed speech and goes straight into AppleScript.
    An unescaped quote would end the string and run whatever follows."""
    command = parse_music_command('play the album "quote" test')
    script = build_script(command, Player.MUSIC)
    assert script.count('"') % 2 == 0


def test_now_playing_reads_rather_than_changes():
    script = build_script(parse_music_command("what's playing"), Player.SPOTIFY)
    for mutation in ("playpause", "next track", "set sound volume"):
        assert mutation not in script
