"""Run the API alongside the voice loop.

Started from `kavach/voice/__main__.py` on its own thread, next to the
WebSocket bridge. The bridge is untouched: the HUD keeps its private protocol
and this is a second, public-shaped surface, so nothing that works today has
to change for the iPhone to exist.
"""

from __future__ import annotations

import logging
import secrets
import threading
from pathlib import Path

log = logging.getLogger("kavach.api.server")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
TOKEN_KEY = "KAVACH_API_TOKEN"


def load_or_create_token() -> str:
    """The API token, generated on first run.

    Written to brain/.env, which is gitignored and mode 600. Generating it
    rather than shipping a default matters: a default token in a repo is not a
    token, and this API can act on the machine.
    """
    import os

    existing = os.environ.get(TOKEN_KEY)
    if existing and existing.strip():
        return existing.strip()

    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if line.startswith(f"{TOKEN_KEY}="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value

    token = secrets.token_urlsafe(32)
    with ENV_PATH.open("a") as handle:
        handle.write(f"\n# Generated on first run. Required by every API route.\n")
        handle.write(f"{TOKEN_KEY}={token}\n")
    ENV_PATH.chmod(0o600)
    log.info("generated an API token in %s", ENV_PATH)
    return token


class ApiServer(threading.Thread):
    """uvicorn on a background thread."""

    daemon = True

    def __init__(self, loop, kill_switch, registry=None,
                 host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 queue=None):
        super().__init__(name="kavach-api")
        self.loop = loop
        self.kill_switch = kill_switch
        self.registry = registry
        #: Phase 33's queue, so /proposals has something to show.
        self.queue = queue
        self.host = host
        self.port = port
        self.token = load_or_create_token()
        self._server = None

    def run(self) -> None:
        import uvicorn

        from .app import create_app

        app = create_app(
            loop=self.loop,
            kill_switch=self.kill_switch,
            token=self.token,
            registry=self.registry,
            # Phase 33. Without this the /proposals routes answer with an
            # empty list forever — a review surface that shows nothing is
            # the same as no review surface.
            queue=self.queue,
        )
        config = uvicorn.Config(
            app,
            host=self.host,
            port=self.port,
            log_level="warning",  # the access log would echo every poll
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        try:
            self._server.run()
        except Exception as exc:
            log.warning("api server stopped: %s", exc)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
