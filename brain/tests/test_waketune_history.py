"""Every calibration attempt leaves a record, including the ones that fail.

A refused calibration wrote nothing at all — correct about the threshold, and
useless afterwards. Three times this session the only copy of a measurement was
a terminal that had scrolled away, and comparing v2 against v3 on the user's
real voice needed numbers nobody could retrieve without recording them again.

Scores, not audio. §7 is about audio: the raw takes are still never written.
"""

import json

import pytest

from kavach.voice import waketune
from kavach.voice.waketune import choose_threshold, record_attempt


@pytest.fixture
def history(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    monkeypatch.setattr(waketune, "HISTORY_PATH", path)
    return path


def test_a_refused_calibration_is_still_recorded(history):
    """The case that mattered and was thrown away."""
    cal = choose_threshold([0.041, 0.089, 0.06], [0.01, 0.02])

    record_attempt(cal, model_name="kavach_v3", saved=False)

    row = json.loads(history.read_text().strip())
    assert row["separated"] is False
    assert row["saved"] is False
    assert row["positives"] == [0.041, 0.089, 0.06]
    assert row["model"] == "kavach_v3"


def test_a_successful_calibration_is_recorded_too(history):
    cal = choose_threshold([0.80, 0.78], [0.02])

    record_attempt(cal, model_name="kavach_v3", saved=True)

    assert json.loads(history.read_text().strip())["saved"] is True


def test_attempts_accumulate_so_models_can_be_compared(history):
    record_attempt(choose_threshold([0.04, 0.05], [0.01]), "kavach_v2", False)
    record_attempt(choose_threshold([0.40, 0.44], [0.02]), "kavach_v3", False)

    rows = [json.loads(l) for l in history.read_text().splitlines()]

    assert [r["model"] for r in rows] == ["kavach_v2", "kavach_v3"]
    assert rows[1]["margin"] > rows[0]["margin"], \
        "the whole point is seeing whether a retrain moved the number"


def test_no_audio_is_ever_written(history):
    """§7. The scores are a measurement; the takes are not."""
    record_attempt(choose_threshold([0.04], [0.01]), "kavach_v3", False)

    row = json.loads(history.read_text().strip())
    for key in row:
        assert "audio" not in key and "wav" not in key and "clip" not in key


def test_a_broken_history_never_blocks_calibration(history, monkeypatch):
    """Recording is a convenience. It must not be able to fail the run that
    matters."""
    history.parent.chmod(0o500)
    try:
        record_attempt(choose_threshold([0.8], [0.01]), "kavach_v3", True)
    finally:
        history.parent.chmod(0o700)
