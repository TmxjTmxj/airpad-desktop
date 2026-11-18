import math
import pytest


def test_core_module_exists():
    import importlib.util
    assert importlib.util.find_spec('airpad') is not None, 'AIRPAD package is not implemented'


def test_config_rejects_inverted_pinch_thresholds(tmp_path):
    from airpad.config import Profile
    with pytest.raises(ValueError):
        Profile(pinch_close=.5, pinch_release=.3).validate()
    p = Profile(sensitivity=900)
    p.save(tmp_path / 'profile.json')
    assert Profile.load(tmp_path / 'profile.json').sensitivity == 900


def sample(t, pose='pointer', pinch=.8, x=.5, scale=.2):
    from airpad.types import HandSample
    return HandSample(t,(x,.3),(x,.6),scale,pinch,pose,(x,.3),.7)


def armed():
    from airpad.core import GestureEngine
    from airpad.config import Profile
    e = GestureEngine(Profile())
    for i in range(7): e.step(sample(i*.033,'open'),i*.033)
    for i in range(7,15): e.step(sample(i*.033),i*.033)
    assert e.state == 'TRACKING'
    return e


def test_short_pinch_commits_once_only_on_valid_release():
    e = armed()
    events=[]
    for i in range(15,19): events += e.step(sample(i*.033,pinch=.15),i*.033)
    assert not any(a.kind in ('click','left_down') for a in events)
    events += e.step(sample(19*.033,pinch=.8),19*.033)
    events += e.step(sample(20*.033,pinch=.8),20*.033)
    assert [a.kind for a in events].count('click') == 1


def test_loss_during_pinch_never_clicks():
    e=armed()
    for i in range(15,19): e.step(sample(i*.033,pinch=.15),i*.033)
    actions=e.step(None,19*.033)
    for i in range(20,25): actions += e.step(sample(i*.033,pinch=.8),i*.033)
    assert not any(a.kind in ('click','left_down') for a in actions)


def test_drag_loss_releases_and_closed_reacquisition_cannot_redrag():
    e=armed()
    actions=[]
    for i in range(15,25): actions += e.step(sample(i*.033,pinch=.15,x=.5+max(0,i-19)*.015),i*.033)
    assert any(a.kind=='left_down' for a in actions)
    release=e.step(None,25*.033)
    assert any(a.kind in ('left_up','release_all') for a in release)
    actions=[]
    for i in range(26,38): actions += e.step(sample(i*.033,pinch=.15),i*.033)
    assert not any(a.kind in ('click','left_down') for a in actions)


def test_clutch_reposition_does_not_emit_jump():
    e=armed()
    for i in range(15,22):
        assert not any(a.kind=='move' for a in e.step(sample(i*.033,'open',x=.8),i*.033))
    movements=[]
    for i in range(22,31): movements += e.step(sample(i*.033,x=.8),i*.033)
    assert not any(a.kind=='move' and abs(a.x)>1 for a in movements)


def test_stale_frame_cancels_pinch_instead_of_completing_it():
    e=armed()
    for i in range(15,19): e.step(sample(i*.033,pinch=.15),i*.033)
    actions=e.step(sample(1.5,pinch=.8),1.5)
    assert not any(a.kind in ('click','left_down','move') for a in actions)


def test_nonfinite_sample_is_rejected():
    e=armed()
    actions=e.step(sample(.5,x=math.nan),.5)
    assert not any(a.kind in ('move','click','left_down') for a in actions)


def test_motion_scale_invariance():
    from airpad.filtering import RelativeMotion
    m1,m2=RelativeMotion(1000,1.5),RelativeMotion(1000,1.5)
    out1=[];out2=[]
    for i in range(60):
        out1.append(m1.update((.5+i*.001,.3),.2,i/30))
        out2.append(m2.update((1.+i*.002,.6),.4,i/30))
    assert sum(p[0] for p in out1) == pytest.approx(sum(p[0] for p in out2),abs=1)
    assert sum(p[0] for p in out1) > 100


def test_pinch_approach_freezes_motion_before_contact():
    e=armed()
    actions=e.step(sample(15*.033,pinch=.44,x=.51),15*.033)
    assert not any(a.kind=='move' for a in actions)
    assert e.state=='PINCH_APPROACH'


def test_three_finger_swipe_only_fires_once_until_pose_exits():
    e=armed();e.profile.three_finger_enabled=True
    events=[]
    for i in range(15,48):
        events += e.step(sample(i*.033,'three',x=.5+max(0,i-21)*.012),i*.033)
    assert sum(a.kind=='desktop_right' for a in events)==1


def test_synthetic_replay_10000_events_never_clicks_without_pinch():
    import random
    rng=random.Random(42)
    e=armed()
    actions=[]
    for i in range(15,10015):
        pose=rng.choice(['open','pointer','unknown','two'])
        actions+=e.step(sample(i*.033,pose,x=.5+rng.uniform(-.001,.001)),i*.033)
    assert not any(a.kind in ('click','left_down') for a in actions)


def test_two_finger_translation_scrolls_without_click_or_pointer():
    from dataclasses import replace
    e=armed();e.profile.two_finger_scroll=True;events=[]
    for i in range(15,45):
        s=sample(i*.033,'two')
        s=replace(s,two_center=(.5,.3+max(0,i-21)*.004),
                  palm=(.5,.6+max(0,i-21)*.004))
        events+=e.step(s,s.t)
    assert any(a.kind=='wheel' and a.y>0 for a in events)
    assert not any(a.kind in ('click','move','left_down','zoom') for a in events)


def test_a_two_pose_still_points_when_scrolling_is_disabled():
    """The default: a relaxed middle finger must never freeze the cursor."""
    from dataclasses import replace
    e=armed();events=[]
    for i in range(15,45):
        s=replace(sample(i*.033,'two'),index=(.5+max(0,i-21)*.002,.3))
        events+=e.step(s,s.t)
    assert any(a.kind=='move' for a in events)
    assert not any(a.kind in ('wheel','zoom','click','left_down') for a in events)
    assert e.state=='TRACKING'


def test_two_finger_separation_zoom_is_opt_in_and_locked():
    from dataclasses import replace
    e=armed();e.profile.zoom_enabled=True;e.profile.two_finger_scroll=True;events=[]
    for i in range(15,45):
        s=replace(sample(i*.033,'two'),two_distance=.7+max(0,i-21)*.025)
        events+=e.step(s,s.t)
    assert any(a.kind=='zoom' and a.y>0 for a in events)
    assert not any(a.kind in ('wheel','move','click') for a in events)
