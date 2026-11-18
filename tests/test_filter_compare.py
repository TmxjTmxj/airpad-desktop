"""The filter comparison must behave sensibly, or its numbers mean nothing."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import filter_compare as fc  # noqa: E402


@pytest.fixture(scope='module')
def series():
    points = fc.trajectory()
    return {name: fc.smooth_series(name, points) for name in ('raw', 'ema', 'one_euro', 'kalman')}


def test_trajectory_is_deterministic_and_shaped_as_declared():
    points = fc.trajectory()
    assert points == fc.trajectory()
    assert len(points) == fc.STILL_FRAMES + fc.RAMP_FRAMES
    assert points != fc.trajectory(seed=12)


def test_smoothing_reduces_still_jitter(series):
    jitter = {name: fc.metrics(counts)['still_jitter_rms_counts'] for name, counts in series.items()}
    assert jitter['raw'] > jitter['ema'] > 0
    assert jitter['raw'] > jitter['one_euro'] >= 0
    assert jitter['raw'] > jitter['kalman'] > 0


def test_no_variant_loses_meaningful_travel(series):
    totals = {name: fc.metrics(counts)['total_counts'] for name, counts in series.items()}
    raw = totals['raw']
    for name, total in totals.items():
        assert abs(total - raw) / raw < 0.10, f'{name} travelled {total} vs raw {raw}'


def test_constant_velocity_is_tracked_by_every_variant(series):
    medians = {name: fc.metrics(counts)['ramp_median_counts'] for name, counts in series.items()}
    assert len(set(medians.values())) == 1, f'filters disagree on steady-state speed: {medians}'


def test_lag_is_reported_relative_to_raw(series):
    raw = series['raw']
    for name, counts in series.items():
        lag = fc.ramp_lag_vs_raw(counts, raw)
        if name == 'raw':
            assert lag == 0.0
        else:
            # A causal smoother must trail raw slightly and never lead it.
            assert 0 <= lag < 1.0, f'{name} lag {lag}'
