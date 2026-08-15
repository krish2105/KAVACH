"""`kavach-wakecheck` — the diagnostic that makes "it didn't fire" answerable.

The wake word did not fire for the user, and there was no way to find out
why. §7 means the daemon never logs the transcript of a burst that did not
match, so the only evidence available was "it didn't work" — which cannot
distinguish *the model never heard you* from *the model heard you and spelled
it in a way the matcher does not know*. Those have completely different
fixes, and guessing between them is how four ONNX models got trained.

**Opt-in, printed, never stored.** The same shape `kavach-waketune` has for
the ONNX path. The tests below assert the storing part rather than trusting
it: a diagnostic that quietly kept a transcript of everything said near the
microphone would be exactly the ambient capture this project cut.
"""

import inspect

import pytest

from kavach.voice import wakecheck
from kavach.voice.wakecheck import closest_target


# ═══ it stores nothing ═══

def test_it_writes_nothing_anywhere():
    """Asserted on the code, because "currently does not" is not the claim
    being made to the user in its own help text."""
    from tests._sourcecheck import code_text

    source = code_text(inspect.getmodule(wakecheck))
    for forbidden in ("write_text", "open", "ActionLog", "MemoryStore",
                      "remember", "sf.write", "savez", "dump"):
        assert forbidden not in source, (
            f"{forbidden} in wakecheck — it promises to store nothing"
        )


def test_it_does_not_touch_the_action_log():
    source = inspect.getsource(wakecheck)
    assert "log.append" not in source


# ═══ the near-miss report is the whole point ═══

def test_a_close_spelling_is_reported_with_its_score():
    """"no match" alone sends you to retrain a model when the real fix is a
    one-line addition to WAKE_TARGETS."""
    word, target, ratio = closest_target("coverage")

    assert word == "coverage"
    assert target in ("kavach", "kawach", "kavatch", "gavaj", "gauj", "vajah")
    assert 0.0 < ratio < 1.0


def test_an_exact_hit_scores_high():
    _, _, ratio = closest_target("kavach please open notes")
    assert ratio == pytest.approx(1.0)


def test_nothing_close_reports_nothing():
    """Distinguishing "heard the wrong word" from "heard no word like it" is
    the distinction the whole tool exists to make."""
    word, _, ratio = closest_target("the weather is pleasant today")
    assert ratio < 0.7, (word, ratio)


def test_short_words_are_ignored_the_same_way_the_matcher_does():
    """`matches_wake` skips words under 4 characters because fuzzy matching
    produces nonsense there. A diagnostic that scored them would report a
    near-miss the real matcher can never act on."""
    word, _, _ = closest_target("go to it")
    assert word == ""


def test_empty_and_none_are_survivable():
    assert closest_target("") == ("", "", 0.0)
    assert closest_target(None) == ("", "", 0.0)


# ═══ it is wired ═══

def test_the_command_exists():
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
    assert scripts.get("kavach-wakecheck") == "kavach.voice.wakecheck:main"


def test_it_uses_the_microphone_correctly():
    """`MicStream` has start()/stop() and no context manager. `with
    MicStream()` raises AttributeError at runtime and passes every test that
    does not actually run it — which is how a broken command reaches a user."""
    import ast

    from kavach.voice.mic import MicStream

    assert hasattr(MicStream, "start") and hasattr(MicStream, "stop")

    # Parsed, not grepped. The comment in wakecheck.py explains the bug by
    # naming `with MicStream()`, and a string search cannot tell an
    # explanation from an occurrence — the fourth time that has bitten here.
    # A `code_text` search cannot work either: it emits bare identifiers, so
    # "with MicStream()" matches nothing for any input, which is a test that
    # cannot go red.
    tree = ast.parse(inspect.getsource(wakecheck))
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                call = item.context_expr
                assert getattr(getattr(call, "func", None), "id", None) != "MicStream", (
                    f"line {node.lineno}: MicStream used as a context manager, "
                    f"which raises AttributeError at runtime"
                )

    calls = {ast.unparse(n.func) for n in ast.walk(tree)
             if isinstance(n, ast.Call) and hasattr(n, "func")}
    assert "mic.start" in calls and "mic.stop" in calls


def test_it_pins_the_language_like_the_detector_does():
    """auto lets the multilingual model answer in Devanagari, which the
    matcher cannot read. A diagnostic that differs from the detector reports
    on something the user is not running."""
    assert 'language="en"' in inspect.getsource(wakecheck)
