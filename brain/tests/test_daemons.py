"""The launch agents — what has to be running for the orb to be visible.

Written after the panel showed *nothing* and the reason turned out to be that
nobody was serving the page. The overlay agent faithfully opened a window at
login; the Next.js server behind `http://127.0.0.1:3100` only ever existed
because someone typed `next start` in a terminal. When that terminal went away
the panel became an empty transparent window — no error, no log line, nothing
to click, and no way to tell it apart from a crash.

`doctor.py` had known for a while: its remedy text is literally "start `npx
next start -p 3100` in apps/orb". A permanent instruction to the human is a
missing daemon.

**The obvious fix — an agent that runs `next start` — cannot work here**, and
that is worth recording so nobody adds it back. The project lives under
~/Desktop, which is TCC-protected, and a launchd job has no grant for it:

    $ launchctl bootstrap gui/$UID <a job that cats one file>
    DESKTOP DENIED
    HOME READABLE

Node does not fail on that denial, it hangs — sampled after two minutes, 100%
of samples were blocked in a single open() while walking up parents looking for
package.json. Port never bound, zero bytes logged, launchd cheerfully reporting
the job as running. The page server therefore lives in the overlay process,
which is the app bundle and already reads the project — see
`kavach/presence/pageserver.py` and `tests/test_pageserver.py`.

These tests do not start anything. They assert the *contract* between the agent
and the process that draws the window, because every failure mode here is
silent at runtime.
"""

import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

from kavach import daemons

DAEMONS = Path(__file__).resolve().parents[2] / "daemon"
OVERLAY_PLIST = DAEMONS / "com.krishna.kavach.overlay.plist"



def load(path: Path) -> dict:
    """The template as the installer renders it.

    Reading the raw file would test the placeholders rather than the paths
    launchd is actually handed — and it was precisely that gap, between the
    template in git and the hand-edited copy in ~/Library/LaunchAgents, that
    left the machine launching an overlay with no camera.
    """
    return plistlib.loads(daemons.render(path.stem).encode())


def argv(plist: dict) -> list[str]:
    return [str(a) for a in plist.get("ProgramArguments", [])]


# ═══ the overlay has to be the bundle, or the camera is dead ═══

#: Folders macOS gates behind a TCC grant. A launchd job has none of them.
PROTECTED = ("Desktop", "Documents", "Downloads")


def test_the_overlay_agent_can_read_this_project():
    """The agent must launch something that is *allowed* to read the source.

    This replaces a test that asserted the opposite — that the agent should
    launch KAVACH.app, because the bundle is what macOS will grant a camera to.
    That is true, and it is the wrong trade here, which only a measurement
    showed:

        launchctl bootstrap gui/$UID <a job that cats one file>
        → DESKTOP DENIED / HOME READABLE

    This project sits under ~/Desktop. Pointed at the bundle, the agent
    produced a live process that logged nothing at all; sampling it put 100% of
    samples in `_PyCodecRegistry_Init → os_listdir → open$NOCANCEL` — Python
    hung on its first import, listing a protected directory on sys.path. A
    `next start` agent died at the identical wall.

    `uv run kavach-overlay` holds the grant on this machine and gets the camera
    too: the agent-started process logs live `pinch ENGAGED` lines.

    So the rule is not "prefer the bundle" but "do not put a launchd job behind
    a folder it cannot open" — because that failure mode is a silent hang, and
    a hang is worse than the refusal it was meant to fix.
    """
    args = argv(load(OVERLAY_PLIST))
    project = Path(__file__).resolve().parents[2]
    in_protected = any(part in PROTECTED for part in project.parts)

    if in_protected and any(a == "/usr/bin/open" for a in args):
        pytest.fail(
            f"the agent launches the app bundle while the project lives in "
            f"{project} — a protected folder. launchd cannot read it and the "
            f"process hangs silently. Grant KAVACH.app Full Disk Access first, "
            f"or keep `uv run kavach-overlay`."
        )


def test_the_overlay_agent_points_at_a_bundle_that_exists():
    args = argv(load(OVERLAY_PLIST))
    app = next((Path(a).expanduser() for a in args if a.endswith(".app")), None)

    if app is None:
        pytest.skip("this agent launches the CLI, not the bundle — see "
                    "test_the_overlay_agent_can_read_this_project")
    if not app.exists():
        pytest.skip(f"{app} not built on this machine — run `uv run kavach-app`")

    assert (app / "Contents" / "MacOS").is_dir(), f"{app} is not a bundle"


@pytest.mark.skipif(shutil.which("codesign") is None, reason="no codesign")
def test_the_bundle_the_agent_launches_still_verifies():
    """TCC refuses a bundle whose signature does not validate — without
    showing a prompt. Same 100ms refusal, same broken-hardware look."""
    args = argv(load(OVERLAY_PLIST))
    app = next((Path(a).expanduser() for a in args if a.endswith(".app")), None)
    if app is None or not app.exists():
        pytest.skip("bundle not built on this machine")

    done = subprocess.run(["codesign", "--verify", "--strict", str(app)],
                          capture_output=True, text=True)

    assert done.returncode == 0, \
        f"the bundle does not verify, so macOS will refuse it:\n{done.stderr}"


# ═══ the two must not fight over the same lock ═══

def test_only_one_thing_launches_the_overlay():
    """Every duplicate-panel bug in this project came from two launchers.

    `InstanceLock` makes the loser exit 1, so the visible symptom is not two
    panels but a panel that vanishes — and with KeepAlive, one that vanishes
    and returns on a ten-second cycle.
    """
    launchers = [p for p in DAEMONS.glob("*.plist")
                 if "kavach-overlay" in " ".join(argv(load(p)))
                 or "KAVACH.app" in " ".join(argv(load(p)))]

    assert len(launchers) == 1, \
        f"{len(launchers)} agents launch the overlay: " \
        f"{[p.name for p in launchers]}"


def test_no_agent_launches_through_uv():
    """`uv run` costs the hotkeys, and the loss is silent.

    TCC attributes to the *responsible* process. With `uv run kavach-overlay`
    the job launchd starts is uv, so the Input Monitoring grant the user gave
    python3.12 does not apply, and every global hotkey silently does nothing —
    `hotkeys BLOCKED` in the log while System Settings shows the toggle on.

    Measured from inside a launchd job, which is the only place the difference
    shows:

        uv run python      → CGPreflightListenEventAccess() False
        venv python direct → CGPreflightListenEventAccess() True

    And the venv interpreter reads the Desktop project perfectly well on its
    own, so nothing is traded away for it — the earlier `open -a KAVACH.app`
    attempt failed on exactly that and this does not.
    """
    for plist in DAEMONS.glob("*.plist"):
        args = argv(load(plist))
        assert not any(a.endswith("/uv") for a in args), (
            f"{plist.name} runs through uv, so every TCC grant is checked "
            f"against uv instead of python — the microphone included"
        )

    assert any("kavach-overlay" in a for a in argv(load(OVERLAY_PLIST))), \
        "the agent no longer launches the overlay at all"
