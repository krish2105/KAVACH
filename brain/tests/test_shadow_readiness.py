"""When is there enough shadow data to set a threshold?

Not "when there are twenty samples". Three thresholds have been set from
samples that were plentiful and unrepresentative:

  0.803  many enrolment clips, one sitting     rejected 42 of 42
  0.577  many enrolment clips, one sitting     best real score was 0.238
  0.383  six read-aloud sentences, one sitting commands scored 0.373, -0.001

Every one of those had *enough* samples. What they lacked was spread — they
were all the same act, performed the same way, in the same minute.

So readiness asks three things, and count is the least interesting:

* **enough turns** — a floor, not the test
* **spread across the range** — samples that are all identical describe one
  condition, however many there are
* **spread across time** — turns from at least two distinct sittings, because
  "one sitting" is the specific error that produced all three failures

It announces **once**. A reminder that repeats is a reminder people learn to
ignore, and this one is asking for a security decision.
"""

import pytest

from kavach.autonomy.monitors import check_shadow_readiness


def scores(values, hours_apart=0):
    """Turn a list into (similarity, timestamp) pairs."""
    return [(v, i * hours_apart * 3600.0) for i, v in enumerate(values)]


# ═══ not yet ═══

def test_too_few_samples_is_not_ready():
    assert check_shadow_readiness(scores([0.7, 0.8, 0.6], 2)) is None


def test_samples_from_one_sitting_are_not_ready():
    """The exact error that produced 0.803, 0.577 and 0.383. Twenty turns in
    ten minutes describe ten minutes."""
    one_sitting = [(0.7 + i * 0.005, i * 20.0) for i in range(20)]

    assert check_shadow_readiness(one_sitting) is None


def test_identical_scores_are_not_ready():
    """No spread means no information about where a threshold could sit,
    however many samples there are."""
    flat = [(0.75, i * 7200.0) for i in range(20)]

    assert check_shadow_readiness(flat) is None


def test_nothing_at_all_is_not_ready():
    assert check_shadow_readiness([]) is None


# ═══ ready ═══

def test_varied_samples_across_days_are_ready():
    varied = [(v, i * 7200.0) for i, v in enumerate(
        [0.42, 0.71, 0.55, 0.83, 0.61, 0.38, 0.77, 0.66,
         0.49, 0.80, 0.58, 0.72])]

    found = check_shadow_readiness(varied)

    assert found is not None
    assert found.source == "voiceprint"
    assert found.severity == "ready"


def test_the_finding_says_what_to_run():
    varied = [(0.3 + (i % 7) * 0.09, i * 7200.0) for i in range(14)]

    found = check_shadow_readiness(varied)

    assert "kavach-speaker scores" in found.detail


def test_it_reports_the_range_it_found():
    """A number with no distribution behind it is how 0.803 got written."""
    varied = [(0.3 + (i % 7) * 0.09, i * 7200.0) for i in range(14)]

    found = check_shadow_readiness(varied)

    assert "0.3" in found.detail or "0.30" in found.detail
