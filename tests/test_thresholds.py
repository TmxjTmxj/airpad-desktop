"""Thresholds derived from a hand must actually describe that hand.

The point of measuring is that a fixed threshold is a guess about somebody else's hand.
These tests use synthetic score distributions, including a deliberately weak-scoring hand
where the built-in default would reject a perfectly good pointing finger.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import hand_model  # noqa: E402

from airpad.geometry import (FINGER_ORDER, FingerStability, classify_pose,  # noqa: E402
                            extract_sample, finger_bands)
from airpad.thresholds import DEFAULT_THRESHOLD, derive_finger_thresholds  # noqa: E402

EXTENDED = {'thumb': 0.94, 'index': 0.90, 'middle': 0.92, 'ring': 0.90, 'pinky': 0.88}
CURLED = {'thumb': 0.14, 'index': 0.20, 'middle': 0.05, 'ring': 0.05, 'pinky': 0.05}


def rows(scores, count=20, jitter=0.01):
    out = []
    for index in range(count):
        drift = jitter * ((index % 5) - 2) / 2
        out.append({name: max(0.0, min(1.0, value + drift)) for name, value in scores.items()})
    return out


def phases(**overrides):
    open_scores = overrides.get('open', rows(EXTENDED))
    fist_scores = overrides.get('fist', rows(CURLED))
    pointer_scores = overrides.get('pointer', rows({**CURLED, 'index': 0.90}))
    return open_scores, fist_scores, pointer_scores


def test_a_clean_hand_yields_measured_thresholds_in_order():
    thresholds, report = derive_finger_thresholds(*phases())
    assert len(thresholds) == 5
    assert report['measured_fingers'] == 5
    assert not report['defaults_used']
    for finger, value in zip(FINGER_ORDER, thresholds):
        assert 0.28 <= value <= 0.82
        assert report['per_finger'][finger]['source'] == 'measured'


def test_threshold_sits_between_the_two_postures():
    thresholds, report = derive_finger_thresholds(*phases())
    for finger, value in zip(FINGER_ORDER, thresholds):
        row = report['per_finger'][finger]
        assert row['curled_median'] < value < row['extended_median'], finger


def test_a_weak_hand_gets_a_lower_threshold_than_the_default():
    """The case that made the app unusable: a hand that simply scores lower."""
    weak_extended = {name: value * 0.65 for name, value in EXTENDED.items()}
    weak_curled = {name: value * 1.6 for name, value in CURLED.items()}
    thresholds, report = derive_finger_thresholds(
        rows(weak_extended), rows(weak_curled), rows({**weak_curled, 'index': weak_extended['index']}))
    index_threshold = thresholds[FINGER_ORDER.index('index')]
    assert index_threshold < DEFAULT_THRESHOLD
    assert report['per_finger']['index']['source'] == 'measured'


def test_overlapping_postures_fall_back_to_the_default():
    """If a finger looks the same open and closed, do not invent a threshold for it."""
    flat = {name: 0.5 for name in FINGER_ORDER}
    thresholds, report = derive_finger_thresholds(rows(flat), rows(flat), rows(flat))
    assert report['measured_fingers'] == 0
    assert set(report['defaults_used']) == set(FINGER_ORDER)
    assert all(value == DEFAULT_THRESHOLD for value in thresholds)
    assert 'not separable' in report['per_finger']['index']['reason']


def test_a_narrow_gap_is_still_measured_not_defaulted():
    """A hand whose postures sit close together is exactly the hand a fixed 0.62 fails;
    reverting to the default here would undo the measurement."""
    extended = {name: 0.52 for name in FINGER_ORDER}
    curled = {name: 0.40 for name in FINGER_ORDER}
    thresholds, report = derive_finger_thresholds(rows(extended, jitter=0.0),
                                                 rows(curled, jitter=0.0),
                                                 rows({**curled, 'index': 0.52}, jitter=0.0))
    assert report['measured_fingers'] == 5, report['defaults_used']
    assert report['narrow_fingers'], 'the report must flag a narrow gap'
    index = report['per_finger']['index']
    assert index['confidence'] == 'narrow'
    assert 0.40 < thresholds[FINGER_ORDER.index('index')] < 0.52


def test_a_narrow_threshold_still_separates_that_hand():
    """The measured value must actually classify the two postures it came from, and the
    marginal band must be anchored above this hand's curled median - scaling it from the
    threshold alone let a curled 0.40 drift into the band and latch as extended."""
    extended = {name: 0.52 for name in FINGER_ORDER}
    curled = {name: 0.40 for name in FINGER_ORDER}
    thresholds, report = derive_finger_thresholds(rows(extended, jitter=0.0),
                                                 rows(curled, jitter=0.0),
                                                 rows({**curled, 'index': 0.52}, jitter=0.0))
    marginals = report['marginals']

    stability = FingerStability(thresholds, marginals)
    for _ in range(4):
        extended_state = stability.update(extended)
    assert all(extended_state.values()), 'a 0.52 hand must read as extended'

    stability = FingerStability(thresholds, marginals)
    for _ in range(4):
        curled_state = stability.update(curled)
    assert not any(curled_state.values()), 'and its 0.40 fist must not'


def test_the_marginal_band_is_anchored_above_the_curled_median():
    extended = {name: 0.90 for name in FINGER_ORDER}
    curled = {name: 0.30 for name in FINGER_ORDER}
    thresholds, report = derive_finger_thresholds(rows(extended, jitter=0.0),
                                                 rows(curled, jitter=0.0),
                                                 rows({**curled, 'index': 0.90}, jitter=0.0))
    bands = finger_bands(thresholds, report['marginals'])
    for finger in FINGER_ORDER:
        assert bands[finger]['marginal'] > 0.30, finger
        assert bands[finger]['marginal'] <= bands[finger]['strong'], finger


def test_too_few_samples_fall_back_to_the_default():
    thresholds, report = derive_finger_thresholds(rows(EXTENDED, count=3),
                                                 rows(CURLED, count=3),
                                                 rows(CURLED, count=3))
    assert report['measured_fingers'] == 0
    assert 'not enough' in report['per_finger']['thumb']['reason']


def test_empty_input_is_safe():
    thresholds, report = derive_finger_thresholds([], [], [])
    assert len(thresholds) == 5
    assert report['measured_fingers'] == 0


def test_bands_scale_with_the_measured_threshold():
    default_bands = finger_bands()
    measured_bands = finger_bands((0.45,) * 5)
    assert measured_bands['index']['strong'] == pytest.approx(0.45)
    assert measured_bands['index']['marginal'] < default_bands['index']['marginal']
    assert measured_bands['index']['stateless'] < default_bands['index']['stateless']


def test_a_measured_threshold_engages_a_low_scoring_finger():
    """A finger scoring 0.50 is rejected by the default and accepted when measured."""
    scores = {name: 0.50 for name in FINGER_ORDER}

    default = FingerStability()
    for _ in range(2):
        extended = default.update(scores)
    assert not any(extended.values()), 'the default must not call 0.50 extended yet'

    measured = FingerStability((0.45,) * 5)
    for _ in range(2):
        extended = measured.update(scores)
    assert all(extended.values())


def test_classify_pose_respects_measured_deliberate_levels():
    extended = {name: name in ('index', 'middle') for name in FINGER_ORDER}
    scores = {'thumb': 0.1, 'index': 0.9, 'middle': 0.70, 'ring': 0.05, 'pinky': 0.05}
    assert classify_pose(extended, scores) == 'pointer', 'default needs 0.75 for a two'
    assert classify_pose(extended, scores, finger_bands((0.55,) * 5)) == 'two'


def test_an_extended_index_never_yields_an_unusable_pose():
    """The bug this guards: a middle finger in the 0.62-0.75 band used to classify as
    'unknown', which released control entirely whenever the finger hovered there."""
    scores = {'thumb': 0.1, 'index': 0.90, 'middle': 0.70, 'ring': 0.05, 'pinky': 0.05}
    for middle_score in [x / 100 for x in range(0, 101)]:
        scores['middle'] = middle_score
        for middle_extended in (False, True):
            extended = {name: name in ('index',) for name in FINGER_ORDER}
            extended['middle'] = middle_extended
            pose = classify_pose(extended, scores)
            assert pose in ('pointer', 'two'), (middle_score, middle_extended, pose)


def test_a_measured_threshold_changes_the_live_classification():
    """End to end over synthetic landmarks: a low-scoring hand is refused by the default
    thresholds and accepted once thresholds are measured for it."""
    def run(thresholds):
        stability = FingerStability(thresholds)
        result = None
        for frame in range(8):
            hand = hand_model.as_hand(pose='pointer', curls=(0.28, 1.0, 1.0, 1.0))
            sample, quality = extract_sample(hand, frame / 30.0, 1280, 720, 1, stability)
            result = sample.pose if sample is not None else 'REJECTED'
        return result

    assert run(None) == 'REJECTED', 'a relaxed index is refused at the default bar'
    assert run((0.30,) * 5) == 'pointer', 'and accepted once the bar fits this hand'


# ---------------------------------------------------------------- pointer gain

def sweep(span, count=40, start=-1.0):
    return [start + span * index / (count - 1) for index in range(count)]


def test_the_gain_makes_one_comfortable_sweep_cover_the_target():
    """Spans here stay clear of the clamps, where the invariant is what is being tested."""
    from airpad.thresholds import derive_sensitivity

    for span in (0.7, 1.0, 1.6):
        sensitivity, report = derive_sensitivity(sweep(span))
        assert sensitivity is not None, report
        assert 500 < sensitivity < 2200, 'this span should not hit a clamp'
        assert sensitivity * report['span_palm_units'] == pytest.approx(1100, rel=0.01)


def test_a_small_hand_and_a_large_hand_reach_the_same_screen_travel():
    """The whole point of measuring: a short comfortable range must not mean a slow cursor."""
    from airpad.thresholds import derive_sensitivity

    small, small_report = derive_sensitivity(sweep(0.7))
    large, large_report = derive_sensitivity(sweep(1.4))
    assert small > large, 'a shorter range needs a higher gain'
    assert (small * small_report['span_palm_units']
            == pytest.approx(large * large_report['span_palm_units'], rel=0.01))


def test_a_barely_moving_finger_is_refused_rather_than_turned_into_an_absurd_gain():
    from airpad.thresholds import derive_sensitivity

    sensitivity, report = derive_sensitivity(sweep(0.12))
    assert sensitivity is None
    assert 'move far enough' in report['reason']


def test_too_few_sweep_samples_are_refused():
    from airpad.thresholds import derive_sensitivity

    sensitivity, report = derive_sensitivity(sweep(1.0, count=5))
    assert sensitivity is None
    assert 'not enough' in report['reason']


def test_the_gain_is_clamped_to_the_supported_range():
    from airpad.thresholds import derive_sensitivity

    slow, slow_report = derive_sensitivity(sweep(5.0))
    fast, fast_report = derive_sensitivity(sweep(0.25))
    assert slow == 500.0
    assert fast == 4000.0, fast_report
    assert fast_report['span_palm_units'] > 0.15, 'the span itself was usable'
    assert slow_report['clamped'] == 'low' and fast_report['clamped'] == 'high'


def test_an_unclamped_gain_hits_the_target_and_says_it_was_not_clamped():
    from airpad.thresholds import derive_sensitivity

    gain, report = derive_sensitivity(sweep(1.0))
    assert report['clamped'] is None
    assert report['counts_per_comfortable_sweep'] == pytest.approx(1100, rel=0.05), report


def test_the_ceiling_matches_what_a_profile_accepts():
    """Clamping tighter than the profile allows silently pins a small-sweep hand to a
    pointer that moves a fraction of the intended distance."""
    from airpad.config import Profile
    from airpad.thresholds import derive_sensitivity

    _, report = derive_sensitivity(sweep(0.25))       # a sweep far too small for 1100
    assert report['clamped'] == 'high'
    assert report['sensitivity'] == 4000
    Profile(sensitivity=float(report['sensitivity'])).validate()


def pinch_cycles(closed=0.18, opened=0.90, cycles=6, per_cycle=24):
    """A triangle wave through one pinch cycle, as the measurement phase records it."""
    out = []
    for _ in range(cycles):
        for step in range(per_cycle):
            phase = step / (per_cycle - 1)
            out.append(closed + (opened - closed) * (phase * 2 if phase < 0.5
                                                     else (1 - phase) * 2))
    return out


def test_pinch_contact_sits_between_a_real_pinch_and_the_close_threshold():
    from airpad.thresholds import recommend_pinch

    values = recommend_pinch(pinch_cycles())
    assert values is not None
    assert values['closed_p15'] < values['pinch_contact'] < values['pinch_close']
    assert values['pinch_contact'] <= values['pinch_close'] - 0.05, \
        'contact must leave the close threshold its required margin'


def test_the_measured_pinch_thresholds_are_a_usable_profile():
    """A measured pinch must not be rejected by the profile that has to store it."""
    from airpad.config import Profile
    from airpad.thresholds import recommend_pinch

    values = recommend_pinch(pinch_cycles())
    profile = Profile(**{key: values[key] for key in
                         ('pinch_close', 'pinch_release', 'pinch_open', 'pinch_contact')})
    profile.validate()


def test_a_hand_that_barely_separates_gets_no_pinch_thresholds():
    from airpad.thresholds import recommend_pinch

    assert recommend_pinch(pinch_cycles(closed=0.60, opened=0.66)) is None
    assert recommend_pinch(pinch_cycles()[:10]) is None, 'too few samples'


def test_a_closed_level_above_the_plausible_ceiling_is_refused_not_clamped():
    """Clamping would emit a 'close' below the hand's own closed level, i.e. a threshold
    no pinch can ever cross: every click would vanish with a profile that looks fine."""
    from airpad.thresholds import recommend_pinch

    values = recommend_pinch(pinch_cycles(closed=0.92, opened=1.24))
    assert values is None, values
    # Just inside the ceiling still works, so the refusal is about reachability only.
    assert recommend_pinch(pinch_cycles(closed=0.30, opened=1.20)) is not None


def test_a_hand_whose_pinch_never_quite_closes_still_gets_a_deeper_contact():
    """A hand with long nails may bottom out high; contact must follow it, not 0.25."""
    from airpad.thresholds import recommend_pinch

    values = recommend_pinch(pinch_cycles(closed=0.42, opened=1.05))
    assert values is not None
    assert values['pinch_contact'] > 0.40, values
    assert values['pinch_contact'] <= values['pinch_close'] - 0.05, values
    assert abs(values['pinch_contact'] - 0.5 * (values['closed_p15']
                                                + values['pinch_close'])) < 0.02, values
