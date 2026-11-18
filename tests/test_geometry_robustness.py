"""Recognition must survive realistic landmark jitter and relaxed finger postures.

This is the guard against the class of defect that made the app unusable on a real hand:
thresholds tuned so tightly that an ordinary pointing hand falls outside them. Every case
here is checked across 20 noise seeds; the bar is that all of them hold at 5% landmark
jitter (relative to palm scale), which is comfortably inside real MediaPipe noise.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import hand_model  # noqa: E402

from airpad.geometry import FingerStability, extract_sample  # noqa: E402

# (name, model pose, per-finger curl, expected classification)
CASES = [
    ('index straight', 'pointer', (0.0, 1.0, 1.0, 1.0), 'pointer'),
    ('index 159 degrees', 'pointer', (0.15, 1.0, 1.0, 1.0), 'pointer'),
    ('index 152 degrees', 'pointer', (0.20, 1.0, 1.0, 1.0), 'pointer'),
    ('middle 159 degrees', 'pointer', (0.0, 0.15, 1.0, 1.0), 'two'),
    ('middle half curled', 'pointer', (0.0, 0.35, 1.0, 1.0), 'pointer'),
    ('two fingers', 'two', (0.0, 0.0, 1.0, 1.0), 'two'),
    ('three fingers', 'three', (0.0, 0.0, 0.0, 1.0), 'three'),
    ('open palm', 'open', (0.0, 0.0, 0.0, 0.0), 'open'),
    ('fist', 'unknown', (1.0, 1.0, 1.0, 1.0), 'REJECTED'),
]
SEEDS = range(20)
NOISE_FLOOR = 0.05
FRAMES = 8


def classify(pose, curls, noise, seed, frames=FRAMES, **variations):
    """Run a short frame sequence through the live hysteresis path."""
    stability = FingerStability()
    result = None
    for index in range(frames):
        options = dict(pose=pose, curls=curls, palm=(0.66, 0.55), scale=0.20,
                       noise=noise, seed=seed * 100 + index)
        options.update(variations)
        hand = hand_model.as_hand(**options)
        sample, _ = extract_sample(hand, index / 30.0, 1280, 720, 1, stability)
        result = sample.pose if sample is not None else 'REJECTED'
    return result


@pytest.mark.parametrize('name,pose,curls,expected', CASES, ids=[c[0] for c in CASES])
def test_classification_survives_landmark_jitter(name, pose, curls, expected):
    for noise in (0.0, 0.01, 0.02, NOISE_FLOOR):
        wrong = [seed for seed in SEEDS
                 if classify(pose, curls, noise, seed) != expected]
        assert not wrong, f'{name} misclassified at {noise:.0%} jitter (seeds {wrong})'


def test_a_relaxed_index_engages_quickly():
    """A pointing finger near 150 degrees must not take half a second to be believed."""
    stability = FingerStability()
    frames_to_engage = None
    for frame in range(1, 13):
        hand = hand_model.as_hand(pose='pointer', curls=(0.20, 1.0, 1.0, 1.0))
        sample, _ = extract_sample(hand, frame / 30.0, 1280, 720, 1, stability)
        if sample is not None and sample.pose == 'pointer':
            frames_to_engage = frame
            break
    assert frames_to_engage is not None, 'a relaxed pointing hand never engaged'
    assert frames_to_engage <= 5, f'took {frames_to_engage} frames to engage'


def test_a_curled_hand_never_engages():
    """The mirror image of the test above: a fist must stay refused."""
    stability = FingerStability()
    for frame in range(1, 31):
        hand = hand_model.as_hand(pose='unknown', curls=(1.0, 1.0, 1.0, 1.0))
        sample, quality = extract_sample(hand, frame / 30.0, 1280, 720, 1, stability)
        assert sample is None and not quality['valid']


def test_an_open_palm_with_a_tucked_thumb_still_clutches():
    """Opening a hand normally folds the thumb against the palm, and the thumb is the
    least reliable landmark (four sub-scores over wrapped, foreshortened geometry).
    Requiring it meant the clutch silently failed: the user opened their hand to stop and
    the cursor kept moving.
    """
    from airpad.geometry import finger_scores

    hand = hand_model.as_hand(pose='open', thumb_tucked=True, width=1280, height=720)
    pts = [(x * 1280 / 720, y) for x, y, _ in hand['landmarks']]
    thumb_score = finger_scores(pts)['thumb']
    assert thumb_score < 0.62, f'the model must really tuck the thumb, got {thumb_score}'

    assert classify('open', (0.0, 0.0, 0.0, 0.0), 0.0, 0, thumb_tucked=True) == 'open'


def test_a_tucked_thumb_does_not_turn_a_point_into_an_open_hand():
    """The mirror risk: dropping the thumb requirement must not make pointing clutch."""
    assert classify('pointer', (0.0, 1.0, 1.0, 1.0), 0.0, 0, thumb_tucked=True) == 'pointer'
    assert classify('two', (0.0, 0.0, 1.0, 1.0), 0.0, 0, thumb_tucked=True) == 'two'


@pytest.mark.parametrize('rotation', [-40, -20, 0, 20, 40])
def test_classification_survives_in_plane_rotation(rotation):
    """People do not hold their hand level; the ratios must not care."""
    assert classify('pointer', (0.0, 1.0, 1.0, 1.0), 0.02, 3, rotation=rotation) == 'pointer'
    assert classify('two', (0.0, 0.0, 1.0, 1.0), 0.02, 3, rotation=rotation) == 'two'
    assert classify('open', (0.0, 0.0, 0.0, 0.0), 0.02, 3, rotation=rotation) == 'open'


@pytest.mark.parametrize('squish', [1.0, 0.85, 0.7])
def test_classification_survives_palm_foreshortening(squish):
    """A palm tilted toward the camera projects shorter; the pose must survive."""
    assert classify('pointer', (0.0, 1.0, 1.0, 1.0), 0.02, 3, squish=squish) == 'pointer'
    assert classify('open', (0.0, 0.0, 0.0, 0.0), 0.02, 3, squish=squish) == 'open'


@pytest.mark.parametrize('scale', [0.06, 0.12, 0.20, 0.35, 0.55])
def test_classification_is_independent_of_hand_distance(scale):
    """Everything is a ratio of palm scale, so sitting closer must change nothing."""
    assert classify('pointer', (0.0, 1.0, 1.0, 1.0), 0.02, 3, scale=scale) == 'pointer'
    assert classify('unknown', (1.0, 1.0, 1.0, 1.0), 0.02, 3, scale=scale) == 'REJECTED'


def test_pinch_ratio_is_scale_invariant():
    """A pinch at arm's length and up close must read the same."""
    ratios = []
    for scale in (0.06, 0.20, 0.55):
        hand = hand_model.as_hand(pose='pointer', pinch=0.35, scale=scale)
        sample, quality = extract_sample(hand, 0.0, 1280, 720, 1)
        assert quality['valid'], quality['reason']
        ratios.append(sample.pinch)
    assert max(ratios) - min(ratios) < 0.02, ratios


def test_scroll_is_opt_in_and_two_fingers_point_by_default():
    """The default profile must never let a relaxed middle finger freeze the cursor."""
    from dataclasses import replace

    from airpad.core import GestureEngine
    from airpad.config import Profile

    def run(profile):
        engine = GestureEngine(profile)
        events = []
        for frame in range(60):
            sample = hand_sample(frame, index=(0.5 + max(0, frame - 20) * 0.002, 0.3))
            events += engine.step(sample, sample.t)
        return engine, events

    def hand_sample(frame, **overrides):
        from airpad.types import HandSample
        values = dict(t=frame / 30.0, index=(0.5, 0.3), palm=(0.5, 0.6), scale=0.2,
                      pinch=0.9, pose='two', two_center=(0.5, 0.3), two_distance=0.3,
                      track_id=1, valid=True)
        values.update(overrides)
        return HandSample(**values)

    engine, events = run(Profile())
    assert any(action.kind == 'move' for action in events)
    assert not any(action.kind in ('wheel', 'zoom') for action in events)
    assert engine.state == 'TRACKING'

    engine, events = run(Profile(two_finger_scroll=True))
    assert not any(action.kind == 'move' for action in events)
