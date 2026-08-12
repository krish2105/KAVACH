"""`kavach kill` — the always-available kill surface.

Deliberately the dumbest of the four surfaces: no GUI, no permissions, no
event loop of its own beyond one socket round-trip. When the hotkey has not
been granted Accessibility, or the menubar item has wedged, this still works
from any terminal.

    kavach kill                 halt everything and latch disarmed
    kavach status               is it armed? what is in flight?
    kavach rearm                explicit re-arm (the only way out of the latch)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .ipc import DEFAULT_SOCKET_PATH, send_command


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kavach",
        description="KAVACH kill switch control (spec §7).",
    )
    parser.add_argument(
        "command", choices=["kill", "rearm", "status", "ping"],
        help="kill = halt everything and latch disarmed",
    )
    parser.add_argument(
        "--socket", default=str(DEFAULT_SOCKET_PATH),
        help=f"control socket path (default: {DEFAULT_SOCKET_PATH})",
    )
    parser.add_argument("--reason", default="", help="recorded in the action log")
    parser.add_argument("--json", action="store_true", help="raw JSON output")
    return parser


def _render(command: str, response: dict) -> str:
    if not response.get("ok"):
        return f"✗ {response.get('error', 'unknown error')}"

    status = response.get("status", {})
    state = status.get("state", "?")

    if command == "kill":
        record = response.get("record", {})
        return (
            f"✗ KILLED — state is now {state.upper()}\n"
            f"  cancelled tasks:   {record.get('cancelled_tasks', 0)}\n"
            f"  killed processes:  {record.get('killed_processes', [])}\n"
            f"  errors:            {record.get('errors') or 'none'}\n"
            f"  logged at:         {record.get('ts')}\n"
            f"  re-arm with:       kavach rearm"
        )
    if command == "rearm":
        return f"✓ re-armed — state is now {state.upper()}"

    return (
        f"state:            {state.upper()}\n"
        f"in-flight tasks:  {status.get('in_flight_tasks', 0)}\n"
        f"live processes:   {status.get('live_processes', [])}\n"
        f"action log:       {status.get('log_path')}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not Path(args.socket).exists():
        print(
            f"✗ no kill-switch daemon at {args.socket}\n"
            f"  start one with:  uv run python -m kavach.killswitch.daemon",
            file=sys.stderr,
        )
        return 2

    try:
        response = asyncio.run(
            send_command(args.command, path=args.socket,
                         reason=args.reason, source="cli")
        )
    except (ConnectionRefusedError, FileNotFoundError):
        print(f"✗ daemon socket exists but is not accepting: {args.socket}",
              file=sys.stderr)
        return 2
    except asyncio.TimeoutError:
        print("✗ daemon did not respond within timeout", file=sys.stderr)
        return 2

    print(json.dumps(response, indent=2, default=str) if args.json
          else _render(args.command, response))

    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
