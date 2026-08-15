"""Reading and writing the disk, through the same policy as everything else.

The last piece of total access. The user chose "read and write anywhere,
irreversible operations confirmed" — the same trade as the shell, for the same
reason: **the verb is what matters, not the location.** A gate that asked
*which directory* would be the app allowlist again, wrong on the same axis.

Order, matching `MacActions` and `ToolGate`::

    kill switch  →  resolve  →  confirm if irreversible  →  act  →  log

**Full Disk Access is a different thing from this module.** FDA decides whether
macOS lets this process open `~/Library/Mail` at all; these tools decide what
KAVACH does with what it can reach. Without the grant, protected paths raise
`PermissionError`, and that is reported as a missing grant rather than as an
empty result — "no mail found" would be a lie about the cause, and the user
would go looking for the wrong problem. This is the same failure the project
already hit with launchd and TCC, where a denial presented as a hang.

Three rules, each preventing a specific way to lose a file:

* **Writes and deletes confirm; reads do not.** Confirming every read trains
  the user to say yes reflexively, which destroys the value of asking about
  the writes.
* **Deletes go to the Trash, not `unlink`.** An irreversible operation that
  can be made reversible should be. Then a mis-transcribed filename costs a
  trip to the Trash rather than a restore from a backup nobody made — which
  matters here more than anywhere, because whisper renders this user's own
  wake word as "Gaavj".
* **Paths are resolved before anything checks them.** `~/Documents/../../etc`
  is `/etc`. A check that runs before resolution checks a spelling.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from ..killswitch.core import KillSwitchDisarmed

log = logging.getLogger("kavach.hands.files")

#: How much of a file to read in one go. A voice assistant reads things aloud
#: or summarises them; handing a 2GB log to a language model helps nobody and
#: a truncated read is honest as long as it says so.
MAX_READ_BYTES = 2_000_000

#: What a missing Full Disk Access grant looks like, in words the user can act
#: on. macOS reports it as a bare EPERM, which reads like a broken disk.
FDA_HINT = (
    "macOS refused this path. If it is somewhere protected (Mail, Messages, "
    "Safari history, another app's data) this needs Full Disk Access: System "
    "Settings → Privacy & Security → Full Disk Access, and add the KAVACH "
    "python binary."
)


def resolve_path(path: str | None) -> Path:
    """Expand and resolve `path`, or raise.

    Resolution happens **first**, before any check, because
    `~/Documents/../../etc/passwd` is `/etc/passwd` and a check against the
    unresolved string is a check against a spelling rather than a location.
    """
    if path is None or not str(path).strip():
        raise ValueError("no path given")
    return Path(str(path).strip()).expanduser().resolve()


class FileTools:
    """File operations, gated.

    `confirmer` is anything with `confirm_sync(prompt) -> bool`. Synchronous
    because these are called from `MacActions`' thread rather than the async
    tool path; the same `VoiceConfirmer` answer reaches both.
    """

    def __init__(self, kill_switch, confirmer=None, confirmed_upstream=False):
        self.ks = kill_switch
        self.confirmer = confirmer
        #: The caller's gate already asked, so do not ask again.
        #:
        #: `ToolGate` confirms `mcp__kavach-files__delete_file` at the
        #: PreToolUse hook — that is the §7 enforcement point. Asking a second
        #: time here would mean two prompts for one delete, and a user asked
        #: twice learns to say yes twice, which is the failure confirmations
        #: exist to avoid.
        #:
        #: **Not the default.** A FileTools with neither a confirmer nor this
        #: flag refuses every write and delete: silence is not consent, and an
        #: unattended caller is silence.
        self.confirmed_upstream = confirmed_upstream
        #: Where the last delete went, so a caller can say so out loud.
        self.last_trashed: Path | None = None
        self._confirmed_by = "user"

    # ——— gates ———

    def _guard(self, action: str) -> None:
        """The kill switch outranks everything, including reads.

        Latched means *nothing runs*, not *nothing destructive runs*. An
        ambiguous state stays stopped (§C).
        """
        self.ks.guard(f"file.{action}")

    def _ask(self, prompt: str, event: str, **fields) -> None:
        """Confirm or raise. Denial is the default at every branch."""
        if self.confirmed_upstream:
            # Consent was given at the gate. Recorded here anyway: the consent
            # happened somewhere else, the record still belongs with the act.
            self._confirmed_by = "gate"
            return
        if self.confirmer is None:
            self.ks.log.append("file.refused", reason="no confirmer", **fields)
            raise PermissionError(
                f"{prompt} — but there is no way to ask you right now, so no."
            )
        if not self.confirmer.confirm_sync(prompt):
            self.ks.log.append("file.refused", reason="declined", **fields)
            raise PermissionError("You declined.")

    @staticmethod
    def _wrap_permission(exc: OSError, target: Path) -> PermissionError:
        wrapped = PermissionError(f"{target}: {FDA_HINT}")
        wrapped.__cause__ = exc
        return wrapped

    # ——— reading ———

    def read(self, path: str, max_bytes: int = MAX_READ_BYTES) -> str:
        self._guard("read")
        target = resolve_path(path)
        try:
            text = target.read_text(errors="replace")
        except PermissionError as exc:
            self.ks.log.append("file.refused", path=str(target), reason="permission")
            raise self._wrap_permission(exc, target)
        except IsADirectoryError:
            return "\n".join(self.list_dir(str(target)))

        truncated = len(text) > max_bytes
        if truncated:
            # Saying so matters: a silently truncated file read as a complete
            # one is how an assistant confidently answers from half a document.
            text = text[:max_bytes] + f"\n\n[truncated at {max_bytes} bytes]"
        self.ks.log.append("file.read", path=str(target), bytes=len(text),
                           truncated=truncated)
        return text

    def list_dir(self, path: str) -> list[str]:
        self._guard("list")
        target = resolve_path(path)
        try:
            names = sorted(p.name for p in target.iterdir())
        except PermissionError as exc:
            self.ks.log.append("file.refused", path=str(target), reason="permission")
            raise self._wrap_permission(exc, target)
        self.ks.log.append("file.list", path=str(target), entries=len(names))
        return names

    def search(self, root: str, pattern: str, limit: int = 200) -> list[str]:
        """Filenames under `root` matching a glob. Reads no contents."""
        self._guard("search")
        base = resolve_path(root)
        found: list[str] = []
        truncated = False
        try:
            for p in base.rglob(pattern):
                if len(found) >= limit:
                    # Say so. `read()` announces its truncation and this did
                    # not — it returned exactly `limit` results, which is
                    # indistinguishable from "that is all of them". Seen live:
                    # an iCloud search returned 200 against a limit of 200, and
                    # "I found 200 tax files" would have been confidently wrong
                    # about the one thing the user asked.
                    truncated = True
                    break
                found.append(str(p))
        except PermissionError as exc:
            raise self._wrap_permission(exc, base)
        except OSError:
            log.debug("search stopped early under %s", base, exc_info=True)
        self.ks.log.append("file.search", root=str(base), pattern=pattern,
                           found=len(found), truncated=truncated)
        if truncated:
            found.append(f"[stopped at {limit} matches — there are more]")
        return found

    # ——— writing ———

    def write(self, path: str, content: str) -> Path:
        self._guard("write")
        target = resolve_path(path)
        exists = target.exists()

        # An overwrite loses something and a create does not. The prompt says
        # which, because they deserve different answers.
        verb = "overwrite" if exists else "create"
        self._ask(f"This will {verb} {target}. Should I?",
                  "file.write", path=str(target), overwrite=exists)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        except PermissionError as exc:
            self.ks.log.append("file.refused", path=str(target), reason="permission")
            raise self._wrap_permission(exc, target)

        self.ks.log.append("file.write", path=str(target), bytes=len(content),
                           overwrite=exists, confirmed_by=self._confirmed_by)
        return target

    def delete(self, path: str) -> Path:
        """Move to the Trash. **Never `unlink`.**

        An irreversible operation that can be made reversible should be. The
        confirmation still fires — this is defence in depth, not a substitute
        — but if a mis-transcription gets through both, the file is in the
        Trash rather than gone.
        """
        self._guard("delete")
        target = resolve_path(path)
        if not target.exists():
            raise FileNotFoundError(f"{target} does not exist, so there is "
                                    f"nothing to delete.")

        self._ask(f"This will move {target} to the Trash. Should I?",
                  "file.delete", path=str(target))

        trashed = self._to_trash(target)
        self.last_trashed = trashed
        self.ks.log.append("file.delete", path=str(target),
                           trashed_to=str(trashed),
                           confirmed_by=self._confirmed_by)
        return trashed

    def _to_trash(self, target: Path) -> Path:
        """Finder's own Trash, so the file is restorable from the UI.

        Falls back to `~/.Trash` by move if AppleScript is unavailable. The
        fallback is still a move — there is no branch of this method that
        deletes anything.
        """
        try:
            # POSIX file takes a path string; the path is a resolved Path
            # object here, never a transcript, so nothing user-supplied is
            # interpolated into a script.
            script = (f'tell application "Finder" to delete POSIX file '
                      f'"{target}"')
            result = subprocess.run(["osascript", "-e", script],
                                    capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                return Path.home() / ".Trash" / target.name
            log.warning("Finder refused to trash %s: %s", target, result.stderr)
        except Exception:
            log.debug("osascript trash failed for %s", target, exc_info=True)

        destination = Path.home() / ".Trash" / target.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        counter = 1
        while destination.exists():
            destination = destination.with_name(
                f"{target.stem}-{counter}{target.suffix}")
            counter += 1
        shutil.move(str(target), str(destination))
        return destination


__all__ = ["FileTools", "resolve_path", "MAX_READ_BYTES", "FDA_HINT"]
