"""The profile is the one file the user's measurement is stored in.

Two things must hold. A profile that cannot be read must never stop the app - it must
start on defaults and say so. And whatever the measurement writes must load back
unchanged, including the per-finger marginal bands that keep a curled finger out of the
"partially extended" band.
"""
import json

import pytest

from airpad.config import Profile


def derived_profile():
    """A profile shaped exactly like the one tools/hand_check.py --apply writes."""
    return Profile().updated({
        'finger_thresholds': (0.44, 0.30, 0.41, 0.43, 0.39),
        'finger_marginals': (0.21, 0.16, None, 0.19, None),
        'sensitivity': 1375.0,
        'pinch_close': 0.35,
        'pinch_release': 0.61,
        'pinch_open': 0.83,
        'calibrated': True,
    })


def test_a_measured_profile_survives_a_save_and_load(tmp_path):
    path = tmp_path / 'profile.json'
    written = derived_profile()
    written.save(path)
    loaded = Profile.load(path)
    assert loaded == written, 'the measurement must come back exactly as it went in'
    assert loaded.finger_thresholds == (0.44, 0.30, 0.41, 0.43, 0.39)
    assert loaded.finger_marginals == (0.21, 0.16, None, 0.19, None), \
        'null marginals must round-trip: they are what keeps a curled finger out of the band'
    assert loaded.sensitivity == 1375.0
    assert loaded.calibrated is True


def test_a_profile_without_measurements_round_trips_too(tmp_path):
    path = tmp_path / 'profile.json'
    written = Profile()
    written.save(path)
    loaded = Profile.load(path)
    assert loaded == written
    assert loaded.finger_thresholds == () and loaded.finger_marginals == ()


@pytest.mark.parametrize('content,label', [
    ('{"schema_version": 1, "sensitivity": ', 'truncated json'),
    ('not json at all', 'not json'),
    ('{"schema_version": 2, "sensitivity": 1000.0}', 'future schema version'),
    ('{"schema_version": 1, "sensitivity": 1000.0, "who_knows": 1}', 'unknown field'),
    ('{"schema_version": 1, "sensitivity": 99999.0}', 'value out of range'),
    ('{"schema_version": 1, "finger_thresholds": [0.5, 0.5]}', 'wrong threshold count'),
    ('{"schema_version": 1, "finger_thresholds": [1.5, 0.5, 0.5, 0.5, 0.5]}',
     'threshold out of range'),
    ('{"schema_version": 1, "finger_marginals": [0.2, null, null, null, 0.99]}',
     'marginal out of range'),
])
def test_an_unreadable_profile_is_refused_rather_than_guessed(tmp_path, content, label):
    path = tmp_path / 'profile.json'
    path.write_text(content, encoding='utf-8')
    with pytest.raises((ValueError, TypeError, json.JSONDecodeError)):
        Profile.load(path)


def test_the_controller_falls_back_to_defaults_and_says_so(tmp_path):
    """A corrupt profile must not stop the app: safe defaults plus a visible message."""
    import os

    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication

    from airpad.runtime import AirpadController

    QApplication.instance() or QApplication([])
    (tmp_path / 'profile.json').write_text('{"schema_version": 9}', encoding='utf-8')
    controller = AirpadController(tmp_path, vision_factory=None, broker_factory=None)
    try:
        assert controller.profile.sensitivity == 1000.0, 'defaults must be in effect'
        assert controller.profile.finger_thresholds == ()
        assert 'defaults restored' in controller.error
        # And it must still run.
        controller.start_camera()
        assert controller.camera_state in ('starting', 'running')
    finally:
        controller.shutdown()


def test_the_controller_accepts_a_measured_profile(tmp_path):
    import os

    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication

    from airpad.runtime import AirpadController

    QApplication.instance() or QApplication([])
    derived_profile().save(tmp_path / 'profile.json')
    controller = AirpadController(tmp_path, vision_factory=None, broker_factory=None)
    try:
        assert not controller.error, controller.error
        assert controller.profile.finger_thresholds == (0.44, 0.30, 0.41, 0.43, 0.39)
        assert controller.stability.bands['index']['strong'] == 0.30, \
            'the measured threshold must reach the live hysteresis'
        assert controller.stability.bands['index']['marginal'] == 0.16, \
            'and so must the measured marginal band'
    finally:
        controller.shutdown()
