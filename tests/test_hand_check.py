"""The measurement tool must tell the user what to redo, not just that it fell back."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from hand_check import build_hint  # noqa: E402


def report(defaults=(), narrow=(), reason='not separable'):
    per_finger = {finger: {'reason': reason} for finger in defaults}
    return {'defaults_used': list(defaults), 'narrow_fingers': list(narrow),
            'per_finger': per_finger}


def test_thin_phases_are_named():
    hint = build_hint({'open': 40, 'fist': 3, 'point': 40, 'sweep': 40, 'pinch': 0},
                      report(), {'pinch_close': 0.4})
    assert 'fist' in hint and 'keep your hand in view' in hint


def test_unmeasurable_fingers_are_named_with_the_reason():
    hint = build_hint({'open': 40, 'fist': 40, 'point': 40, 'sweep': 40, 'pinch': 40},
                      report(defaults=['index', 'thumb']), {'pinch_close': 0.4})
    assert 'index' in hint and 'thumb' in hint
    assert 'not separable' in hint
    assert 'built-in default' in hint


def test_a_narrow_gap_is_reported_as_workable_but_improvable():
    hint = build_hint({'open': 40, 'fist': 40, 'point': 40, 'sweep': 40, 'pinch': 40},
                      report(narrow=['ring']), {'pinch_close': 0.4}, 1200.0)
    assert 'narrow' in hint and 'ring' in hint
    assert 'built-in' not in hint


def test_a_failed_pinch_measurement_is_called_out():
    hint = build_hint({'open': 40, 'fist': 40, 'point': 40, 'sweep': 40, 'pinch': 40},
                      report(), None, 1200.0)
    assert 'pinch' in hint.lower()


def test_a_clean_run_says_so():
    hint = build_hint({'open': 40, 'fist': 40, 'point': 40, 'sweep': 40, 'pinch': 40},
                      report(), {'pinch_close': 0.4}, 1200.0)
    assert 'cleanly' in hint


def test_writing_the_profile_is_refused_while_the_app_is_running(tmp_path):
    """The app saves its own in-memory profile when a setting changes, so applying a
    measurement underneath a running instance silently loses it."""
    import os

    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import QLockFile
    from PySide6.QtWidgets import QApplication

    from hand_check import app_is_running

    QApplication.instance() or QApplication([])
    assert not app_is_running(tmp_path), 'nothing holds the lock yet'

    holder = QLockFile(str(tmp_path / 'airpad.lock'))
    assert holder.tryLock(0)
    try:
        assert app_is_running(tmp_path), 'a held lock means the app is running'
    finally:
        holder.unlock()
    assert not app_is_running(tmp_path), 'and a released lock means it is not'


def test_a_missing_data_directory_is_not_mistaken_for_a_running_app(tmp_path):
    """QLockFile cannot create its file in a directory that does not exist yet, and the
    first measurement happens exactly then."""
    import os

    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication

    from hand_check import app_is_running

    QApplication.instance() or QApplication([])
    fresh = tmp_path / 'never-created' / 'deeper'
    assert not fresh.exists()
    assert app_is_running(fresh) is False
    assert fresh.is_dir(), 'the check may create it, but must not report a false positive'


def test_a_too_small_sweep_is_called_out_without_changing_the_gain():
    hint = build_hint({'open': 40, 'fist': 40, 'point': 40, 'sweep': 40, 'pinch': 40},
                      report(), {'pinch_close': 0.4}, None)
    assert 'sweep' in hint and 'pointer gain was left unchanged' in hint
    assert 'cleanly' not in hint


def test_several_caveats_are_all_reported():
    hint = build_hint({'open': 40, 'fist': 40, 'point': 40, 'sweep': 40, 'pinch': 40},
                      report(narrow=['ring']), None, None)
    assert 'narrow' in hint and 'sweep' in hint and 'pinch' in hint
    assert hint.startswith('Measured, with caveats')


CLEAN = {'open': 40, 'fist': 40, 'point': 40, 'sweep': 40, 'pinch': 40}


def test_an_unusable_measurement_is_refused_instead_of_written():
    """A run the report itself calls thin must not silently replace a working profile."""
    from hand_check import apply_blocker

    thin = dict(CLEAN, open=3)
    message = apply_blocker(thin, running=False)
    assert message and 'not usable' in message and 'open' in message
    assert 'nothing was written' in message


def test_a_clean_measurement_is_written():
    from hand_check import apply_blocker

    assert apply_blocker(CLEAN, running=False) == ''


def test_a_thin_phase_is_refused_even_when_the_app_is_closed():
    from hand_check import apply_blocker

    assert apply_blocker(dict(CLEAN, sweep=2), running=False) != ''


def test_a_running_app_still_blocks_a_clean_measurement():
    from hand_check import apply_blocker

    message = apply_blocker(CLEAN, running=True)
    assert 'running' in message.lower() and 'close airpad' in message.lower()


def test_the_pinch_phase_is_never_the_reason_for_a_refusal():
    """The pinch phase has its own fallback: its thresholds are simply left alone."""
    from hand_check import apply_blocker

    assert apply_blocker(dict(CLEAN, pinch=0), running=False) == ''


def test_a_sweep_too_small_to_calibrate_the_gain_is_called_out():
    """'All five phases measured cleanly' must not cover a gain pinned at its ceiling."""
    hint = build_hint(CLEAN, report(), {'pinch_close': 0.4}, 4000.0,
                      {'clamped': 'high', 'span_palm_units': 0.1665})
    assert 'maximum' in hint and 'sweep' in hint
    assert 'whole hand inside the frame' in hint
    assert hint.startswith('Measured, with caveats')


def test_an_over_wide_sweep_is_called_out_too():
    hint = build_hint(CLEAN, report(), {'pinch_close': 0.4}, 500.0,
                      {'clamped': 'low', 'span_palm_units': 3.0})
    assert 'minimum' in hint and 'twitchy' in hint


def test_a_clean_sweep_adds_no_caveat():
    hint = build_hint(CLEAN, report(), {'pinch_close': 0.4}, 1400.0,
                      {'clamped': None, 'span_palm_units': 0.8})
    assert hint == 'All five phases measured cleanly.'
