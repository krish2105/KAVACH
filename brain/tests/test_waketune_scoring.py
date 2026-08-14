"""How a wake take is scored — the ruler, before anything is measured with it.

Every judgement about the wake word rests on this: whether v3 beat v2, whether
a calibration separates, whether retraining on real speech helped at all. If
the ruler is wrong, so is every number downstream — and this project has been
caught by exactly that twice already (the augmentation that never ran, and a
latency figure taken against an app that was already open).

CLAUDE.md records the suspicion that started this:

    the five takes spanned 0.005 to 0.115, a 20x spread. TAKE_SECONDS = 2.6
    with a sliding 2s window means a take that starts late is scored on a
    clipped word, so some of that spread may be timing rather than voice.

Half right. `best_score` already slides — max over every 2s window, hopping
160ms — so a late start *inside* the take is handled. What is not handled is a
start so late that the word runs off the **end** of the recording: 2.6s of tape
against a 2.0s window leaves 0.6s of slack, and speaking a second after the
tone spends all of it. No window then contains the whole word, the take scores
near zero, and nothing says why.

A take that was clipped is not evidence about a voice or a model. It has to be
visible, and it has to be re-recordable.
"""

import numpy as np
import pytest

from kavach.voice.wake import WINDOW_SAMPLES
from kavach.voice.waketune import (
    TAKE_SECONDS,
    best_score,
    score_take,
)

SAMPLE_RATE = 16_000

#: The "word": a burst a detector can recognise wherever it appears.
WORD_SAMPLES = int(0.6 * SAMPLE_RATE)


class MarkerDetector:
    """Scores a window by how much of the marker burst it contains.

    Stands in for the ONNX model so the ruler can be tested without one — and
    so a *clipped* word is unambiguously distinguishable from a quiet one,
    which is the whole point of these tests.
    """

    def score_window(self, window: np.ndarray) -> float:
        if len(window) == 0:
            return 0.0
        present = float(np.count_nonzero(window > 0.5))
        return min(1.0, present / WORD_SAMPLES)


def take_with_word_at(offset_s: float, length_s: float = TAKE_SECONDS) -> np.ndarray:
    """A silent take with the marker word starting at `offset_s`, truncated to
    the tape length exactly as a real recording would be."""
    audio = np.zeros(int(length_s * SAMPLE_RATE), dtype=np.float32)
    start = int(offset_s * SAMPLE_RATE)
    audio[start : start + WORD_SAMPLES] = 1.0
    return audio


# ═══ the slide, which already worked ═══

def test_the_word_scores_the_same_wherever_it_sits_in_the_take():
    """The property `best_score` exists for. Worth a test even though it
    already holds: a future 'optimisation' to a single window would silently
    reintroduce the 20x spread this was blamed for."""
    early = best_score(MarkerDetector(), take_with_word_at(0.1))
    middle = best_score(MarkerDetector(), take_with_word_at(0.6))

    assert early == pytest.approx(1.0)
    assert middle == pytest.approx(1.0)


# ═══ the clipping the slide cannot fix ═══

#: The tape length that shipped until now, kept here on purpose: these two
#: tests are about clipping, and they must keep testing clipping even as
#: TAKE_SECONDS grows.
OLD_TAKE_SECONDS = 2.6


def test_a_word_that_runs_off_the_end_scores_low():
    """Not a bug — a fact about the tape. Starting 2.2s into a 2.6s take
    leaves 0.4s of a 0.6s word, and no window can contain what was never
    recorded."""
    clipped = best_score(MarkerDetector(),
                         take_with_word_at(2.2, OLD_TAKE_SECONDS))

    assert clipped < 0.9, "the word was cut off; a high score would be wrong"


def test_a_clipped_take_says_so():
    """The part that was missing. A take scored 0.05 because the speaker was
    slow is indistinguishable, in the numbers, from one scored 0.05 because
    the model is deaf to that voice — and those have opposite remedies."""
    result = score_take(MarkerDetector(),
                        take_with_word_at(2.2, OLD_TAKE_SECONDS))

    assert result.clipped is True
    assert result.offset_s > 0.0


def test_a_well_placed_take_is_not_flagged():
    result = score_take(MarkerDetector(), take_with_word_at(0.3))

    assert result.clipped is False
    assert result.score == pytest.approx(1.0)


def test_the_offset_reports_where_the_best_window_landed():
    """So a recording session can tell a speaker they are late, while they are
    still sitting there, instead of after five takes have been wasted."""
    result = score_take(MarkerDetector(), take_with_word_at(1.0))

    # The best window is the last one that still contains the whole word.
    assert 0.0 <= result.offset_s <= 1.0


def test_score_take_agrees_with_best_score():
    """Two ways to ask the same question must not drift apart."""
    for offset in (0.1, 0.6, 1.2, 2.2):
        audio = take_with_word_at(offset, OLD_TAKE_SECONDS)
        assert score_take(MarkerDetector(), audio).score == pytest.approx(
            best_score(MarkerDetector(), audio)
        )


# ═══ the tape must be long enough to hold a slow start ═══

def test_the_take_leaves_room_for_a_late_start():
    """0.6s of slack is less than a human reaction time plus the word. The
    tape has to hold the window *and* a start that drifts, or the recorder is
    measuring reflexes."""
    window_s = WINDOW_SAMPLES / SAMPLE_RATE
    slack = TAKE_SECONDS - window_s

    assert slack >= 1.5, (
        f"only {slack:.1f}s of slack after the {window_s:.1f}s window — a take "
        f"that starts late runs off the end and scores as a bad voice"
    )


def test_a_late_start_still_fits_on_the_longer_tape():
    """The regression this length exists to prevent."""
    late = score_take(MarkerDetector(), take_with_word_at(1.5))

    assert late.score == pytest.approx(1.0)
    assert late.clipped is False
