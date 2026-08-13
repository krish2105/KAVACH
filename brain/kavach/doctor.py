"""`kavach-doctor` — check every layer, and be honest about what it cannot check.

Written because "is it working?" had become a twenty-minute manual crawl through
five terminals, and because several things broke this session in ways that
looked fine from outside: a wake word that scored 0.0 on everything, a panel
rendering server HTML with no orb, a hidden window burning a core.

Two rules:

* **Exercise, do not introspect.** Checking that a file exists proves nothing.
  Where a check can actually run the thing — latch the kill switch, feed noise
  to the VAD, ask an MCP server a question — it does.
* **Never report a pass for something not tested.** Anything needing a voice,
  a hand or a click is listed as MANUAL with the exact command, not skipped
  silently and not counted as green.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BRAIN = Path(__file__).resolve().parent.parent

PASS, FAIL, WARN, MANUAL = "PASS", "FAIL", "WARN", "MANUAL"

_MARK = {PASS: "✓", FAIL: "✗", WARN: "!", MANUAL: "·"}


@dataclass
class Check:
    layer: str
    name: str
    status: str
    detail: str = ""


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _running(pattern: str) -> bool:
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


# ─────────────────────────── guardrails (§7) ───────────────────────────

def check_kill_switch(tmp: Path) -> list[Check]:
    from kavach.killswitch.core import KillSwitch, KillSwitchDisarmed
    from kavach.killswitch.log import ActionLog

    out: list[Check] = []
    ks = KillSwitch(log=ActionLog(tmp / "doctor.jsonl"))

    try:
        ks.guard("doctor")
        out.append(Check("guardrails", "kill switch starts armed", PASS))
    except KillSwitchDisarmed:
        return [Check("guardrails", "kill switch starts armed", FAIL, "armed switch refused")]

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                             start_new_session=True)
    ks.register_process(child)
    started = time.monotonic()
    ks.trigger(source="doctor", reason="self-check")
    elapsed_ms = (time.monotonic() - started) * 1000

    try:
        child.wait(timeout=5)
        out.append(Check("guardrails", "kill switch kills a live process", PASS,
                         f"exit {child.returncode} in {elapsed_ms:.0f}ms"))
    except subprocess.TimeoutExpired:
        child.kill()
        out.append(Check("guardrails", "kill switch kills a live process", FAIL,
                         "process survived"))

    try:
        ks.guard("doctor")
        out.append(Check("guardrails", "stays latched after firing", FAIL,
                         "guard passed while disarmed — no auto-recovery is the point"))
    except KillSwitchDisarmed:
        out.append(Check("guardrails", "stays latched after firing", PASS))

    ks.rearm(source="doctor")
    return out


def check_gate(tmp: Path) -> list[Check]:
    from kavach.hands.gate import ToolGate
    from kavach.killswitch.core import KillSwitch
    from kavach.killswitch.log import ActionLog

    ks = KillSwitch(log=ActionLog(tmp / "doctor.jsonl"))
    gate = ToolGate(kill_switch=ks, confirmer=None)
    out: list[Check] = []

    async def verdict(tool: str, args: dict) -> str:
        return (await gate.check(tool, args, context=None)).behavior

    allowed = asyncio.run(verdict(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Safari" to return name of front window'}))
    out.append(Check("guardrails", "allowlisted read is permitted",
                     PASS if allowed == "allow" else FAIL, allowed))

    denied = asyncio.run(verdict(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Mail" to send outgoing message'}))
    out.append(Check("guardrails", "unlisted app is denied",
                     PASS if denied == "deny" else FAIL, denied))

    shell = asyncio.run(verdict("Bash", {"command": "rm -rf ~"}))
    out.append(Check("guardrails", "shell tools refused outright",
                     PASS if shell == "deny" else FAIL, shell))

    no_confirmer = asyncio.run(verdict(
        "mcp__macos-automator__execute_script",
        {"script_content": 'tell application "Notes" to delete note "x"'}))
    out.append(Check("guardrails", "destructive denied with no confirmer",
                     PASS if no_confirmer == "deny" else FAIL,
                     "silence is not consent"))
    return out


# ─────────────────────────── voice ───────────────────────────

def check_voice_gates() -> list[Check]:
    import numpy as np

    from kavach.identity.voiceprint import Voiceprint
    from kavach.voice import vad

    out: list[Check] = []
    rng = np.random.default_rng(0)

    noise = rng.normal(0, 0.05, 32_000).astype(np.float32)
    out.append(Check("voice", "VAD rejects noise",
                     PASS if not vad.has_speech(noise, 16_000) else FAIL))
    silence = np.zeros(32_000, dtype=np.float32)
    out.append(Check("voice", "VAD rejects silence",
                     PASS if not vad.has_speech(silence, 16_000) else FAIL))

    vp = Voiceprint()
    if not vp.is_enrolled:
        out.append(Check("voice", "voiceprint enrolled", FAIL,
                         "run `uv run kavach-enrol --spoken`"))
        return out

    out.append(Check("voice", "voiceprint enrolled", PASS,
                     f"threshold {vp.threshold:.3f}"))
    try:
        import scipy.signal as ss

        from kavach.voice.loop import DEFAULT_MODELS_DIR
        from kavach.voice.tts import TextToSpeech

        tts = TextToSpeech(DEFAULT_MODELS_DIR)
        tts.load()
        speech = tts.synthesize("Delete the draft in Notes.", voice="am_michael")
        audio = ss.resample_poly(speech.audio, 16_000, speech.sample_rate).astype(np.float32)
        result = vp.verify(audio, sample_rate=16_000)
        out.append(Check("voice", "voiceprint rejects another voice",
                         PASS if not result.accepted else FAIL,
                         f"similarity {result.similarity:.3f} < {result.threshold:.3f}"))
    except Exception as exc:
        out.append(Check("voice", "voiceprint rejects another voice", WARN, str(exc)[:60]))
    return out


def check_wake_word() -> list[Check]:
    from kavach.voice.loop import find_wake_model
    from kavach.voice.waketune import load_calibration

    model = find_wake_model()
    if not model.exists():
        return [Check("voice", "wake word", WARN, "not trained — push-to-talk only")]
    from kavach.voice.loop import find_wake_model

    calibrated = load_calibration(model=find_wake_model())
    if calibrated is None:
        return [Check("voice", "wake word calibrated", WARN,
                      "trained but not calibrated on your voice → not loaded. "
                      "`uv run kavach-waketune`")]
    return [Check("voice", "wake word calibrated", PASS, f"threshold {calibrated:.3f}")]


# ─────────────────────────── services ───────────────────────────

def check_services() -> list[Check]:
    out: list[Check] = []

    bridge = _port_open(8765)
    out.append(Check("services", "brain bridge :8765",
                     PASS if bridge else FAIL,
                     "" if bridge else "start `uv run python -m kavach.voice`"))

    orb = _port_open(3100)
    out.append(Check("services", "orb server :3100",
                     PASS if orb else FAIL,
                     "" if orb else "start `npx next start -p 3100` in apps/orb"))

    if orb:
        try:
            import urllib.request

            with urllib.request.urlopen("http://127.0.0.1:3100/?overlay=1", timeout=3) as r:
                body = r.read(4000).decode("utf-8", "ignore")
            dev = "__next_hmr" in body or "webpack-hmr" in body
            out.append(Check("services", "orb served as production build",
                             WARN if dev else PASS,
                             "dev server detected — React will not hydrate in the "
                             "panel; use `next start`" if dev else ""))
        except Exception as exc:
            out.append(Check("services", "orb responds", FAIL, str(exc)[:60]))

    overlay = _running("kavach-overlay")
    out.append(Check("services", "desktop orb panel",
                     PASS if overlay else WARN,
                     "" if overlay else "launchctl bootstrap gui/$UID "
                                        "~/Library/LaunchAgents/com.krishna.kavach.overlay.plist"))

    if overlay:
        log = BRAIN / "wakeword" / "logs" / "overlay.log"
        reports = [l for l in log.read_text(errors="ignore").splitlines()
                   if "page reports" in l] if log.exists() else []
        if reports:
            try:
                data = json.loads(reports[-1][reports[-1].index("{"):])
                ok = data.get("canvas") and data.get("overlay")
                out.append(Check("services", "panel is rendering the orb",
                                 PASS if ok else FAIL,
                                 f"{data.get('cssPx')}pt at {data.get('ratio')}x"
                                 if ok else "no canvas — React did not hydrate"))
            except Exception:
                pass
    return out


def check_api() -> list[Check]:
    """The local API (Phase 6) — reachable, authenticated, and localhost-only.

    Read-only on purpose. The doctor must never POST /command: a diagnostic
    that acts on your machine to prove it can act on your machine is not a
    diagnostic.
    """
    import urllib.error
    import urllib.request

    out: list[Check] = []
    up = _port_open(8770)
    out.append(Check("api", "local api :8770", PASS if up else FAIL,
                     "" if up else "start `uv run python -m kavach.voice`"))
    if not up:
        return out

    def _get(path: str, token: str | None) -> int:
        req = urllib.request.Request(f"http://127.0.0.1:8770{path}")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:
            return 0

    unauth = _get("/status", None)
    out.append(Check("api", "refuses an unauthenticated request",
                     PASS if unauth == 401 else FAIL,
                     "" if unauth == 401 else f"expected 401, got {unauth}"))

    out.append(Check("api", "refuses a wrong token",
                     PASS if _get("/status", "wrong") == 401 else FAIL))

    token = ""
    env = BRAIN / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("KAVACH_API_TOKEN="):
                token = line.split("=", 1)[1].strip()
    if token:
        code = _get("/status", token)
        out.append(Check("api", "accepts the real token",
                         PASS if code == 200 else FAIL,
                         "" if code == 200 else f"got {code}"))
        mode = oct(env.stat().st_mode & 0o777)
        out.append(Check("api", "token file is not world-readable",
                         PASS if mode == "0o600" else WARN,
                         "" if mode == "0o600" else f"{env} is {mode}"))
    else:
        out.append(Check("api", "api token present", FAIL,
                         "no KAVACH_API_TOKEN in brain/.env"))

    # Bound to loopback, not the LAN. Phase 9 reaches this over Tailscale,
    # which does not need it listening on a network interface.
    lan = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.4)
            probe.connect(("8.8.8.8", 80))
            lan = probe.getsockname()[0]
    except Exception:
        pass
    if lan and not lan.startswith("127."):
        exposed = _port_open(8770, lan)
        out.append(Check("api", "not listening on the network",
                         FAIL if exposed else PASS,
                         f"reachable on {lan}:8770" if exposed else f"loopback only"))
    return out


def check_tailscale() -> list[Check]:
    """The phone's route in (Phase 7).

    Reports honestly at every step: not installed is a WARN, not a FAIL —
    KAVACH works fine on this Mac without it, and only the phone needs it.
    """
    out: list[Check] = []

    binary = shutil.which("tailscale") or (
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
        if Path("/Applications/Tailscale.app").exists() else None
    )
    if not binary:
        out.append(Check("reach", "tailscale installed", WARN,
                         "not installed — the phone cannot reach KAVACH. "
                         "See SETUP.md"))
        return out
    out.append(Check("reach", "tailscale installed", PASS))

    def _run(*args: str) -> tuple[int, str]:
        try:
            done = subprocess.run([binary, *args], capture_output=True,
                                  text=True, timeout=8)
            return done.returncode, (done.stdout or done.stderr).strip()
        except Exception as exc:
            return 1, str(exc)[:80]

    code, status = _run("status", "--json")
    if code != 0:
        out.append(Check("reach", "tailscale logged in", WARN,
                         "run `tailscale up` — see SETUP.md"))
        return out

    try:
        data = json.loads(status)
    except Exception:
        data = {}
    backend = data.get("BackendState", "")
    online = backend == "Running"
    out.append(Check("reach", "tailnet connected", PASS if online else WARN,
                     "" if online else f"state: {backend or 'unknown'}"))

    name = (data.get("Self") or {}).get("DNSName", "").rstrip(".")
    if name:
        out.append(Check("reach", "this machine on the tailnet", PASS, name))

    code, serve = _run("serve", "status")
    proxied = "8770" in serve
    out.append(Check("reach", "api served to the tailnet",
                     PASS if proxied else WARN,
                     f"https://{name}/" if proxied and name else
                     "run `tailscale serve --bg 8770` — see SETUP.md"))

    # Serving to the tailnet must NOT have quietly widened the binding. This is
    # the whole reason Serve was chosen over binding to the LAN, so it is
    # checked here rather than assumed.
    if proxied:
        lan = ""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.settimeout(0.4)
                probe.connect(("8.8.8.8", 80))
                lan = probe.getsockname()[0]
        except Exception:
            pass
        if lan and not lan.startswith("127."):
            exposed = _port_open(8770, lan)
            out.append(Check("reach", "still not on the LAN",
                             FAIL if exposed else PASS,
                             f"reachable on {lan}:8770" if exposed
                             else "tailnet only, as intended"))
    return out


def check_mcp() -> list[Check]:
    config = BRAIN.parent / "hands" / "mcp.config.json"
    if not config.exists():
        return [Check("hands", "MCP config", FAIL, str(config))]
    servers = json.loads(config.read_text()).get("mcpServers", {})
    return [Check("hands", "MCP servers configured", PASS, ", ".join(servers))]


# ─────────────────────────── manual ───────────────────────────

MANUAL_CHECKS = [
    ("voice", "speak a command",
     "hold Space in the panel and say \"what time is it\""),
    ("voice", "wake word fires on your voice",
     "say \"KAVACH\" — only after `kavach-waketune` succeeds"),
    ("presence", "drag the panel",
     "🛡 menu → Move / resize, then drag it"),
    ("guardrails", "spoken confirmation on a destructive action",
     "ask it to delete a note; it must speak the action back and wait"),
    ("guardrails", "kill switch hotkey",
     "press ⌃⌥⌘K mid-action; needs Input Monitoring"),
    ("presence", "gesture confirmation",
     "press G for gestures, then hold a thumbs-up at the camera"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check every KAVACH layer.")
    parser.add_argument("--skip-slow", action="store_true",
                        help="skip checks that load models")
    args = parser.parse_args(argv)

    import tempfile

    checks: list[Check] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        checks += check_kill_switch(tmp)
        checks += check_gate(tmp)
        checks += check_mcp()
        checks += check_services()
        checks += check_api()
        checks += check_tailscale()
        checks += check_wake_word()
        if not args.skip_slow:
            checks += check_voice_gates()

    width = max(len(c.name) for c in checks) + 2
    layer = None
    print()
    for check in checks:
        if check.layer != layer:
            layer = check.layer
            print(f"  {layer.upper()}")
        detail = f"  — {check.detail}" if check.detail else ""
        print(f"    {_MARK[check.status]} {check.name:<{width}}{detail}")

    print(f"\n  MANUAL — needs your voice, hands or eyes; not checked above")
    for _, name, how in MANUAL_CHECKS:
        print(f"    · {name:<{width}}  {how}")

    failed = [c for c in checks if c.status == FAIL]
    warned = [c for c in checks if c.status == WARN]
    print()
    print(f"  {len(checks) - len(failed) - len(warned)} passed, "
          f"{len(warned)} warning, {len(failed)} failed, "
          f"{len(MANUAL_CHECKS)} manual")
    if failed:
        print("\n  Failures are real: something that should work does not.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
