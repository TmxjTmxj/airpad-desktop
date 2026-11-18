"""Deterministic gesture state machine. This module never touches OS input.

Control logic is deliberately direct, because this tool exists to control a computer:
a hand showing an index finger takes control immediately (no open-palm prerequisite),
an open palm is the clutch that releases and lets you reposition, a pinch clicks or drags,
and two fingers scroll. Everything unsafe fails toward releasing input.
"""
import math
from collections import deque
from statistics import median
from .types import Action, HandSample
from .filtering import OneEuro, RelativeMotion

# A pinch needs this many frames below the close threshold, on top of the confirm time.
# Two frames is a single clock tick at 30 fps, which noise alone can produce.
MIN_PINCH_SAMPLES = 3
# A click additionally needs fingertip contact for this many frames. One frame is a single
# noisy landmark, and requiring two makes a lone spike unable to fake a contact depth.
MIN_CONTACT_SAMPLES = 2


class GestureEngine:
    def __init__(self,profile):
        self.profile=profile.validate()
        self.motion=RelativeMotion(profile.sensitivity,profile.smoothing)
        self.pinch_filter=OneEuro(min_cutoff=1.0,beta=0.5)
        self.reset()

    def reset(self):
        self.state='READY'
        self.last_t=None
        self.owner=None
        self.candidate=None
        self.candidate_since=0.
        self.candidate_count=0
        self.pinch_start=0.
        self.pinch_confirmed=False
        self.pinch_samples=0
        self.pinch_close_since=0.
        self.pinch_palm=(0.,0.)
        self.pinch_index=(0.,0.)
        self.pinch_scale=1.
        self.max_pinch_travel=0.
        self.palm_history=deque(maxlen=3)
        self.travel_candidate=None
        self.travel_since=0.
        self.travel_samples=0
        self.release_samples=0
        self.pinch_depth=1.0
        self.pinch_contact_samples=0
        self.pinch_contact_confirmed=False
        self.filtered_pinch=1.0
        self.pinch_ratio=0.
        self.previous_pinch=1.0
        self.pinch_filter.reset()
        # A closed hand acquired after reset/loss is not a new pinch. Only a
        # visible separation can authorize the next click/drag; pointing itself
        # remains available immediately, without an open-palm prerequisite.
        self.pinch_ready=False
        self.velocity=0.
        self.last_delta=(0,0)
        self.motion.reset()
        self.mode_start=None
        self.mode_previous=None
        self.mode_distance=0.
        self.mode_ratio=0.
        self.mode_time=0.
        self.mode_scale=1.
        self.two_samples=0
        self.two_tap_possible=False
        self.latch_until=0.

    def cancel(self,state='LOST_TRACKING'):
        was_drag=self.state=='DRAGGING'
        self.reset()
        self.state=state
        return [Action('left_up'),Action('release_all')] if was_drag else [Action('release_all')]

    def _dwell(self,pose,t,duration=.12):
        if self.candidate!=pose:
            self.candidate=pose;self.candidate_since=t;self.candidate_count=1
            return False
        self.candidate_count+=1
        return self.candidate_count>=3 and t-self.candidate_since>=duration

    @staticmethod
    def _distance(a,b):
        return math.hypot(a[0]-b[0],a[1]-b[1])

    def _enter_two(self,sample,t):
        # Keep the first observation: dwell must not eat a short tap or the first
        # part of a scroll. Dedicated modes still require stable pose confirmation.
        self.state='TWO_CANDIDATE'
        self.mode_start=sample.palm
        self.mode_previous=sample.palm
        self.mode_distance=sample.two_distance
        self.mode_time=t
        self.mode_scale=sample.scale
        self.two_samples=1
        self.two_tap_possible=True
        self.candidate=None

    def _start_pinch(self,sample,t):
        self.state='PINCH_START';self.pinch_start=t;self.pinch_close_since=t
        self.pinch_samples=1;self.pinch_palm=self._stable_palm();self.pinch_index=sample.index
        self.pinch_scale=sample.scale;self.max_pinch_travel=0.
        self.travel_candidate=None;self.travel_samples=0;self.release_samples=0
        self.pinch_depth=sample.pinch
        self.pinch_contact_samples=int(sample.pinch<=self.profile.pinch_contact)
        self.pinch_contact_confirmed=False;self.pinch_confirmed=False
        self.motion.reset()

    def _stable_palm(self):
        return tuple(median(point[j] for point in self.palm_history) for j in range(2))

    def _update_pinch_travel(self,sample,t):
        """A peak cannot erase a click: require sustained displacement in one direction."""
        vector=tuple((sample.palm[j]-self.pinch_palm[j])/self.pinch_scale for j in range(2))
        travel=math.hypot(*vector)
        if travel<self.profile.click_drag_travel:
            self.travel_candidate=None;self.travel_samples=0
            return
        candidate=self.travel_candidate
        coherent=(candidate is not None and
                  sum(vector[j]*candidate[j] for j in range(2))>=
                  .75*travel*math.hypot(*candidate))
        if not coherent:
            self.travel_candidate=vector;self.travel_since=t;self.travel_samples=1
            return
        self.travel_samples+=1
        if self.travel_samples>=3 and t-self.travel_since>=.06-1e-9:
            stable_travel=self._distance(self._stable_palm(),self.pinch_palm)/self.pinch_scale
            if stable_travel>=self.profile.click_drag_travel:
                self.max_pinch_travel=max(self.max_pinch_travel,stable_travel)

    @staticmethod
    def _drives_pointer(pose,profile):
        """Any pose with an extended index drives the pointer, unless its own dedicated
        mode owns it. This is what stops a relaxed middle finger from freezing the cursor:
        two-finger scrolling is opt-in, so by default a 'two' pose is still a pointer."""
        if pose=='pointer':
            return True
        if pose=='two':
            return not profile.two_finger_scroll
        if pose=='three':
            return not profile.three_finger_enabled
        return False

    def _pinch_intent(self,sample):
        """Release commits contact at its frozen target, unless the palm travelled.

        Movement without the deliberate drag hold is cancelled; it is neither a
        stationary click nor a held drag. Release-frame rebound is never counted.
        """
        if not self.pinch_confirmed:
            return None
        if self.max_pinch_travel>=self.profile.click_drag_travel:
            return None                      # it was a drag; the button was already released
        if not self.pinch_contact_confirmed:
            return None                      # a graze, never real fingertip contact
        return Action('click')

    def step(self,sample,now):
        self.last_delta=(0,0)
        if sample is None or not sample.valid:
            return self.cancel()
        numbers=(*sample.index,*sample.palm,*sample.two_center,sample.scale,sample.pinch,sample.t,now,sample.two_distance)
        if not all(math.isfinite(x) for x in numbers) or sample.scale<.015 or now-sample.t>.08 or sample.t>now+.005:
            return self.cancel()
        if self.last_t is not None:
            gap=sample.t-self.last_t
            if gap<=0 or gap>.200001:
                return self.cancel()
            if gap>.15:
                self.candidate=None
                self.two_tap_possible=False
                self.pinch_samples=0;self.pinch_contact_samples=0
            if gap>.1:
                self.palm_history.clear();self.travel_candidate=None
                self.travel_samples=0;self.release_samples=0
        if self.owner is not None and sample.track_id!=self.owner:
            return self.cancel()
        self.owner=sample.track_id
        self.last_t=sample.t
        self.palm_history.append(sample.palm)
        t=sample.t
        p=self.profile
        pose=sample.pose
        # Decisions use the RAW pinch ratio, because that is the signal the thresholds were
        # measured from (tools/hand_check.py collects unfiltered values). An earlier version
        # filtered the signal and then compared it against those thresholds; a One Euro lags
        # at every edge, so a fully opened hand still read 0.59 and never crossed
        # pinch_release, while pinch_depth bottomed out at 0.27 although the fingers really
        # reached 0.18. Both defects silently killed every click. Noise is rejected by the
        # sample/time confirmation below instead, which costs no edge lag. The smoothed value
        # is still computed, for the readout only.
        pinch=sample.pinch
        self.filtered_pinch=self.pinch_filter.update(pinch,t)
        self.pinch_ratio=sample.pinch
        previous_pinch=self.previous_pinch
        self.previous_pinch=pinch
        if pose!='unknown' and sample.pinch>=p.pinch_release:
            self.pinch_ready=True

        # Open palm is the clutch: release everything and let the user reposition.
        if pose=='open':
            release=[Action('left_up'),Action('release_all')] if self.state=='DRAGGING' else []
            if self.state not in ('READY','CLUTCH','LOST_TRACKING'):
                self.motion.reset();self.candidate=None
            self.state='CLUTCH'
            return release

        # Take control as soon as a usable pose appears. There is no arming ceremony:
        # the baseline is seeded from this frame, so the first movement is always zero.
        if self.state in ('READY','CLUTCH','LOST_TRACKING'):
            if pose=='two' and p.two_finger_scroll:
                self.motion.reset()
                self._enter_two(sample,t)
            elif pose=='three' and p.three_finger_enabled and t>=self.latch_until:
                self.motion.reset()
                if self._dwell('three',t):
                    self.state='THREE_PENDING';self.mode_start=sample.palm;self.mode_time=t
            elif self._drives_pointer(pose,p):
                self.state='TRACKING';self.candidate=None;self.motion.reset()
                self.motion.update(sample.index,sample.scale,t)
            return []

        if self.state in ('PINCH_START','PINCH_HOLD','DRAGGING','PINCH_APPROACH'):
            # A closed fist is not a pinch: never keep a button held on an unusable pose.
            if pose=='unknown':
                return self.cancel()
            elapsed=t-self.pinch_start
            if self.state=='PINCH_APPROACH':
                guard=(p.pinch_release+p.pinch_open)/2
                if pinch<p.pinch_close:
                    self._start_pinch(sample,t)
                elif pinch>=guard or elapsed>.25:
                    self.state='CLUTCH';self.candidate=None;self.motion.reset()
                return []
            if pinch>=p.pinch_release:
                self.release_samples+=1
                # Clear opening stays immediate. The ambiguous release/open band
                # requires two frames, avoiding an accidental click or dropped drag.
                if pinch<p.pinch_open and self.release_samples<2:
                    return []
                if self.state=='DRAGGING':
                    result=[Action('left_up')]
                else:
                    click=self._pinch_intent(sample)
                    result=[click] if click else []
                self.state='CLUTCH';self.candidate=None;self.motion.reset()
                return result
            self.release_samples=0
            # Only still-pinched observations contribute intent. Opening the fingers
            # can shift both landmarks abruptly; that rebound must not erase a click.
            self._update_pinch_travel(sample,t)
            self.pinch_depth=min(self.pinch_depth,pinch)
            # Schmitt-style bands tolerate small landmark tremor after entry, but
            # cannot enter contact without crossing the calibrated raw threshold.
            contact_limit=p.pinch_contact+(.08*p.pinch_close if self.pinch_contact_samples else 0)
            self.pinch_contact_samples=(self.pinch_contact_samples+1 if pinch<=contact_limit else 0)
            if self.pinch_contact_samples>=MIN_CONTACT_SAMPLES:
                self.pinch_contact_confirmed=True
            if self.state!='DRAGGING' and (elapsed>2 or (not self.pinch_confirmed and elapsed>.25)):
                return self.cancel('READY')
            close_limit=p.pinch_close+(.05*(p.pinch_release-p.pinch_close) if self.pinch_samples else 0)
            if pinch<close_limit:
                if self.pinch_samples==0:
                    self.pinch_close_since=t
                self.pinch_samples+=1
                if not self.pinch_confirmed and self.pinch_samples>=MIN_PINCH_SAMPLES and t-self.pinch_close_since>=p.pinch_confirm:
                    self.pinch_confirmed=True
                    if self.state!='DRAGGING': self.state='PINCH_HOLD'
            else:
                self.pinch_samples=0
            if (self.pinch_confirmed and self.pinch_contact_confirmed
                    and pinch<p.pinch_close and elapsed>=p.drag_hold
                    and self.max_pinch_travel>=p.click_drag_travel and self.state!='DRAGGING'):
                self.state='DRAGGING';self.motion.reset()
                self.motion.update(sample.palm,sample.scale,t)
                return [Action('left_down')]
            if self.state=='DRAGGING':
                return self._move(sample.palm,sample)
            return []

        if self.state in ('TWO_CANDIDATE','TWO_PENDING','SCROLLING','ZOOMING'):
            if pose=='unknown':
                return self.cancel()
            if pose!='two' or pinch<p.pinch_close:
                tap=(self.state=='TWO_PENDING' and self.two_tap_possible
                     and pose=='pointer' and pinch>=p.pinch_release
                     and .12-1e-9<=t-self.mode_time<=.4+1e-9)
                self.candidate=None;self.motion.reset()
                self.state='TRACKING' if pose=='pointer' else 'CLUTCH'
                if pose=='pointer':
                    self.motion.update(sample.index,sample.scale,t)
                return [Action('right_click')] if tap else []
            # Curling the middle finger on exit changes two_center even while the
            # palm is still. Use hand translation for scrolling and tap rejection.
            total=self._distance(sample.palm,self.mode_start)/self.mode_scale
            ratio=max(sample.two_distance,.01)
            zoom=abs(math.log(ratio/max(self.mode_distance,.01)))
            if total>=.10 or (p.zoom_enabled and zoom>=.12) or t-self.mode_time>.4:
                self.two_tap_possible=False
            if self.state=='TWO_CANDIDATE':
                self.two_samples+=1
                if self.two_samples<3 or t-self.mode_time<.08:
                    return []
                self.state='TWO_PENDING'
            if self.state=='TWO_PENDING':
                if p.zoom_enabled and zoom>=.12 and zoom/.12>1.5*total/.10:
                    if self._dwell('zoom',t,.06):
                        self.state='ZOOMING';self.mode_ratio=max(self.mode_distance,.01)
                elif total>=.10 and (not p.zoom_enabled or total/.10>1.5*zoom/.12):
                    if self._dwell('scroll',t,.06):
                        self.state='SCROLLING'
                else:
                    self.candidate=None
                if self.state=='TWO_PENDING':
                    return []
            if self.state=='ZOOMING':
                delta=math.log(ratio/self.mode_ratio)*600
                self.mode_ratio=ratio
                return [Action('zoom',y=max(-120,min(120,delta)))] if abs(delta)>.5 else []
            dx=(sample.palm[0]-self.mode_previous[0])/self.mode_scale
            dy=(sample.palm[1]-self.mode_previous[1])/self.mode_scale
            self.mode_previous=sample.palm
            direction=1 if p.natural_scroll else -1
            return [Action('wheel',x=-dx*p.scroll_speed*direction,y=dy*p.scroll_speed*direction)]

        if self.state in ('THREE_PENDING','LATCHED'):
            if pose!='three':
                self.state='CLUTCH';self.candidate=None
                return []
            if self.state=='LATCHED': return []
            dx=(sample.palm[0]-self.mode_start[0])/sample.scale
            dy=(sample.palm[1]-self.mode_start[1])/sample.scale
            elapsed=t-self.mode_time
            if elapsed>.65:
                self.state='LATCHED';return []
            if elapsed>=.12:
                kind=None
                if dy<-.45 and abs(dy)>1.8*abs(dx):kind='task_view'
                if abs(dx)>.45 and abs(dx)>1.8*abs(dy):kind='desktop_right' if dx>0 else 'desktop_left'
                if kind:
                    self.state='LATCHED';self.latch_until=t+.6
                    return [Action(kind)]
            return []

        # TRACKING: a closing pinch starts a click or a drag; otherwise move the pointer.
        if pose=='unknown':
            return self.cancel()
        # Only a pose that drives the pointer may start a pinch. Closing a fist also brings
        # the thumb and index tip close together, so without this gate clenching the hand to
        # pause would arm a click and fire it on opening. A pinch already under way is left
        # alone, so a tight pinch that momentarily classifiers as a fist still completes.
        drives=self._drives_pointer(pose,p)
        if drives and self.pinch_ready and pinch<p.pinch_close:
            self._start_pinch(sample,t)
            return []
        guard=(p.pinch_release+p.pinch_open)/2
        # Compare against the PREVIOUS frame's value (the local), not self.previous_pinch,
        # which this step has already overwritten with the current one: that made the
        # difference identically zero and this branch dead code. The 0.03 step keeps
        # landmark jitter from freezing the pointer on a hand that is merely pointing.
        if drives and self.pinch_ready and pinch<guard and previous_pinch-pinch>.03:
            self.state='PINCH_APPROACH';self.pinch_start=t
            self.pinch_palm=sample.palm;self.pinch_index=sample.index
            self.max_pinch_travel=0.;self.pinch_depth=pinch;self.pinch_contact_samples=0
            self.pinch_confirmed=False;self.pinch_samples=0;self.motion.reset()
            return []
        if pose=='two' and p.two_finger_scroll:
            self.motion.reset()
            self._enter_two(sample,t)
            return []
        if pose=='three' and p.three_finger_enabled and t>=self.latch_until:
            self.motion.reset()
            if self._dwell('three',t):
                self.state='THREE_PENDING';self.mode_start=sample.palm;self.mode_time=t
            return []
        if not self._drives_pointer(pose,p):
            self.state='CLUTCH';self.motion.reset();self.candidate=None
            return []
        self.candidate=None
        return self._move(sample.index,sample)

    def _move(self,point,sample):
        delta=self.motion.update(point,sample.scale,sample.t)
        self.last_delta=delta;self.velocity=self.motion.speed
        return [Action('move',*delta)] if any(delta) else []
