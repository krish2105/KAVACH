"""Reading and writing the disk, through the same policy as everything else.

The last piece of total access (spec §8 deferred it). The user chose "read and
write anywhere, irreversible operations confirmed" — the same trade as the
shell, for the same reason: what matters is the verb, not the location.

**Full Disk Access is a separate thing from this module.** FDA governs whether
macOS lets the process read `~/Library/Mail` at all; these tools govern what
KAVACH does with what it can reach. Without the grant, protected paths raise
PermissionError and are reported as such — a missing grant is a clear refusal,
never a silent empty result.

Three rules, each with its own failure it prevents:

* **Writes and deletes confirm.** Reversibility is the axis, as everywhere else.
* **Deletes go to the Trash**, not `unlink`. An irreversible operation that can
  be made reversible should be, and then the confirmation is a courtesy rather
  than the only thing between you and a lost file.
* **Paths are resolved before they are checked.** `~/Documents/../../etc` is
  `/etc`, and a check that runs before resolution checks a string rather than
  a location.
"""

from pathlib import Path

import pytest

from kavach.hands.files import FileTools, resolve_path


# ═══ paths mean what they resolve to ═══

def test_a_path_is_resolved_before_anything_looks_at_it(tmp_path):
    """`~/Documents/../../../etc/passwd` is /etc/passwd. Checking the string
    checks a spelling; checking the resolved path checks a location."""
    tricky = tmp_path / "a" / ".." / "b.txt"
    assert resolve_path(str(tricky)) == (tmp_path / "b.txt")


def test_a_tilde_is_expanded():
    assert resolve_path("~/Documents") == Path.home() / "Documents"


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_an_empty_path_is_refused(bad):
    with pytest.raises(ValueError):
        resolve_path(bad)


# ═══ reading ═══

def test_reading_a_file_needs_no_confirmation(tmp_path):
    """Confirming every read trains the user to say yes reflexively, which
    destroys the value of asking about the writes."""
    target = tmp_path / "notes.txt"
    target.write_text("hello")
    tools, confirmer = _tools(tmp_path)

    assert tools.read(str(target)).strip() == "hello"
    assert not confirmer.asked


def test_a_missing_grant_is_reported_not_swallowed(tmp_path, monkeypatch):
    """Without Full Disk Access, ~/Library/Mail raises PermissionError. An
    empty result would read as "no mail", which is a lie about the cause."""
    tools, _ = _tools(tmp_path)

    def denied(*a, **k):
        raise PermissionError(13, "Operation not permitted")

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(PermissionError) as exc:
        tools.read(str(tmp_path / "anything.txt"))
    assert "Full Disk Access" in str(exc.value)


def test_reading_a_directory_is_a_listing_not_an_error(tmp_path):
    (tmp_path / "one.txt").write_text("x")
    (tmp_path / "two.txt").write_text("y")
    tools, _ = _tools(tmp_path)

    names = tools.list_dir(str(tmp_path))
    assert {"one.txt", "two.txt"} <= set(names)


# ═══ writing ═══

def test_writing_confirms_first(tmp_path):
    tools, confirmer = _tools(tmp_path)
    target = tmp_path / "new.txt"

    tools.write(str(target), "content")

    assert confirmer.asked, "a file was written without asking"
    assert str(target) in confirmer.asked[0], "the user was not shown the path"
    assert target.read_text() == "content"


def test_declining_a_write_leaves_the_file_alone(tmp_path):
    target = tmp_path / "precious.txt"
    target.write_text("original")
    tools, _ = _tools(tmp_path, answer=False)

    with pytest.raises(PermissionError):
        tools.write(str(target), "replaced")

    assert target.read_text() == "original"


def test_an_overwrite_says_it_is_an_overwrite(tmp_path):
    """"Write to notes.txt" and "replace the contents of notes.txt" deserve
    different answers, and only one of them loses something."""
    target = tmp_path / "notes.txt"
    target.write_text("existing")
    tools, confirmer = _tools(tmp_path)

    tools.write(str(target), "new")

    assert "overwrite" in confirmer.asked[0].lower()


# ═══ deleting ═══

def test_deleting_moves_to_the_trash(tmp_path):
    """An irreversible operation that can be made reversible should be. Then
    a mis-transcribed filename costs a trip to the Trash, not a restore from
    a backup nobody made."""
    target = tmp_path / "gone.txt"
    target.write_text("x")
    tools, confirmer = _tools(tmp_path)

    tools.delete(str(target))

    assert confirmer.asked
    assert not target.exists()
    assert tools.last_trashed is not None, "it was unlinked, not trashed"


def test_declining_a_delete_keeps_the_file(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("x")
    tools, _ = _tools(tmp_path, answer=False)

    with pytest.raises(PermissionError):
        tools.delete(str(target))

    assert target.exists()


# ═══ the kill switch outranks all of it ═══

def test_a_latched_kill_switch_stops_every_write(tmp_path):
    tools, _ = _tools(tmp_path)
    tools.ks.trigger("test", "latched")
    target = tmp_path / "x.txt"

    for call in (lambda: tools.write(str(target), "x"),
                 lambda: tools.delete(str(target))):
        with pytest.raises(Exception):
            call()


def test_reading_is_also_stopped_when_latched(tmp_path):
    """The latch means *nothing runs*, not *nothing destructive runs*. An
    ambiguous state stays stopped."""
    target = tmp_path / "r.txt"
    target.write_text("x")
    tools, _ = _tools(tmp_path)
    tools.ks.trigger("test", "latched")

    with pytest.raises(Exception):
        tools.read(str(target))


# ═══ §7: every operation, every argument ═══

def test_every_operation_reaches_the_log(tmp_path):
    target = tmp_path / "logged.txt"
    tools, _ = _tools(tmp_path)

    tools.write(str(target), "x")
    tools.read(str(target))

    events = [e["event"] for e in tools.ks.log.read_all()]
    assert "file.write" in events
    assert "file.read" in events


def test_a_refusal_is_logged_too(tmp_path):
    target = tmp_path / "refused.txt"
    target.write_text("x")
    tools, _ = _tools(tmp_path, answer=False)

    with pytest.raises(PermissionError):
        tools.delete(str(target))

    events = [e["event"] for e in tools.ks.log.read_all()]
    assert "file.refused" in events


# ═══ helpers ═══

class Confirmer:
    def __init__(self, answer=True):
        self.answer = answer
        self.asked = []

    def confirm_sync(self, prompt: str) -> bool:
        self.asked.append(prompt)
        return self.answer


def _tools(tmp_path, answer=True):
    from kavach.killswitch.core import KillSwitch
    from kavach.killswitch.log import ActionLog

    ks = KillSwitch(log=ActionLog(tmp_path / "actions.jsonl"))
    confirmer = Confirmer(answer)
    return FileTools(kill_switch=ks, confirmer=confirmer), confirmer
