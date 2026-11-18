"""Click semantics, pinned down.

The click is the gesture users notice first, so its rules are stated here rather than
buried in the general core tests. The behaviour follows what the published work does:

* a quick pinch-and-release is a click; a pinch that is *held* while the hand travels is a
  drag (TriPad, ACM CHI 2024; Apple's pinch-and-drag).
* a pinch must last an appreciable time, because "early release" is a named error class in
  the AR/VR pointing literature, and a momentary loss of fingertip separation is noise.
* a graze is not a pinch: the fingertips have to actually meet (PinchCatcher, arXiv
  2503.05456, separates a semi-pinch from a full pinch), which is what stops accidental
  clicks while sweeping the hand through the workspace.

Two regressions that silently killed every click are pinned here as well, because both
were invisible in ordinary use and only a measurement exposed them: the decision signal
must be the raw pinch (a smoothed one never reaches the release threshold or the contact
depth), and the approach branch must compare against the previous frame rather than the
value this step already overwrote.
"""
import pytest

DT = 1.0 / 30.0
OPEN = 0.90          # comfortably above pinch_release (0.62)
CLOSED = 0.18        # below pinch_contact (0.25)


def sample(t, pose='pointer', pinch=OPEN, x=0.5, scale=0.2):
    from airpad.types import HandSample
    return HandSample(t, (x, .3), (x, .6), scale, pinch, pose, (x, .3), .7)


def armed():
    """An engine in TRACKING with pinch_ready set, as after ordinary pointing."""
    from airpad.core import GestureEngine
    from airpad.config import Profile
    e = GestureEngine(Profile())
    for i in range(7):
        e.step(sample(i * DT, 'open'), i * DT)
    for i in range(7, 15):
        e.step(sample(i * DT), i * DT)
    assert e.state == 'TRACKING' and e.pinch_ready
    return e


def run(engine, plan, start=15, x=0.5):
    """Feed (pinch, dx) pairs; return every action, tagged with its frame index."""
    events = []
    for step, row in enumerate(plan):
        pinch, dx = row if isinstance(row, tuple) else (row, 0.0)
        x += dx
        for action in engine.step(sample((start + step) * DT, pinch=pinch, x=x),
                                  (start + step) * DT):
            events.append((step, action.kind))
    return events


def kinds(events):
    return [kind for _, kind in events]


def approach(target, frames=6, start=OPEN):
    """A physical closing motion: the ratio falls smoothly instead of teleporting."""
    return [start + (target - start) * (i + 1) / frames for i in range(frames)]


# --- the click itself -------------------------------------------------------------------

def test_a_quick_pinch_clicks_exactly_once_on_release():
    e = armed()
    plan = approach(CLOSED, frames=3) + [CLOSED] * 3 + [OPEN]
    events = run(e, plan)
    assert kinds(events).count('click') == 1


def test_a_slow_pinch_still_clicks():
    """There is no click window to fall out of: only the fingers opening triggers it."""
    e = armed()
    plan = approach(CLOSED, frames=15) + [CLOSED] * 4 + [OPEN]
    assert kinds(run(e, plan)).count('click') == 1


def test_two_pinches_produce_two_clicks():
    e = armed()
    plan = (approach(CLOSED) + [CLOSED] * 3 + [OPEN] * 3
            + approach(CLOSED) + [CLOSED] * 3 + [OPEN])
    assert kinds(run(e, plan)).count('click') == 2


def test_two_pinches_inside_one_breath_are_not_merged():
    """One open frame between them must still be two clicks, not one."""
    e = armed()
    plan = (approach(CLOSED) + [CLOSED] * 3 + [OPEN]
            + approach(CLOSED) + [CLOSED] * 3 + [OPEN])
    assert kinds(run(e, plan)).count('click') == 2


def test_the_click_does_not_drift_while_the_hand_keeps_moving():
    """A pinch freezes the cursor, so the click lands where the pinch started."""
    e = armed()
    plan = approach(CLOSED) + [CLOSED] * 3
    for step, pinch in enumerate(plan):
        e.step(sample((15 + step) * DT, pinch=pinch), (15 + step) * DT)
    assert e.state == 'PINCH_HOLD' and e.pinch_confirmed

    t = (15 + len(plan)) * DT
    actions = e.step(sample(t, pinch=CLOSED, x=0.51), t)     # a real move, if it were allowed
    assert not any(a.kind == 'move' for a in actions), 'the pinch must freeze the cursor'
    assert e.max_pinch_travel < e.profile.click_drag_travel, 'and stay inside click travel'

    actions = e.step(sample(t + DT, pinch=OPEN, x=0.51), t + DT)
    assert [a.kind for a in actions] == ['click'], 'so the click still lands cleanly'


# --- what must NOT click ------------------------------------------------------------------

@pytest.mark.parametrize('plateau', [0.42, 0.40, 0.36, 0.32, 0.26])
def test_a_graze_that_never_reaches_contact_never_clicks(plateau):
    """Crossing pinch_close is not enough; the fingertips must meet."""
    e = armed()
    plan = approach(plateau) + [plateau] * 6 + [OPEN]
    assert kinds(run(e, plan)) == []


def test_a_single_noisy_frame_cannot_click():
    e = armed()
    assert kinds(run(e, [0.10, OPEN, OPEN])) == []


def test_a_two_frame_pass_through_cannot_click():
    """Two samples is one clock tick of proximity, which a sweep can produce by accident."""
    e = armed()
    assert kinds(run(e, [0.35, 0.30, OPEN])) == []


def test_a_fist_with_the_fingers_together_never_clicks():
    """A closed hand brings the thumb and index tip together, but it is not a pinch:
    clenching the hand to pause must never fire the click that opening it would."""
    e = armed()
    for step in range(6):
        for action in e.step(sample((15 + step) * DT, pose='fist', pinch=CLOSED, x=0.5),
                             (15 + step) * DT):
            assert action.kind not in ('click', 'left_down')
    assert e.state == 'CLUTCH', 'a fist is an unusable pose and must release control'


def test_an_open_palm_after_a_pinch_releases_instead_of_clicking():
    """Clutching mid-pinch is an escape hatch, not a click."""
    e = armed()
    plan = approach(CLOSED) + [CLOSED] * 3
    for step, pinch in enumerate(plan):
        e.step(sample((15 + step) * DT, pinch=pinch), (15 + step) * DT)
    actions = e.step(sample(40 * DT, pose='open', pinch=OPEN), 40 * DT)
    assert not any(a.kind == 'click' for a in actions)


def test_losing_the_hand_mid_pinch_never_clicks():
    e = armed()
    plan = approach(CLOSED) + [CLOSED] * 3
    for step, pinch in enumerate(plan):
        e.step(sample((15 + step) * DT, pinch=pinch), (15 + step) * DT)
    assert not any(a.kind == 'click' for a in e.step(None, 60 * DT))


# --- click versus deliberate held drag ---------------------------------------------------

def test_travelling_while_pinched_is_a_drag_not_a_click():
    from airpad.config import Profile
    e = armed()
    plan = approach(CLOSED) + [CLOSED] * 3 + [(CLOSED, 0.03)] * 3 + [OPEN]
    events = run(e, plan)
    assert 'left_down' in kinds(events), events
    assert 'click' not in kinds(events), 'a drag must never also click'
    assert e.profile.click_drag_travel > 0


def test_a_drag_never_clicks_even_after_the_button_comes_up():
    e = armed()
    plan = approach(CLOSED) + [CLOSED] * 3 + [(CLOSED, 0.03)] * 3 + [OPEN] * 3
    events = run(e, plan)
    assert kinds(events).count('left_down') == 1
    assert kinds(events).count('click') == 0


def test_travel_just_below_the_boundary_still_clicks():
    """The boundary is shared, so there is no band where a pinch does nothing."""
    from airpad.config import Profile
    boundary = Profile().click_drag_travel
    e = armed()
    step = (boundary * 0.6) * 0.2          # palm units -> index travel in palm units
    plan = approach(CLOSED) + [CLOSED] * 3 + [(CLOSED, step)] + [OPEN]
    events = run(e, plan)
    assert kinds(events).count('click') == 1, events
    assert 'left_down' not in kinds(events), events


def test_travel_at_the_boundary_is_a_drag():
    from airpad.config import Profile
    e = armed()
    step = Profile().click_drag_travel * 0.2
    # Sustain the boundary crossing for three frames; a single peak is now tremor.
    plan = approach(CLOSED) + [CLOSED] * 6 + [(CLOSED, step * 1.01)] + [CLOSED] * 2 + [OPEN]
    assert 'left_down' in kinds(run(e, plan))


# --- regressions -------------------------------------------------------------------------

def test_contact_depth_is_measured_on_the_raw_signal():
    """A smoothed signal lags, bottoming out above pinch_contact and killing every click."""
    from airpad.config import Profile
    e = armed()
    plan = approach(CLOSED) + [CLOSED] * 3 + [OPEN]
    run(e, plan)
    assert e.pinch_depth <= CLOSED + 0.02, \
        f'the raw pinch reached {CLOSED}, but the recorded depth is {e.pinch_depth}'
    assert e.pinch_depth <= Profile().pinch_contact


def test_the_release_edge_is_not_delayed_by_smoothing():
    """The frame the fingers part is the frame the click fires; no filter may hold it back."""
    e = armed()
    plan = approach(CLOSED) + [CLOSED] * 3
    events = run(e, plan)
    assert 'click' not in kinds(events)
    released = run(e, [OPEN], start=15 + len(plan))
    assert released and released[0][0] == 0, f'the click must fire immediately: {released}'


def test_a_closing_pinch_freezes_the_pointer_before_contact():
    """Regression: this branch once compared a value with itself and never ran."""
    e = armed()
    actions = e.step(sample(15 * DT, pinch=.44, x=.51), 15 * DT)
    assert not any(a.kind == 'move' for a in actions)
    assert e.state in ('PINCH_APPROACH', 'PINCH_START')


def test_pointing_noise_does_not_freeze_the_pointer():
    """The freeze needs a real closing motion, not jitter around the release band."""
    e = armed()
    e.step(sample(15 * DT, pinch=.71, x=.5), 15 * DT)          # hovering just above guard
    actions = e.step(sample(16 * DT, pinch=.695, x=.52), 16 * DT)
    assert any(a.kind == 'move' for a in actions), 'a 0.015 blip must not stop the cursor'
    assert e.state == 'TRACKING'


def test_a_held_pinch_is_released_by_the_emergency_path_without_clicking():
    """The safety escapes stay ahead of the click logic."""
    e = armed()
    plan = approach(CLOSED) + [CLOSED] * 3
    for step, pinch in enumerate(plan):
        e.step(sample((15 + step) * DT, pinch=pinch), (15 + step) * DT)
    actions = e.step(sample(60 * DT, pose='unknown', pinch=OPEN), 60 * DT)
    assert not any(a.kind == 'click' for a in actions)
    assert any(a.kind == 'release_all' for a in actions)
