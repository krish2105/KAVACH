"""Live kill-switch demonstration.

Stands in for Phase 4's real device control: starts a long-running subprocess
(as an MCP server would be) and a long-running async task (as an agent turn
would be), guards a work loop behind ``KillSwitch.guard()``, then waits.

    uv run python -m kavach.killswitch.demo

In another terminal:

    uv run kavach kill        # or press ⌃⌥⌘K, or use the menu bar item

Everything stops, the switch latches DISARMED, and the guarded loop refuses to
continue. Doubles as the §5 demo-video beat.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from .core import KillSwitch, KillSwitchDisarmed
from .ipc import DEFAULT_SOCKET_PATH, serve
from .log import ActionLog


async def _pretend_mcp_action(name: str) -> None:
    """A long action that should never be allowed to finish after a kill."""
    for i in range(1, 10_000):
        await asyncio.sleep(1)
        print(f"  [{name}] still running… step {i}", flush=True)


async def main(socket_path: Path, log_path: Path) -> int:
    ks = KillSwitch(log=ActionLog(log_path))
    server = await serve(ks, socket_path)

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"],
        start_new_session=True,  # own process group, so killpg reaches its children
    )
    ks.register_process(child)

    task = asyncio.create_task(_pretend_mcp_action("agent-turn"))
    ks.register_task(task)

    print("─" * 62)
    print("  KAVACH kill-switch demo — everything below is REAL")
    print("─" * 62)
    print(f"  socket           {socket_path}")
    print(f"  child process    pid {child.pid} (a stand-in MCP server)")
    print(f"  async task       'agent-turn' (a stand-in agent turn)")
    print()
    print(f"  Now run:  uv run kavach kill --socket {socket_path}")
    print("  …or press ⌃⌥⌘K, or use the menu bar PANIC item.")
    print("─" * 62, flush=True)

    try:
        while True:
            await asyncio.sleep(0.25)
            try:
                ks.guard("demo work loop")
            except KillSwitchDisarmed as exc:
                print()
                print("─" * 62)
                print("  KILL SWITCH FIRED")
                print("─" * 62)
                print(f"  guard() refused:  {exc}")
                print()

                await asyncio.sleep(0.2)  # let cancellation land
                print(f"  async task cancelled:  {task.cancelled()}")
                child.wait(timeout=5)
                print(f"  child pid {child.pid} exit code:  {child.returncode} "
                      f"(-9 = SIGKILL)")
                print(f"  state:                 {ks.status()['state'].upper()}")
                print(f"  action log:            {ks.log.path}")
                print("─" * 62, flush=True)
                return 0
    finally:
        if child.poll() is None:
            child.kill()
        server.close()
        await server.wait_closed()


if __name__ == "__main__":
    sock = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOCKET_PATH
    logp = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    raise SystemExit(asyncio.run(
        main(sock, logp or (Path.home() / ".kavach" / "logs" / "actions.jsonl"))
    ))
