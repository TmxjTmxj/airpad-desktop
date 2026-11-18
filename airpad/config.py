"""Validated, versioned local profiles. No global mouse settings are changed."""
from dataclasses import asdict, dataclass, fields, replace
import json
import math
import os
from pathlib import Path


@dataclass
class Profile:
    sensitivity: float = 1000.0
    smoothing: float = 1.5
    scroll_speed: float = 480.0
    pinch_close: float = .42
    pinch_release: float = .62
    pinch_open: float = .78
    # Fingertip contact. A click needs a real pinch, not a graze: the pinch must reach
    # this depth for at least two frames, so merely crossing pinch_close does not fire a
    # click and a single noisy landmark cannot fake contact. It is tied to this profile's
    # own pinch scale (0.6 x pinch_close), never an absolute constant: a measured close
    # threshold of 0.60 would demand an absolute 0.25 that such a hand may never reach,
    # and every click would silently vanish.
    pinch_contact: float = .252
    # Minimum time the fingers must stay together before a pinch counts as intentional.
    # Together with three below-close samples this means a pinch lasting about three
    # frames (100 ms at 30 fps); a two-frame pass-through is rejected as noise.
    pinch_confirm: float = .06
    # Palm travel, measured against the scale at pinch onset. Fingertip curling does
    # not express drag intent. A drag additionally needs contact and a deliberate hold.
    click_drag_travel: float = .12
    drag_hold: float = .18
    dominant_hand: str = 'auto'
    camera: int = 0
    camera_backend: str = 'auto'
    zoom_enabled: bool = False
    three_finger_enabled: bool = False
    # Two-finger scrolling is geometrically identical to pointing with a relaxed middle
    # finger, so it is opt-in: by default every index-extended pose drives the pointer
    # and can never freeze the cursor by being mistaken for a scroll.
    two_finger_scroll: bool = False
    natural_scroll: bool = True
    calibrated: bool = False
    palm_reference: float = 0.0
    # Per-finger extension thresholds measured from the owner's own hand, in
    # thumb/index/middle/ring/pinky order. Empty means "use the built-in defaults".
    finger_thresholds: tuple = ()
    # Where "partially extended" starts for each finger; null entries fall back to a
    # ratio of the threshold above.
    finger_marginals: tuple = ()
    revision: int = 1

    def validate(self):
        for key, low, high in [('sensitivity',100,4000),('smoothing',.5,8),
                               ('scroll_speed',60,2400),('palm_reference',0,2),
                               ('drag_hold',.18,1.0)]:
            value=getattr(self,key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
                raise ValueError(f'{key} must be between {low} and {high}')
        for key in ('pinch_close','pinch_release','pinch_open','pinch_contact','pinch_confirm','click_drag_travel'):
            value=getattr(self,key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                raise ValueError('Pinch thresholds must be finite numbers')
        if not .05<=self.pinch_close<self.pinch_release<self.pinch_open<=1.5:
            raise ValueError('Pinch requires close < release < open')
        if self.pinch_release-self.pinch_close<.04:
            raise ValueError('Pinch hysteresis must be at least .04')
        if not .02<=self.pinch_contact<=self.pinch_close-.05+1e-9:
            # The tolerance matters: 0.6 - 0.05 is 0.5499999999999999 in binary floating
            # point, so a legitimately rounded depth of 0.55 would be rejected here and a
            # completed calibration would fail at the last step.
            raise ValueError('pinch_contact must be clearly deeper than pinch_close')
        if not .02<=self.pinch_confirm<=.5:
            raise ValueError('pinch_confirm must be between .02 and .5 seconds')
        if not .02<=self.click_drag_travel<=.6:
            raise ValueError('click_drag_travel must be between .02 and .6 palm units')
        if self.dominant_hand not in ('auto','left','right'):
            raise ValueError('Hand must be auto, left or right')
        if self.camera_backend not in ('auto','msmf','dshow'):
            raise ValueError('Unknown camera backend')
        if type(self.camera) is not int or not 0<=self.camera<=9:
            raise ValueError('Camera index must be 0–9')
        if type(self.revision) is not int or self.revision<1:
            raise ValueError('Invalid profile revision')
        for key in ('zoom_enabled','three_finger_enabled','natural_scroll','calibrated','two_finger_scroll'):
            if type(getattr(self,key)) is not bool:
                raise ValueError(f'{key} must be boolean')
        thresholds=self.finger_thresholds
        if thresholds in (None,(),[]):
            self.finger_thresholds=()
        else:
            if isinstance(thresholds,(list,tuple)):
                thresholds=tuple(thresholds)
            else:
                raise ValueError('finger_thresholds must be a sequence of five numbers')
            if len(thresholds)!=5:
                raise ValueError('finger_thresholds needs one value per finger')
            for value in thresholds:
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not .15<=value<=.95:
                    raise ValueError('Each finger threshold must be between 0.15 and 0.95')
            self.finger_thresholds=tuple(float(value) for value in thresholds)
        marginals=self.finger_marginals
        if marginals in (None,(),[]):
            self.finger_marginals=()
        else:
            if isinstance(marginals,(list,tuple)):
                marginals=tuple(marginals)
            else:
                raise ValueError('finger_marginals must be a sequence of five entries')
            if len(marginals)!=5:
                raise ValueError('finger_marginals needs one entry per finger')
            cleaned=[]
            for value in marginals:
                if value is None:
                    cleaned.append(None)
                    continue
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not .10<=value<=.95:
                    raise ValueError('Each finger marginal must be null or between 0.10 and 0.95')
                cleaned.append(float(value))
            self.finger_marginals=tuple(cleaned)
        return self

    @staticmethod
    def contact_for(close):
        """The click depth that belongs to a given pinch_close.

        0.6 x close reproduces the 0.25 that was verified against the default 0.42, and
        scales correctly for a hand measured with a wider pinch range.
        """
        return round(min(float(close) - .05, max(.02, .6 * float(close))), 4)

    def updated(self, changes):
        allowed={f.name for f in fields(self)}-{'revision'}
        if not set(changes)<=allowed:
            raise ValueError('Unknown profile setting')
        changes=dict(changes)
        if 'pinch_close' in changes and 'pinch_contact' not in changes:
            # The contact depth is only meaningful relative to the close threshold, so
            # moving one without the other would leave a stale, possibly unreachable gate.
            changes['pinch_contact']=self.contact_for(changes['pinch_close'])
        return replace(self,**changes,revision=self.revision+1).validate()

    def save(self,path):
        self.validate()
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix('.tmp')
        data={'schema_version':1,**asdict(self)}
        with tmp.open('w',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2,allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp,path)

    @classmethod
    def load(cls,path):
        path=Path(path)
        if not path.exists():
            return cls()
        data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or data.pop('schema_version',None)!=1:
            raise ValueError('Unsupported profile schema')
        if not set(data)<={f.name for f in fields(cls)}:
            raise ValueError('Unknown profile fields')
        if 'pinch_contact' not in data and 'pinch_close' in data:
            # Profiles written before the contact gate existed carry a measured pinch
            # range but no depth. Backfill it from that range instead of the default, or
            # a wide-range hand would load a gate it can never satisfy.
            data['pinch_contact']=cls.contact_for(data['pinch_close'])
        return cls(**data).validate()
