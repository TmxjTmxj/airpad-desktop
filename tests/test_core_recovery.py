"""Recovery regressions; all observations and actions stay inside GestureEngine."""
import pytest
from dataclasses import replace

from airpad.config import Profile
from airpad.core import GestureEngine
from airpad.types import HandSample


def observation(frame, pinch=.8, x=.5, pose='pointer'):
    return HandSample(frame * .033, (x, .3), (x, .6), .2, pinch, pose, (x, .3), .7)


def step(engine, frame, **kwargs):
    sample = observation(frame, **kwargs)
    return engine.step(sample, sample.t)


def pinching_engine(drag):
    engine = GestureEngine(Profile())
    step(engine, 0)
    actions = []
    for frame in range(1, 12):
        actions += step(engine, frame, pinch=.15,
                        x=.5 + (max(0, frame - 4) * .006 if drag else 0))
    assert engine.state == ('DRAGGING' if drag else 'PINCH_HOLD')
    assert sum(a.kind == 'left_down' for a in actions) == int(drag)
    return engine


@pytest.mark.parametrize('drag', [False, True])
@pytest.mark.parametrize('moving', [False, True])
@pytest.mark.parametrize('interruption', ['missing', 'invalid', 'stale', 'owner_changed'])
def test_closed_reacquisition_cannot_commit_an_interrupted_pinch(drag, moving, interruption):
    """Loss cancels intent even if the hand returns pinched and then moves/releases."""
    engine = pinching_engine(drag)
    cancelled = {
        'missing': None,
        'invalid': replace(observation(12, pinch=.15), valid=False),
        'stale': observation(8, pinch=.15),
        'owner_changed': replace(observation(12, pinch=.15), track_id=2),
    }[interruption]
    release = engine.step(cancelled, 12 * .033)
    assert any(a.kind == 'release_all' for a in release)
    if drag:
        assert any(a.kind == 'left_up' for a in release)
    # Repeated missing frames must not erase the cancellation latch.
    engine.step(None, 13 * .033)
    actions = []
    for frame in range(14, 25):
        actions += step(engine, frame, pinch=.15,
                        x=.5 + (max(0, frame - 18) * .006 if moving else 0))
    actions += step(engine, 25, pinch=.8, x=.536 if moving else .5)
    assert not any(a.kind in ('left_down', 'click') for a in actions)
    # A visible separation followed by a new pinch should restore normal clicking.
    step(engine, 26)
    fresh = []
    for frame in range(27, 32):
        fresh += step(engine, frame, pinch=.15)
    fresh += step(engine, 32)
    assert sum(a.kind == 'click' for a in fresh) == 1


def test_fresh_drag_continues_past_two_seconds_until_release():
    engine = pinching_engine(True)
    actions = []
    for frame in range(12, 110):
        frame_actions = step(engine, frame, pinch=.15, x=.53 + (frame - 11) * .002)
        assert not any(a.kind in ('left_up', 'release_all', 'left_down', 'click')
                       for a in frame_actions)
        actions += frame_actions
        assert engine.state == 'DRAGGING'
    assert any(a.kind == 'move' for a in actions)
    release = step(engine, 110, x=.726)
    assert [a.kind for a in release] == ['left_up']


def test_unknown_pose_cannot_seed_a_pinch_that_later_becomes_valid():
    engine = GestureEngine(Profile())
    step(engine, 0)
    actions = step(engine, 1, pose='unknown', pinch=.15)
    for frame in range(2, 7):
        actions += step(engine, frame, pinch=.15)
    actions += step(engine, 7)
    assert not any(a.kind in ('click', 'left_down') for a in actions)


def test_pending_pinch_still_times_out_and_needs_separation_before_reuse():
    engine = pinching_engine(False)
    actions = []
    for frame in range(12, 76):
        actions += step(engine, frame, pinch=.15)
    assert any(a.kind == 'release_all' for a in actions)
    assert not any(a.kind in ('left_down', 'click') for a in actions)
    recovered = []
    for frame in range(76, 90):
        recovered += step(engine, frame, pinch=.15, x=.5 + (frame - 76) * .004)
    recovered += step(engine, 90, x=.552)
    assert not any(a.kind in ('left_down', 'click') for a in recovered)


def test_closed_initial_acquisition_can_point_but_cannot_click_on_release():
    engine = GestureEngine(Profile())
    assert step(engine, 0, pinch=.15) == []
    assert engine.state == 'TRACKING'
    actions = []
    for frame in range(1, 15):
        actions += step(engine, frame, pinch=.15, x=.5 + frame * .002)
    actions += step(engine, 15, x=.528)
    assert any(a.kind == 'move' for a in actions)
    assert not any(a.kind in ('left_down', 'click') for a in actions)
