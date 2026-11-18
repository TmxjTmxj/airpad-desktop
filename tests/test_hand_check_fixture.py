"""The measurement tool must work with data flowing through it, not just in pieces.

Its collection code only ever ran with an empty scene before: every manual run reported
zero samples per phase, and the fist phase - the only source of the "curled" distribution -
collected nothing at all because a fist is deliberately refused by the control gate. This
runs the whole guided sequence from a scripted hand and checks what it produces.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEED = '0.4'


@pytest.fixture(scope='module')
def measured(tmp_path_factory):
    """Run the tool once in fixture mode and return (report, profile)."""
    work = tmp_path_factory.mktemp('measure')
    data_dir = work / 'data'
    report_path = work / 'report.json'
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}
    result = subprocess.run(
        [sys.executable, 'tools/hand_check.py', '--source', 'fixture', '--speed', SPEED,
         '--apply', '--data-dir', str(data_dir), '--output', str(report_path),
         '--screenshot', ''],
        cwd=ROOT, capture_output=True, text=True, timeout=300, env=env)
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(report_path.read_text(encoding='utf-8'))
    profile_path = data_dir / 'profile.json'
    profile = json.loads(profile_path.read_text(encoding='utf-8')) if profile_path.is_file() else None
    return report, profile


def test_every_phase_collects_enough_samples(measured):
    report, _ = measured
    samples = report['phase_samples']
    assert set(samples) == {'open', 'fist', 'point', 'sweep', 'pinch'}
    for name, count in samples.items():
        assert count >= 12, f'{name} collected only {count} usable samples'
    assert report['enough_data'] is True
    assert 'too few frames' not in report['hint']


def test_the_fist_phase_is_what_supplies_the_curled_distribution(measured):
    """Without it the index - the finger that matters most - can never be measured."""
    report, _ = measured
    per_finger = report['recommended']['threshold_report']['per_finger']
    index = per_finger['index']
    assert index['source'] == 'measured', index.get('reason')
    assert index['curled_samples'] >= 12, 'the fist phase must feed the curled side'
    assert index['extended_median'] > index['curled_median'], \
        'an extended index must score above a curled one'


def test_every_finger_gets_a_measured_threshold(measured):
    report, _ = measured
    assert report['recommended']['threshold_report']['measured_fingers'] == 5
    assert report['recommended']['threshold_report']['defaults_used'] == []
    margins = report['recommended']['finger_marginals']
    assert len(margins) == 5
    for finger, value in zip(('thumb', 'index', 'middle', 'ring', 'pinky'),
                             report['recommended']['finger_thresholds']):
        assert 0.28 <= value <= 0.82, (finger, value)


def test_the_pointer_gain_is_derived_from_the_sweep(measured):
    report, _ = measured
    recommended = report['recommended']
    assert recommended['sensitivity'] is not None, recommended['sweep_report']
    sweep = recommended['sweep_report']
    assert sweep['span_palm_units'] > 0.15
    assert sweep['counts_per_comfortable_sweep'] == pytest.approx(1100, rel=0.02), \
        'one comfortable sweep should always cover about the same cursor travel'


def test_the_pinch_range_is_derived_from_the_samples(measured):
    report, _ = measured
    pinch = report['recommended']['pinch']
    assert pinch is not None
    assert pinch['open_p85'] - pinch['closed_p15'] > 0.15
    assert pinch['pinch_close'] < pinch['pinch_release'] < pinch['pinch_open']
    assert pinch['closed_p15'] < pinch['pinch_contact'] < pinch['pinch_close'], \
        'the depth a click demands must come from this hand, not from a default'


def test_apply_writes_a_profile_the_app_can_load(measured):
    report, profile = measured
    assert report['applied'] is True
    assert profile is not None, 'the profile must have been written'
    assert profile['schema_version'] == 1
    assert profile['calibrated'] is True
    assert profile['sensitivity'] == report['recommended']['sensitivity']
    assert profile['finger_thresholds'] == report['recommended']['finger_thresholds']
    assert profile['finger_marginals'] == report['recommended']['finger_marginals']
    assert profile['pinch_contact'] == report['recommended']['pinch']['pinch_contact']
    assert 'Start AIRPAD' in report['next_step']
    from airpad.config import Profile
    Profile(**{key: profile[key] for key in ('pinch_close', 'pinch_release', 'pinch_open',
                                            'pinch_contact')}).validate()


def test_a_fixture_run_is_labelled_as_a_self_check(measured):
    """It proves the path works; it is not a measurement of anybody's hand."""
    report, _ = measured
    assert report['source'] == 'fixture'
    assert 'meaningless as a measurement' in report['hand_check']
    assert 'not what your hand scores' in report['note']
