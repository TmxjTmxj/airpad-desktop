"""The synthetic hand model must extract to exactly the features it claims.

Without this guard the fixture would be meaningless: it would assert gesture behaviour
for a hand whose geometry never actually said what the script intended.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import hand_model  # noqa: E402

from airpad.geometry import extract_sample  # noqa: E402


def features(pose='pointer', pinch=0.90, **kwargs):
    hand = hand_model.as_hand(pose, pinch, **kwargs)
    return extract_sample(hand, 0.0, kwargs.get('width', 640), kwargs.get('height', 480), 1)


@pytest.mark.parametrize('pose', ['open', 'pointer', 'two', 'three'])
def test_every_usable_pose_is_geometrically_valid(pose):
    sample, quality = features(pose)
    assert quality['valid'], quality['reason']
    assert sample is not None
    assert sample.pose == pose


def test_a_closed_fist_is_refused_rather_than_guessed():
    """A fist carries no intent; the app must release instead of inventing a pose."""
    sample, quality = features('unknown')
    assert sample is None
    assert not quality['valid']
    assert 'pointing or open' in quality['reason']


def test_scale_pinch_and_palm_are_reproduced_exactly():
    sample, _ = features('pointer', pinch=0.32, palm=(0.66, 0.58), scale=0.24)
    assert sample.scale == pytest.approx(0.24, abs=1e-6)
    assert sample.pinch == pytest.approx(0.32, abs=1e-5)
    assert sample.palm[0] == pytest.approx(0.66, abs=1e-5)
    assert sample.palm[1] == pytest.approx(0.58, abs=1e-5)


def test_open_palm_reads_as_not_pinching():
    from airpad.config import Profile
    profile = Profile()
    sample, _ = features('open')
    assert sample.pinch > profile.pinch_open, \
        'an open palm must sit above the pinch-open threshold'


def test_index_tip_is_above_the_palm_for_a_pointer_hand():
    sample, _ = features('pointer')
    assert sample.index[1] < sample.palm[1]


def test_two_finger_spread_changes_the_separation():
    narrow, _ = features('two', two_spread=6.0)
    wide, _ = features('two', two_spread=22.0)
    assert wide.two_distance > narrow.two_distance


def test_curled_fingers_are_not_reported_as_extended():
    pointer, _ = features('pointer')
    open_hand, _ = features('open')
    assert pointer.pose == 'pointer' and open_hand.pose == 'open'


def test_unknown_pose_is_rejected_loudly():
    with pytest.raises(ValueError):
        hand_model.landmarks('fist')


def test_landmarks_stay_inside_the_image_bounds():
    for pose in ('open', 'pointer', 'two', 'three', 'unknown'):
        for x, y, _ in hand_model.landmarks(pose):
            assert -0.03 <= x <= 1.03 and -0.03 <= y <= 1.03
