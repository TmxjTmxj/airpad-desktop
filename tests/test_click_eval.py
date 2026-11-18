"""The click-evaluation scoring must be correct before anyone trusts its numbers."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from click_eval import TrialScorer, make_targets  # noqa: E402


def scorer(count=3, radius=0.09, timeout=8.0):
    targets = [(0.5 + 0.1 * index, 0.5) for index in range(count)]
    return TrialScorer(targets, radius=radius, timeout=timeout)


def test_click_inside_the_target_is_a_hit():
    s = scorer()
    s.begin(0.0)
    assert s.observe(0.5, 0.5, 0, False, 1.0) == []
    assert s.observe(0.5, 0.5, 1, False, 1.5) == ['hit']
    assert (s.hits, s.unintended, s.timeouts) == (1, 0, 0)
    assert s.index == 1


def test_click_outside_the_target_is_an_unintended_activation():
    s = scorer()
    s.begin(0.0)
    assert s.observe(0.1, 0.1, 1, False, 1.0) == ['unintended']
    assert (s.hits, s.unintended) == (0, 1)
    assert s.index == 0, 'an unintended click must not consume the trial'


def test_target_timeout_is_recorded_as_a_miss():
    s = scorer(timeout=2.0)
    s.begin(0.0)
    assert s.observe(0.5, 0.5, 0, False, 2.5) == ['timeout']
    assert s.timeouts == 1 and s.index == 1


def test_a_new_click_count_is_required_for_each_click():
    s = scorer()
    s.begin(0.0)
    s.observe(0.5, 0.5, 5, False, 1.0)     # five clicks while on target
    assert s.hits == 1, 'one trial can only be completed once'


def test_dragging_is_counted_separately_from_clicking():
    s = scorer()
    s.begin(0.0)
    assert s.observe(0.5, 0.5, 0, True, 1.0) == ['drag']
    s.observe(0.5, 0.5, 0, True, 1.1)
    assert s.drag_events == 1, 'a held drag is one event, not one per update'
    assert s.hits == 0


def test_scoring_stops_after_the_last_target():
    s = scorer(count=1)
    s.begin(0.0)
    s.observe(0.5, 0.5, 1, False, 1.0)
    assert s.finished
    assert s.observe(0.5, 0.5, 2, False, 1.2) == []


def test_report_metrics():
    s = scorer(count=4)
    s.begin(0.0)
    s.observe(0.5, 0.5, 1, False, 1.0)          # hit target 0
    s.observe(0.1, 0.1, 2, False, 2.0)          # unintended
    s.observe(0.6, 0.5, 3, False, 3.0)          # hit target 1
    s.observe(0.6, 0.5, 3, False, 12.0)         # timeout target 2
    s.observe(0.6, 0.5, 3, False, 21.0)         # timeout target 3
    report = s.report()
    assert report['hits'] == 2
    assert report['unintended_clicks'] == 1
    assert report['timeouts'] == 2
    assert report['precision'] == pytest.approx(2 / 3, abs=1e-4)
    assert report['recall'] == pytest.approx(0.5, abs=1e-4)
    # Trial 1 starts when trial 0 was hit (t=1.0) and is hit at t=3.0, so 2.0 s.
    assert report['median_seconds'] == pytest.approx(1.5, abs=1e-3)


def test_radius_is_respected():
    s = scorer(count=1, radius=0.05)
    s.begin(0.0)
    assert s.observe(0.54, 0.5, 1, False, 1.0) == ['hit'], '0.04 is inside r=0.05'
    s = scorer(count=1, radius=0.05)
    s.begin(0.0)
    assert s.observe(0.56, 0.5, 1, False, 1.0) == ['unintended'], '0.06 is outside r=0.05'


def test_targets_are_seeded_and_inside_the_field():
    first = make_targets(20, 7, 0.09)
    assert first == make_targets(20, 7, 0.09), 'same seed must give the same field'
    assert first != make_targets(20, 8, 0.09)
    for x, y in first:
        assert 0.0 < x < 1.0 and 0.0 < y < 1.0
