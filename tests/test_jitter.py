"""Tremor regressions: synthetic observations only, never camera/OS input."""
import math

import pytest

from airpad.config import Profile
from airpad.core import GestureEngine
from airpad.filtering import RelativeMotion
from airpad.types import HandSample


def feed(engine, frame, pinch=.9, palm=.5, tip=.5, scale=.2):
    sample = HandSample(frame / 30, (tip, .3), (palm, .6), scale,
                        pinch, 'pointer', (tip, .3), .7)
    return engine.step(sample, sample.t)


def click_with_offsets(offsets):
    engine = GestureEngine(Profile())
    feed(engine, 0)
    actions = []
    for frame, offset in enumerate(offsets, 1):
        actions += feed(engine, frame, pinch=.18, palm=.5 + offset)
    actions += feed(engine, len(offsets) + 1)
    return [action.kind for action in actions]


@pytest.mark.parametrize('spike_frame', [0, 3, 7])
def test_single_palm_landmark_spike_does_not_cancel_click(spike_frame):
    offsets = [0.] * 10
    offsets[spike_frame] = .035
    assert click_with_offsets(offsets) == ['click']


@pytest.mark.parametrize('amplitude', [.012, .026, .035])
def test_back_and_forth_tremor_is_not_deliberate_drag(amplitude):
    offsets = [0.] + [amplitude * (-1) ** frame for frame in range(1, 13)]
    assert click_with_offsets(offsets) == ['click']


def test_sustained_translation_still_drags_once_and_releases():
    kinds = click_with_offsets([0.] * 6 + [.04] * 5)
    assert kinds.count('left_down') == kinds.count('left_up') == 1
    assert 'click' not in kinds


def test_brief_outward_and_returning_motion_remains_click():
    assert click_with_offsets([0.] * 6 + [.035, .035, 0., 0., 0.]) == ['click']


def test_contact_has_small_hysteresis_without_delaying_release():
    engine = GestureEngine(Profile())
    feed(engine, 0)
    actions = []
    for frame, pinch in enumerate([.245, .265, .245, .265, .245], 1):
        actions += feed(engine, frame, pinch=pinch)
    assert actions == []
    assert [a.kind for a in feed(engine, 6)] == ['click']


def test_close_confirmation_tolerates_small_threshold_tremor():
    engine = GestureEngine(Profile())
    feed(engine, 0)
    for frame, pinch in enumerate([.18, .18, .425, .41], 1):
        feed(engine, frame, pinch=pinch)
    assert [a.kind for a in feed(engine, 5)] == ['click']


@pytest.mark.parametrize('drag', [False, True])
def test_single_release_threshold_spike_does_not_commit_or_drop_drag(drag):
    engine = GestureEngine(Profile())
    feed(engine, 0)
    for frame in range(1, 12):
        feed(engine, frame, pinch=.18, palm=.54 if drag and frame > 6 else .5)
    before = engine.state
    assert feed(engine, 12, pinch=.635, palm=.54 if drag else .5) == []
    assert engine.state == before
    feed(engine, 13, pinch=.18, palm=.54 if drag else .5)
    assert [a.kind for a in feed(engine, 14)] == ['left_up' if drag else 'click']


def test_partial_but_sustained_opening_releases_after_two_frames():
    engine = GestureEngine(Profile())
    feed(engine, 0)
    for frame in range(1, 6):
        feed(engine, frame, pinch=.18)
    assert feed(engine, 6, pinch=.635) == []
    assert [a.kind for a in feed(engine, 7, pinch=.635)] == ['click']


@pytest.mark.parametrize('sensitivity', [1000, 2200])
def test_stationary_pointer_small_tremor_does_not_creep(sensitivity):
    motion = RelativeMotion(sensitivity)
    events = [motion.update((.5 + .001 * math.sin(i * 1.7), .3), .2, i / 30)
              for i in range(120)]
    assert all(delta == (0, 0) for delta in events)


def test_tiny_continuous_motion_accumulates_instead_of_disappearing():
    motion = RelativeMotion(2200)
    events = [motion.update((.5 + i * .0001, .3), .2, i / 30) for i in range(120)]
    assert sum(delta[0] for delta in events) > 100
    assert all(delta[0] >= 0 for delta in events)


def test_fast_motion_is_responsive_and_not_erased_by_stop_zone():
    motion = RelativeMotion(2200)
    motion.update((.5, .3), .2, 0)
    assert motion.update((.51, .3), .2, 1 / 30)[0] > 20


@pytest.mark.parametrize('interruption', ['loss', 'open'])
def test_noise_tolerance_does_not_weaken_cancellation(interruption):
    engine = GestureEngine(Profile())
    feed(engine, 0)
    for frame in range(1, 12):
        feed(engine, frame, pinch=.18, palm=.54 if frame > 6 else .5)
    assert engine.state == 'DRAGGING'
    sample = None if interruption == 'loss' else HandSample(
        .4, (.5, .3), (.5, .6), .2, .9, 'open', (.5, .3), .7)
    assert [a.kind for a in engine.step(sample, .4)] == ['left_up', 'release_all']


def test_wide_pinch_range_keeps_calibrated_thresholds_and_tolerates_contact_noise():
    profile = Profile(pinch_close=.6, pinch_contact=.48, pinch_release=1., pinch_open=1.3)
    engine = GestureEngine(profile)
    feed(engine, 0, pinch=1.4)
    for frame, pinch in enumerate([.47, .50, .47, .50, .47], 1):
        feed(engine, frame, pinch=pinch)
    assert [a.kind for a in feed(engine, 6, pinch=1.4)] == ['click']
    assert engine.profile.pinch_contact == .48


def test_real_landmark_chain_keeps_a_palm_spike_as_a_click():
    from airpad.geometry import extract_sample, FingerStability
    from tools.hand_model import as_hand

    engine = GestureEngine(Profile())
    stability = FingerStability()
    actions = []
    for frame in range(26):
        closed = 12 <= frame < 24
        hand = as_hand(pose='pointer', pinch=.18 if closed else .9,
                       palm=(.66 + (.035 if frame == 19 else 0.), .55))
        sample, _ = extract_sample(hand, frame / 30, 640, 480, 1, stability)
        actions += engine.step(sample, frame / 30)
    assert [a.kind for a in actions if a.kind in ('click', 'left_down')] == ['click']


def test_deterministic_tremor_benchmark_clicks_without_false_drag():
    from tools.jitter_eval import evaluate
    report = evaluate()
    assert report['correct_stationary_clicks'] == report['click_trials'] == 200
    assert report['unintended_drags'] == 0
