"""The watcher that makes Phase 31 reachable.

`claudecode.py` can read a transcript; without this it is called by nothing.
Three modules built today worked perfectly and were wired to nothing —
`browser.py`, the file tools, and the endpointing fix applied to the copy that
does not run. Each was found by asking whether the code was *reached*, not
whether it *worked*. Asked first here.
"""

import json

from kavach.observe.watcher import SessionWatcher


def result_line(text) -> str:
    return json.dumps({
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": str(text)}]},
    }) + "\n"


def session(root, name="s.jsonl", body=""):
    project = root / "proj"
    project.mkdir(exist_ok=True)
    path = project / name
    path.write_text(body)
    return path


# ═══ it starts at the end ═══

def test_existing_history_is_not_replayed(tmp_path):
    """A watcher that replayed the transcript would announce every test run
    since the session began, the moment it started."""
    session(tmp_path, body=result_line("1151 passed"))
    watcher = SessionWatcher(root=tmp_path)

    assert watcher.poll() == []


def test_new_lines_after_it_starts_are_narrated(tmp_path):
    path = session(tmp_path, body=result_line("nothing interesting"))
    watcher = SessionWatcher(root=tmp_path)
    watcher.poll()

    with path.open("a") as handle:
        handle.write(result_line("3 failed, 100 passed"))

    narrations = watcher.poll()
    assert len(narrations) == 1
    assert "fail" in narrations[0].text.lower()


# ═══ it does not repeat itself ═══

def test_the_same_news_twice_is_said_once(tmp_path):
    """Repeating a narration is how a notification becomes background noise,
    which is the same failure as confirming everything."""
    path = session(tmp_path, body="")
    watcher = SessionWatcher(root=tmp_path)
    watcher.poll()

    with path.open("a") as handle:
        handle.write(result_line("1151 passed"))
        handle.write(result_line("1151 passed"))

    assert len(watcher.poll()) == 1


def test_a_change_in_the_news_is_said(tmp_path):
    path = session(tmp_path, body="")
    watcher = SessionWatcher(root=tmp_path)
    watcher.poll()

    with path.open("a") as handle:
        handle.write(result_line("1151 passed"))
        handle.write(result_line("2 failed, 1149 passed"))

    assert len(watcher.poll()) == 2


# ═══ it survives the file moving under it ═══

def test_a_new_session_starts_from_its_end(tmp_path):
    session(tmp_path, "old.jsonl", result_line("1 passed"))
    watcher = SessionWatcher(root=tmp_path)
    watcher.poll()

    import time
    time.sleep(0.01)
    session(tmp_path, "new.jsonl", result_line("999 passed"))

    assert watcher.poll() == [], "a new session replayed its history"


def test_a_truncated_file_does_not_reread_it(tmp_path):
    path = session(tmp_path, body=result_line("1151 passed") * 3)
    watcher = SessionWatcher(root=tmp_path)
    watcher.poll()

    path.write_text("")

    assert watcher.poll() == []


def test_a_missing_root_is_quiet(tmp_path):
    assert SessionWatcher(root=tmp_path / "nope").poll() == []


def test_a_partial_line_is_not_guessed_at(tmp_path):
    """Tailing a file being appended to means catching half-written lines.
    Normal, and not something to interpret."""
    path = session(tmp_path, body="")
    watcher = SessionWatcher(root=tmp_path)
    watcher.poll()

    with path.open("a") as handle:
        handle.write('{"type": "user", "mess')

    assert watcher.poll() == []
