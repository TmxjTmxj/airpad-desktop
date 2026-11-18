"""Compare smoothing baselines on one identical trajectory (Stage 3 evidence).

The design spec asks for EMA / One Euro / Kalman to be compared on the same fixture and
for the choice not to be made from a static curve alone. This tool runs a scripted
trajectory with a still phase and a constant-velocity ramp through four variants:

    raw        no smoothing (the accumulated palm-unit displacement)
    ema        fixed-alpha exponential moving average
    one_euro   the shipped speed-adaptive filter + adaptive stop zone (airpad/filtering.py)
    kalman     1-D constant-velocity Kalman per axis

Variants emit integer counts with a sub-pixel residual. Since the tremor fix, the shipped
pipeline also has a radial stop zone; the other baselines do not. This compares the whole
shipped pointer pipeline with simple smoothing baselines, not isolated filter superiority.
Metrics are reported per phase: jitter while still, lag while moving, and total counts.

This is a baseline comparison on a scripted path with fixed parameters. It is evidence
for the Stage 3 exit criterion, not a parameter-tuning study, and it says nothing about
real-hand feel.

Usage:
    python tools/filter_compare.py [--output artifacts/filter-comparison.json]
"""
import argparse
import json
import math
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from airpad.filtering import RelativeMotion  # noqa: E402

FPS = 30.0
DT = 1.0 / FPS
SENSITIVITY = 1000.0
MIN_CUTOFF = 1.5
EMA_ALPHA = 0.35
KALMAN_PROCESS = 1e-4
KALMAN_MEASURE = 1e-2
STILL_FRAMES = 60
RAMP_FRAMES = 60
RAMP_SPEED = 0.15          # palm units per second
NOISE_SIGMA = 0.004        # palm units of stillness noise


def trajectory(seed=11):
    """A still phase followed by a constant-velocity ramp, in palm units."""
    generator = random.Random(seed)
    points = []
    position = 0.0
    for index in range(STILL_FRAMES):
        points.append((position + generator.gauss(0.0, NOISE_SIGMA), index * DT))
    for index in range(RAMP_FRAMES):
        position += RAMP_SPEED * DT
        points.append((position, (STILL_FRAMES + index) * DT))
    return points


class RawSmoother:
    def __call__(self, sample):
        return sample


class EmaSmoother:
    """Fixed-alpha exponential moving average."""

    def __init__(self, alpha=EMA_ALPHA):
        self.alpha = alpha
        self.value = None

    def __call__(self, sample):
        if self.value is None:
            self.value = sample
        else:
            self.value += self.alpha * (sample - self.value)
        return self.value


class KalmanSmoother:
    """Constant-velocity Kalman filter on the displacement axis."""

    def __init__(self, process=KALMAN_PROCESS, measure=KALMAN_MEASURE):
        self.q, self.r = process, measure
        self.position = None
        self.velocity = 0.0
        self.p00, self.p01, self.p10, self.p11 = 1.0, 0.0, 0.0, 1.0

    def __call__(self, sample, dt=DT):
        if self.position is None:
            self.position = sample
            return sample
        # Predict
        self.position += self.velocity * dt
        self.p00 += dt * (self.p10 + self.p01) + dt * dt * self.p11 + self.q
        self.p01 += dt * self.p11
        self.p10 += dt * self.p11
        self.p11 += self.q
        # Update
        gain_position = self.p00 / (self.p00 + self.r)
        gain_velocity = self.p10 / (self.p00 + self.r)
        residual = sample - self.position
        self.position += gain_position * residual
        self.velocity += gain_velocity * residual
        self.p00 = (1 - gain_position) * self.p00
        self.p01 = (1 - gain_position) * self.p01
        self.p10 -= gain_velocity * self.p00
        self.p11 -= gain_velocity * self.p01
        return self.position


def smooth_series(name, points):
    """Return the smoothed displacement series for one variant."""
    if name == 'one_euro':
        # The shipped filter is driven through its real interface; it returns integer
        # counts directly, so its own output stage is used as-is.
        motion = RelativeMotion(SENSITIVITY, MIN_CUTOFF)
        return [motion.update((position, 0.0), 1.0, stamp)[0] for position, stamp in points]
    smoother = {'raw': RawSmoother(), 'ema': EmaSmoother(), 'kalman': KalmanSmoother()}[name]
    counts, residual, previous = [], 0.0, None
    for position, _ in points:
        smoothed = smoother(position)
        if previous is None:
            previous = smoothed
            counts.append(0)
            continue
        value = (smoothed - previous) * SENSITIVITY + residual
        integer = math.trunc(value)
        residual = value - integer
        counts.append(integer)
        previous = smoothed
    return counts


def metrics(counts):
    still = counts[1:STILL_FRAMES]
    ramp = counts[STILL_FRAMES:]
    jitter = math.sqrt(sum(value * value for value in still) / len(still)) if still else 0.0
    return {
        'still_jitter_rms_counts': round(jitter, 3),
        'still_peak_counts': max((abs(value) for value in still), default=0),
        'ramp_median_counts': round(statistics.median(ramp), 2) if ramp else 0.0,
        'total_counts': sum(counts),
        'frames': len(counts),
    }


def ramp_lag_vs_raw(counts, raw_counts):
    """Mean counts the variant trails raw by over the ramp."""
    deficits = [raw_counts[i] - counts[i] for i in range(STILL_FRAMES, len(counts))]
    return round(sum(deficits) / len(deficits), 3) if deficits else 0.0


def main():
    parser = argparse.ArgumentParser(description='Smoothing baseline comparison.')
    parser.add_argument('--output', default='artifacts/filter-comparison.json')
    args = parser.parse_args()

    points = trajectory()
    series = {name: smooth_series(name, points)
              for name in ('raw', 'ema', 'one_euro', 'kalman')}
    raw_counts = series['raw']
    variants = {}
    for name, counts in series.items():
        row = metrics(counts)
        row['ramp_lag_vs_raw_counts'] = ramp_lag_vs_raw(counts, raw_counts)
        variants[name] = row

    document = {
        'comparison': 'filter-comparison',
        'trajectory': {
            'source': f'scripted: {STILL_FRAMES} still frames (gaussian sigma '
                      f'{NOISE_SIGMA} palm units) then {RAMP_FRAMES} frames at '
                      f'{RAMP_SPEED} palm units/s',
            'fps': FPS, 'sensitivity': SENSITIVITY, 'min_cutoff_hz': MIN_CUTOFF,
            'ema_alpha': EMA_ALPHA, 'kalman_process': KALMAN_PROCESS,
            'kalman_measure': KALMAN_MEASURE, 'seed': 11,
        },
        'metric_notes': {
            'still_jitter_rms_counts': 'RMS emitted counts while still: lower is steadier',
            'still_peak_counts': 'largest single-frame count while still: worst visible twitch',
            'ramp_median_counts': 'median counts per frame during constant-velocity motion',
            'ramp_lag_vs_raw_counts': 'mean counts the variant trails raw by during the ramp',
            'total_counts': 'sum over the path: a filter must not lose or invent travel',
        },
        'variants': variants,
        'caveat': ('Scripted path, fixed parameters. Evidence for the Stage 3 exit criterion '
                   'only: it does not establish feel on a real hand. The one_euro variant '
                   'includes the shipped adaptive stop zone; other baselines do not, so '
                   'this is a pipeline comparison, not an isolated filter comparison.'),
    }
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2), encoding='utf-8')
    print(json.dumps(variants, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
