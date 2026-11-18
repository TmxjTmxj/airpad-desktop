"""Touchpad intent from independent tip/palm trajectories; no device input."""
from dataclasses import replace

import pytest

from airpad.config import Profile
from airpad.core import GestureEngine
from airpad.types import HandSample


def observation(t, pinch=.9, pose='pointer', tip=.5, palm=.5,
                center=(.5, .3), scale=.2, distance=.7):
    palm = (palm, .6) if isinstance(palm, (float, int)) else palm
    return HandSample(t, (tip, .3), palm, scale, pinch, pose, center, distance)


def send(engine, t, **kwargs):
    return engine.step(observation(t, **kwargs), t)


def engine(two=False, zoom=False):
    result = GestureEngine(Profile(two_finger_scroll=two, zoom_enabled=zoom))
    send(result, 0)
    return result


def test_curled_tip_with_stationary_palm_clicks_instead_of_dragging():
    e = engine()
    actions = []
    for frame in range(1, 10):
        actions += send(e, frame / 30, pinch=.18, tip=.5 + frame * .007)
    actions += send(e, 10 / 30, tip=.5)
    assert [a.kind for a in actions] == ['click']


@pytest.mark.parametrize('tip,palm', [(.6, .5), (.5, .6), (.6, .6)])
def test_release_rebound_cannot_erase_an_already_confirmed_click(tip, palm):
    e = engine()
    for frame in range(1, 5):
        send(e, frame / 30, pinch=.18)
    assert [a.kind for a in send(e, 5 / 30, tip=tip, palm=palm)] == ['click']


def test_contact_and_travel_cannot_start_drag_before_180ms():
    e = engine()
    actions = []
    for frame in range(1, 7):
        actions += send(e, frame / 30, pinch=.18, palm=.5 + (frame - 1) * .009,
                        tip=.5 + (frame - 1) * .009)
    assert not any(a.kind == 'left_down' for a in actions)
    assert [a.kind for a in send(e, 7 / 30, pinch=.18, palm=.56)] == ['left_down']


def test_custom_drag_hold_delays_drag_without_changing_contact_thresholds():
    e = GestureEngine(Profile(drag_hold=.3))
    send(e, 0)
    for frame in range(1, 11):
        assert not any(a.kind == 'left_down' for a in
                       send(e, frame * .03, pinch=.18, palm=.5 + (frame - 1) * .01))
    assert [a.kind for a in send(e, .34, pinch=.18, palm=.6)] == ['left_down']


@pytest.mark.parametrize('hold', [.1, 1.1, True, float('nan')])
def test_drag_hold_rejects_values_outside_safe_intent_window(hold):
    with pytest.raises(ValueError):
        Profile(drag_hold=hold).validate()


def test_drag_measures_palm_motion_even_when_tip_stays_still():
    e = engine()
    actions = []
    for frame in range(1, 10):
        actions += send(e, frame / 30, pinch=.18, palm=.5 + (frame - 1) * .006)
    assert sum(a.kind == 'left_down' for a in actions) == 1
    assert [a.kind for a in send(e, 10 / 30)] == ['left_up']


def test_pinch_uses_initial_scale_so_scale_jitter_cannot_start_drag():
    e = engine()
    send(e, .03, pinch=.18)
    actions = []
    for frame in range(2, 10):
        actions += send(e, frame * .03, pinch=.18, palm=.516, tip=.516, scale=.1)
    actions += send(e, .30, palm=.516, scale=.1)
    assert [a.kind for a in actions] == ['click']


@pytest.mark.parametrize('pinches', [[.35] * 9, [.35, .18, .35, .18, .35, .18, .35, .18, .35]])
def test_grazes_or_separated_contact_spikes_cannot_drag_or_click(pinches):
    e = engine()
    actions = []
    for frame, pinch in enumerate(pinches, 1):
        actions += send(e, frame / 30, pinch=pinch, palm=.5 + (frame - 1) * .006,
                        tip=.5 + (frame - 1) * .006)
    actions += send(e, 10 / 30)
    assert not any(a.kind in ('left_down', 'click') for a in actions)


def test_disconnected_below_close_samples_cannot_confirm_a_pinch():
    e = engine()
    actions = []
    for frame, pinch in enumerate([.18, .18, .5, .18, .5, .18, .9], 1):
        actions += send(e, frame / 30, pinch=pinch)
    assert not any(a.kind in ('click', 'left_down') for a in actions)


@pytest.mark.parametrize('times', [[.03, .06, .09, .12, .16], [.03, .11, .19]])
def test_stable_short_two_finger_tap_right_clicks_once_on_pointer_return(times):
    e = engine(two=True)
    for t in times:
        assert send(e, t, pose='two') == []
    released = send(e, times[-1] + .04, tip=.8)
    assert [a.kind for a in released] == ['right_click']
    assert send(e, times[-1] + .07, tip=.8) == []


@pytest.mark.parametrize('exit_pose', ['open', 'unknown', 'fist'])
def test_two_finger_tap_cancellation_never_right_clicks(exit_pose):
    e = engine(two=True)
    for frame in range(1, 6):
        send(e, frame * .03, pose='two')
    actions = send(e, .18, pose=exit_pose)
    actions += send(e, .21)
    assert not any(a.kind == 'right_click' for a in actions)


@pytest.mark.parametrize('interruption', ['missing', 'gap', 'long', 'short'])
def test_interrupted_or_outside_window_two_finger_pose_cannot_right_click(interruption):
    e = engine(two=True)
    stop = 3 if interruption == 'short' else 6
    for frame in range(1, stop):
        send(e, frame * .03, pose='two')
    if interruption == 'missing':
        e.step(None, .18)
    if interruption == 'long':
        for frame in range(6, 17):
            send(e, frame * .03, pose='two')
    t = {'missing': .21, 'gap': .32, 'long': .51, 'short': .09}[interruption]
    assert not any(a.kind == 'right_click' for a in send(e, t))


def test_scroll_keeps_motion_from_pose_entry_and_locks_before_pointer_return():
    e = engine(two=True)
    actions = []
    # 0.06 normalized-image travel / 0.2 palm * 480 = 144 wheel units.
    for frame in range(1, 9):
        offset = min(frame - 1, 6) * .01
        actions += send(e, frame * .03, pose='two', center=(.5, .3 + offset), palm=(.5, .6 + offset))
    assert sum(a.y for a in actions if a.kind == 'wheel') == pytest.approx(144)
    actions += send(e, .27, tip=.8)
    actions += send(e, .30, tip=.8)
    assert not any(a.kind in ('right_click', 'move', 'click', 'zoom') for a in actions)


def test_movement_that_returns_before_scroll_lock_cannot_be_a_tap():
    e = engine(two=True)
    for frame in range(1, 5):
        send(e, frame * .03, pose='two')
    send(e, .15, pose='two', center=(.5, .33), palm=(.5, .63))
    send(e, .18, pose='two')
    assert not any(a.kind == 'right_click' for a in send(e, .21))


def test_zoom_lock_cannot_right_click_after_pointer_return():
    e = engine(two=True, zoom=True)
    actions = []
    for frame in range(1, 10):
        actions += send(e, frame * .03, pose='two', distance=.7 + (frame - 1) * .04)
    assert any(a.kind == 'zoom' for a in actions)
    assert not any(a.kind == 'right_click' for a in send(e, .30))


def test_finger_shape_changes_with_stationary_palm_are_not_scroll_intent():
    e = engine(two=True)
    actions = []
    for frame in range(1, 9):
        actions += send(e, frame * .03, pose='two', center=(.5, .3 + frame * .01),
                        distance=.7 + frame * .03)
    actions += send(e, .27)
    assert [a.kind for a in actions] == ['right_click']


def test_actual_landmark_middle_fold_preserves_two_finger_tap_through_hysteresis():
    from tools.hand_model import as_hand
    from airpad.geometry import FingerStability, extract_sample

    e = GestureEngine(Profile(two_finger_scroll=True))
    stability = FingerStability()
    events = []
    curls = [1.] * 8 + [0.] * 7 + [.1, .2, .3, .5, 1., 1., 1.]
    observed = []
    for frame, curl in enumerate(curls):
        t = frame / 30
        hand = as_hand(pose='pointer', curls=(0., curl, 1., 1.), width=1280, height=720)
        sample, _ = extract_sample(hand, t, 1280, 720, 1, stability)
        observed.append(sample.pose if sample else None)
        events += e.step(sample, t)
    assert observed[15] == 'two', 'fixture must exercise the middle-finger hysteresis tail'
    assert [a.kind for a in events if a.kind in ('right_click', 'wheel', 'zoom')] == ['right_click']
