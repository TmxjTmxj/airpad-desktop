import math
import pytest


def hand():
    points=[(.5,.8,0.)]*21
    points[0]=(.5,.82,0.)
    points[1]=(.39,.72,0.);points[2]=(.32,.65,0.)
    points[3]=(.25,.57,0.);points[4]=(.18,.49,0.)
    for base,x in ((5,.4),(9,.5),(13,.6),(17,.7)):
        for j,y in enumerate((.6,.45,.32,.2)):
            points[base+j]=(x,y,0.)
    return {'landmarks':points,'handedness':'Right','handedness_score':.95}


def test_open_palm_geometry_and_scale_invariance():
    from airpad.geometry import extract_sample
    data=hand()
    a,q=extract_sample(data,1.0,640,480,1)
    assert a.valid and a.pose=='open'
    assert q['valid']
    shrunk={**data,'landmarks':[(x*.5+.1,y*.5+.1,z*.5) for x,y,z in data['landmarks']]}
    b,_=extract_sample(shrunk,1.0,640,480,1)
    assert b.pinch==pytest.approx(a.pinch)
    assert b.pose=='open'


def test_degenerate_and_nonfinite_landmarks_rejected():
    from airpad.geometry import extract_sample
    data=hand();data['landmarks']=[(.5,.5,0.)]*21
    sample,quality=extract_sample(data,1.,640,480,1)
    assert sample is None and not quality['valid']
    data=hand();data['landmarks'][8]=(math.nan,.2,0.)
    assert extract_sample(data,1.,640,480,1)[0] is None


def test_ambiguous_crossing_does_not_select_a_random_hand():
    from airpad.geometry import HandSelector
    h=hand()
    selector=HandSelector('auto')
    assert selector.select([h],0.) is not None
    assert selector.select([h,dict(h)],.03) is None


def test_user_hand_preference_is_respected():
    from airpad.geometry import HandSelector
    selector=HandSelector('left')
    assert selector.select([hand()],0.) is None


def test_a_hand_mostly_in_frame_is_accepted():
    """MediaPipe extrapolates joints that leave the frame; a webcam at the bottom of a
    monitor means the wrist is routinely just outside it."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import hand_model
    from airpad.geometry import extract_sample
    for outside_count in (1, 3, 6):
        points = [list(p) for p in hand_model.landmarks('pointer')]
        for index in range(outside_count):
            points[index] = [points[index][0], 1.15, 0.0]
        sample, quality = extract_sample(
            {'landmarks': [tuple(p) for p in points], 'handedness': 'Right',
             'handedness_score': 0.9}, 0.0, 640, 480)
        assert quality['valid'], (outside_count, quality['reason'])
        assert quality['offscreen'] == outside_count


def test_a_hand_mostly_out_of_frame_is_rejected():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import hand_model
    from airpad.geometry import extract_sample
    points = [list(p) for p in hand_model.landmarks('pointer')]
    for index in range(14, 21):
        points[index] = [points[index][0], 1.20, 0.0]
    sample, quality = extract_sample(
        {'landmarks': [tuple(p) for p in points], 'handedness': 'Right',
         'handedness_score': 0.9}, 0.0, 640, 480)
    assert sample is None and not quality['valid']
    assert 'in view' in quality['reason']


def test_two_hands_pick_the_pointing_one():
    """Resting your other hand in frame must not silently disable the pointer."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import hand_model
    from airpad.geometry import HandSelector
    pointing = hand_model.as_hand(pose='pointer', palm=(0.45, 0.55))
    resting = hand_model.as_hand(pose='unknown', curls=(1.0, 1.0, 1.0, 1.0), palm=(0.95, 0.60))
    selector = HandSelector('auto')
    chosen = selector.select([resting, pointing], 0.0)
    assert chosen is not None, selector.reason
    assert selector.center(chosen)[0] < 0.7, 'the pointing hand should win'


def test_two_identical_hands_are_still_refused():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import hand_model
    from airpad.geometry import HandSelector
    left = hand_model.as_hand(pose='pointer', palm=(0.40, 0.55))
    right = hand_model.as_hand(pose='pointer', palm=(0.95, 0.55))
    selector = HandSelector('auto')
    assert selector.select([left, right], 0.0) is None
    assert 'alike' in selector.reason
