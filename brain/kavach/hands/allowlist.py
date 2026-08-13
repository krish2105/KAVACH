"""App allowlist (spec §7).

An allowlist, not a blocklist: anything not named here is denied. That ordering
is the whole point — a blocklist silently permits every app nobody thought of,
which for an agent with Accessibility access means "all of them".

Phase 0 establishes the file and this check. Phase 4 wires
:meth:`Allowlist.check` into the real MCP dispatch path alongside
``KillSwitch.guard()``.
"""

from __future__ import annotations

import json
from pathlib import Path

# brain/kavach/hands/allowlist.py -> repo root is four parents up.
DEFAULT_ALLOWLIST_PATH = (
    Path(__file__).resolve().parents[3] / "hands" / "allowlist.json"
)


class AppNotAllowed(PermissionError):
    """Raised when an action targets an app outside the allowlist."""


class Allowlist:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_ALLOWLIST_PATH
        data = json.loads(self.path.read_text())

        self.version: int = data["version"]
        self.devices: dict = data.get("devices", {})

        # v1 kept a flat `allowed` list; v2 moved it under devices.mac. The
        # flat API below still speaks for the Mac so every existing caller and
        # test keeps working unchanged.
        if "allowed" in data:                      # v1
            self.devices = {"mac": {"enabled": True, "allowed": data["allowed"]}}
        self.entries: list[dict] = self.device_entries("mac")
        self.confirm_always: set[str] = {
            token.lower() for token in data.get("confirm_always", [])
        }

        self._names = {e["name"].casefold() for e in self.entries}
        self._bundle_ids = {e["bundle_id"].casefold() for e in self.entries}

    # ——— device-scoped ———

    def device_entries(self, device: str) -> list[dict]:
        return self.devices.get(device, {}).get("allowed", [])

    def device_enabled(self, device: str) -> bool:
        """A device nobody has enabled is denied, like an unlisted app."""
        return bool(self.devices.get(device, {}).get("enabled", False))

    def device_tool_policy(self, device: str, tool: str) -> str:
        """`allow`, `confirm` or `deny` for a device gated by tool rather than
        by app — see hands/allowlist.json for why the iPhone works this way."""
        config = self.devices.get(device, {})
        if tool in config.get("read_only_tools", []):
            return "allow"
        if tool in config.get("confirm_tools", []):
            return "confirm"
        return "deny"

    # ——— app-scoped (the Mac) ———

    def is_allowed(self, app: str) -> bool:
        """Accept either a display name ("Safari") or a bundle id."""
        token = (app or "").strip().casefold()
        if not token:
            return False
        return token in self._names or token in self._bundle_ids

    def check(self, app: str) -> None:
        """Raise :class:`AppNotAllowed` unless the app is on the list."""
        if not self.is_allowed(app):
            allowed = ", ".join(sorted(e["name"] for e in self.entries))
            raise AppNotAllowed(
                f"{app!r} is not on the KAVACH allowlist. Allowed: {allowed}. "
                f"Expanding the list is a deliberate decision — edit {self.path}."
            )

    def needs_confirmation(self, action: str) -> bool:
        """True if the action is destructive or externally visible, and so
        must be spoken back and confirmed before it runs (§7)."""
        text = (action or "").casefold()
        return any(token in text for token in self.confirm_always)
