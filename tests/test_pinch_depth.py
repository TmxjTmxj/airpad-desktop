"""The click depth must belong to the hand the profile was measured from.

A fixed absolute depth looks reasonable next to the default thresholds and is a silent
trap for anybody else: the calibration wizard clamps a wide pinch range to
`pinch_close = 0.60`, and a hand measured at that scale would have to close to an
absolute 0.25 before a click is allowed at all. If it never does, no click ever fires and
nothing in the UI explains why. So the depth follows the close threshold.
"""
import json

import pytest


def test_the_default_depth_is_the_verified_quarter_of_a_palm():
    from airpad.config import Profile
    assert Profile().pinch_contact == pytest.approx(0.252), \
        'the default must stay at the 0.60 x close value the click suite was tuned against'


def test_a_wide_measured_range_gets_a_depth_it_can_reach():
    from airpad.config import Profile
    wide = Profile().updated({'pinch_close': .60, 'pinch_release': 1.16, 'pinch_open': 1.24})
    assert wide.pinch_contact == Profile.contact_for(.60)
    assert wide.pinch_contact > .25, 'an absolute 0.25 could be unreachable for this hand'
    assert wide.pinch_contact <= wide.pinch_close - .05
    wide.validate()


def test_a_depth_exactly_at_the_margin_is_accepted():
    """0.6 - 0.05 is 0.5499999999999999 in floating point; 0.55 is the same number."""
    from airpad.config import Profile
    Profile(pinch_close=.60, pinch_release=.70, pinch_open=.80, pinch_contact=.55).validate()


def test_moving_the_close_threshold_moves_the_depth_with_it():
    from airpad.config import Profile
    moved = Profile().updated({'pinch_close': .30})
    assert moved.pinch_contact == Profile.contact_for(.30)
    assert moved.pinch_contact <= moved.pinch_close - .05


def test_an_explicit_depth_is_still_honoured():
    from airpad.config import Profile
    kept = Profile().updated({'pinch_close': .30, 'pinch_contact': .20})
    assert kept.pinch_contact == .20


def test_a_profile_written_before_the_gate_existed_is_backfilled_not_defaulted(tmp_path):
    from airpad.config import Profile
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps({
        'schema_version': 1, 'sensitivity': 1407.0, 'smoothing': 1.5,
        'pinch_close': 0.60, 'pinch_release': 1.1599, 'pinch_open': 1.2399,
        'calibrated': True, 'finger_thresholds': [0.62] * 5,
    }, ensure_ascii=False), encoding='utf-8')
    loaded = Profile.load(path)
    assert loaded.pinch_contact == Profile.contact_for(.60)
    assert loaded.calibrated is True and loaded.pinch_close == 0.60


def test_a_broken_contact_is_still_rejected():
    from airpad.config import Profile
    with pytest.raises(ValueError):
        Profile(pinch_close=.42, pinch_contact=.40).validate()
    with pytest.raises(ValueError):
        Profile(pinch_contact=0.0).validate()


def test_the_calibration_wizard_applies_a_measurable_depth():
    """The wizard must never leave the depth at a value this hand cannot reach."""
    from airpad.calibration import Calibrator
    from airpad.config import Profile
    from airpad.thresholds import recommend_pinch

    wide = [0.42 + (1.05 - 0.42) * (i / 23) for i in range(24)]
    values = recommend_pinch(wide * 6)
    assert values is not None
    profile = Profile().updated({key: values[key] for key in
                                 ('pinch_close', 'pinch_release', 'pinch_open',
                                  'pinch_contact')})
    assert profile.pinch_contact == values['pinch_contact']
    # Same floating-point boundary as the profile rule itself: 0.6 - 0.05 is 0.5499999999999999.
    assert profile.pinch_contact <= profile.pinch_close - .05 + 1e-9
    profile.validate()
    assert Calibrator(Profile()) is not None
