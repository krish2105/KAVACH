"""`kavach-observe` — the process that makes Phases 31 and 32 real.

Without this, `claudecode.py` can read a transcript and `monitors.py` can
return findings, and nothing calls either. That is the defect this project
found three times in one day — `browser.py` imported by nothing, file tools
the agent could not reach, endpointing fixed in the copy that does not run.

Two jobs, both observation-only (Tier AUTO, Phase 30):

* **Watch** the newest Claude Code session and narrate test results.
* **Check** battery and KAVACH's own health on a schedule, and put anything
  worth acting on into the Phase 33 queue rather than doing it.

**It speaks through the API, not by opening the microphone stack.** A second
process holding Kokoro would mean two TTS engines and two audio devices, and
this project has already spent an evening on two Whisper instances competing.
If the daemon is not running, findings still reach the queue — narration is
the part that degrades, not the observation.

**Nothing here acts.** A monitor that could act would be a second action path
with its own gate.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from ..autonomy.monitors import check_battery, check_self_health, run_all
from ..autonomy.proposals import ProposalQueue
from ..killswitch.log import ActionLog
from .watcher import SessionWatcher

log = logging.getLogger("kavach.observe")

#: How often the scheduled checks run. Minutes, not seconds: a battery does
#: not change quickly and a health check that runs constantly is a health
#: problem of its own.
CHECK_INTERVAL_SECONDS = 300


def _battery() -> tuple[int | None, bool | None]:
    """Percentage and whether it is charging, or (None, None).

    `pmset` is read-only and takes no arguments from anywhere, so nothing
    user-supplied reaches a command line. Returning None rather than guessing
    is deliberate: an unread battery and a healthy battery look the same to
    everyone downstream, and only one of them is true.
    """
    import re
    import subprocess

    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return (None, None)
    percent = re.search(r"(\d+)%", out)
    if not percent:
        return (None, None)
    charging = "AC Power" in out or "charging" in out.lower()
    return (int(percent.group(1)), charging)


def _processes() -> dict[str, bool]:
    """Which KAVACH pieces are alive, by their launchd labels."""
    import subprocess

    alive = {}
    for name, label in (("voice", "com.krishna.kavach"),
                        ("overlay", "com.krishna.kavach.overlay")):
        try:
            result = subprocess.run(
                ["launchctl", "print", f"gui/{__import__('os').getuid()}/{label}"],
                capture_output=True, text=True, timeout=5)
            alive[name] = result.returncode == 0
        except Exception:
            # Unknown, not dead. `check_self_health` treats an empty mapping
            # as unknown; a wrong False here would cry wolf every cycle.
            continue
    return alive


def speak(text: str) -> bool:
    """Ask the running voice daemon to say something. False if it cannot.

    Through the API deliberately: a second process holding Kokoro would mean
    two TTS engines and two audio devices, and two Whisper instances competing
    already cost this project an evening.
    """
    try:
        import json
        import urllib.request
        from pathlib import Path

        env = Path(__file__).resolve().parents[2] / ".env"
        token = ""
        for line in env.read_text().splitlines():
            if line.startswith("KAVACH_API_TOKEN="):
                token = line.split("=", 1)[1].strip()
        if not token:
            return False
        request = urllib.request.Request(
            "http://127.0.0.1:8770/say",
            data=json.dumps({"text": text}).encode(),
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=10)
        return True
    except Exception:
        log.debug("could not reach the voice daemon to speak", exc_info=True)
        return False


def run_checks(queue: ProposalQueue, action_log: ActionLog) -> int:
    """One round of scheduled checks. Returns how many findings there were."""
    percent, charging = _battery()
    findings = run_all([
        lambda: check_battery(percent, charging),
        lambda: check_self_health(_processes()),
    ])
    for finding in findings:
        action_log.append("monitor.finding", source=finding.source,
                          severity=finding.severity, detail=finding.detail)
        log.info("%s: %s", finding.source, finding.detail)
    return len(findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kavach-observe",
        description="Watch Claude Code and run scheduled checks (Phases 31-32).")
    parser.add_argument("--once", action="store_true",
                        help="run the checks once and exit")
    parser.add_argument("--quiet", action="store_true",
                        help="log findings but never speak")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    action_log = ActionLog()
    queue = ProposalQueue(log_=action_log)

    if args.once:
        found = run_checks(queue, action_log)
        print(f"{found} finding(s)")
        return 0

    stop = threading.Event()
    watcher = SessionWatcher()

    def narrate(text, observation):
        action_log.append("observe.narrated", kind=observation.kind,
                          ok=observation.ok, detail=observation.detail)
        if not args.quiet:
            speak(text)
        log.info("%s", text)

    threading.Thread(target=watcher.follow, args=(narrate, stop),
                     daemon=True, name="kavach-observe-watch").start()

    log.info("observing — Claude Code sessions and scheduled checks")
    try:
        while True:
            run_checks(queue, action_log)
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        stop.set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
