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

    def app_names(self, device: str = "mac") -> list[str]:
        """Every app this device may drive, in file order.

        Exists because the startup banner printed the list as a **string
        literal** — "Safari, Notes, Calendar, Finder" — which stayed frozen
        while the file grew to seven entries. §7 requires asking before the
        allowlist expands, and that is worth little if the running system
        misreports what it will drive.
        """
        return [e["name"] for e in self.device_entries(device)]

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

    def canonical_name(self, app: str) -> str | None:
        """The approved spelling of an app, or None if it isn't on the list.

        Load-bearing for anything that builds an AppleScript: the string that
        reaches ``tell application "…"`` is this file's spelling, never the
        transcript. Escaping a transcribed name would also work, and this is
        stronger — a name that is not already approved never reaches a script
        at all, so there is nothing to escape.
        """
        token = (app or "").strip().casefold()
        if not token:
            return None
        for entry in self.entries:
            if token in (entry["name"].casefold(), entry["bundle_id"].casefold()):
                return entry["name"]
        return None

    def check(self, app: str) -> None:
        """Raise :class:`AppNotAllowed` unless the app is on the list."""
        if not self.is_allowed(app):
            allowed = ", ".join(sorted(e["name"] for e in self.entries))
            raise AppNotAllowed(
                f"{app!r} is not on the KAVACH allowlist. Allowed: {allowed}. "
                f"Expanding the list is a deliberate decision — edit {self.path}."
            )

    # ——— growing the list ———

    def add(self, name: str, bundle_id: str, reason: str) -> dict:
        """Add one app, recording why. Returns the entry.

        §C says the list grows only by asking, and until now "asking" meant a
        human editing this file. Voice can now do it too, which is why the
        reason is a **required argument** rather than a comment: an entry that
        cannot say who wanted it is exactly what
        ``test_nothing_is_allowed_that_was_not_approved`` exists to catch.

        The write is whole-file and atomic (temp file, then rename), and only
        this device's ``allowed`` array is touched — the iPhone is governed by
        tool rather than by app, and a rewrite that reshaped its section would
        silently change grants nobody asked about.
        """
        name = (name or "").strip()
        bundle_id = (bundle_id or "").strip()
        reason = (reason or "").strip()
        if not name or not bundle_id:
            raise ValueError("an allowlist entry needs both a name and a bundle id")
        if not reason:
            raise ValueError(
                "an allowlist entry needs a recorded reason — an app that "
                "cannot say who approved it must not be added"
            )

        if self.is_allowed(name) or self.is_allowed(bundle_id):
            return next(e for e in self.entries
                        if name.casefold() in (e["name"].casefold(),
                                               e["bundle_id"].casefold())
                        or bundle_id.casefold() == e["bundle_id"].casefold())

        entry = {"name": name, "bundle_id": bundle_id, "reason": reason}

        data = json.loads(self.path.read_text())
        if "allowed" in data:                       # v1, flat
            data["allowed"].append(entry)
        else:
            data["devices"]["mac"]["allowed"].append(entry)

        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n")
        temp.replace(self.path)                     # atomic: never a half file

        self.entries.append(entry)
        self._names.add(entry["name"].casefold())
        self._bundle_ids.add(entry["bundle_id"].casefold())
        return entry

    def needs_confirmation(self, action: str) -> bool:
        """True if the action is destructive or externally visible, and so
        must be spoken back and confirmed before it runs (§7)."""
        text = (action or "").casefold()
        return any(token in text for token in self.confirm_always)
