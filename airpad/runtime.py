"""Qt controller joining isolated vision/input workers and deterministic core."""
from collections import deque
from dataclasses import asdict
from pathlib import Path
import time

from PySide6.QtCore import QObject,QTimer,Signal

from .config import Profile
from .core import GestureEngine
from .geometry import FingerStability,HandSelector,extract_sample
from .calibration import Calibrator, PHASES, measurement_row
from .telemetry import SessionLog

# How long a hand may stay visible-but-unusable before the app explains what to do.
GUIDANCE_AFTER_SECONDS = 6.0
# These modes can be entered directly after clutching, without single-finger tracking.
BROKER_ARM_STATES = frozenset({'TRACKING', 'TWO_CANDIDATE', 'TWO_PENDING',
                               'SCROLLING', 'ZOOMING', 'THREE_PENDING'})


class AirpadController(QObject):
    updated=Signal(dict)
    preview=Signal(object)
    problem=Signal(str)

    def __init__(self,data_dir,*,vision_factory=None,broker_factory=None):
        super().__init__()
        from .vision import VisionClient
        from .broker import BrokerClient
        self._vision_factory=vision_factory or VisionClient
        self._broker_factory=broker_factory or BrokerClient
        self.data_dir=Path(data_dir);self.data_dir.mkdir(parents=True,exist_ok=True)
        self.error=''
        try:self.profile=Profile.load(self.data_dir/'profile.json')
        except (OSError,ValueError,TypeError) as exc:
            self.profile=Profile();self.error=f'Profile could not be loaded; defaults restored: {exc}'
        self.engine=GestureEngine(self.profile)
        self.selector=HandSelector(self.profile.dominant_hand)
        self.stability=FingerStability(self.profile.finger_thresholds or None,
                                       self.profile.finger_marginals or None)
        self.vision=None;self.broker=None
        # Control mode is the point of this tool, so it is the default: the camera starts
        # and a tracked hand drives the desktop. Practice mode stays available as an
        # explicit opt-in that never creates an input broker.
        self.enabled=False;self.practice=False;self.debug=False
        self.camera_state='off';self.hand_state='No hand detected'
        self._broker_active=False;self._closed=False
        self._enable_attempts=0
        self._acknowledge_emergency=False
        self._unusable_since=None;self._guidance_given=False
        self._last_received=0.;self._camera_started=0.;self._last_status=0.
        self._last_preview=0.;self._last_log=0.;self._last_state=''
        self._frame_seq=-1;self._history=deque(maxlen=120)
        self._fps=0.;self._capture_fps=0.;self._inference=0.;self._latency=0.
        self._scale=0.;self._quality=0.
        self.practice_x=.5;self.practice_y=.5;self.click_count=0;self.dragging=False
        self.right_click_count=0;self.scroll_count=0
        self.practice_scroll_x=0.;self.practice_scroll_y=0.
        self.calibration=None;self._calibration_step=0;self._calibration_text=''
        try:self.log=SessionLog(self.data_dir/'logs')
        except OSError:self.log=None
        self.timer=QTimer(self);self.timer.setInterval(10)
        self.timer.timeout.connect(self._tick);self.timer.start()

    def _event(self,kind,**values):
        if self.log:
            try:self.log.event(kind,**values)
            except (OSError,ValueError):pass

    def _show_error(self,message):
        self.error=str(message);self.problem.emit(self.error)
        self._event('error',message=self.error)

    def start_camera(self):
        if self._closed:return
        if self.vision is not None:self.stop_camera()
        self.error='';self.camera_state='starting';self._frame_seq=-1
        self._history.clear();self._camera_started=time.monotonic();self._last_received=0.
        self.selector=HandSelector(self.profile.dominant_hand)
        self.stability=FingerStability(self.profile.finger_thresholds or None,
                                       self.profile.finger_marginals or None)
        try:
            self.vision=self._vision_factory(camera=self.profile.camera,backend=self.profile.camera_backend)
            self.vision.start()
        except Exception as exc:
            self.camera_state='error';self._show_error(exc)
        self._emit()

    def stop_camera(self):
        self.set_enabled(False)
        if self.vision is not None:
            self.vision.close();self.vision=None
        self.camera_state='off';self.hand_state='No hand detected'
        self._fps=0.;self._quality=0.;self.preview.emit(None);self._emit()

    def set_enabled(self,enabled):
        if self._closed:return
        self.engine.reset();self.dragging=False
        if self.broker is not None:self.broker.pause()
        self._broker_active=False
        if not enabled:
            self.enabled=False;self._acknowledge_emergency=False;self._emit();return
        if self.calibration is not None:
            self._show_error('请完成或保存校准后再启用控制。');return
        if self.vision is None or self.camera_state=='error':self.start_camera()
        if self.camera_state=='error':return
        if not self.practice and self.broker is None:
            try:
                self.broker=self._broker_factory(dry_run=False);self.broker.start()
            except Exception as exc:
                self._show_error(exc);return
        self._acknowledge_emergency=bool(self.broker and self.broker.poll().get('emergency'))
        self.enabled=True;self.error=''
        self._event('enabled',practice=self.practice);self._emit()

    def set_practice(self,practice):
        self.set_enabled(False)
        if self.calibration is not None:self.cancel_calibration()
        self.practice=bool(practice);self._emit()

    def set_debug(self,enabled):
        self.debug=bool(enabled);self._emit()

    def update_profile(self,changes):
        self.set_enabled(False)
        if self.calibration is not None:self.cancel_calibration()
        try:
            new=self.profile.updated(changes)
            new.save(self.data_dir/'profile.json')
        except (ValueError,TypeError,OSError) as exc:
            self._show_error(exc);return
        camera_changed=(new.camera,new.camera_backend)!=(self.profile.camera,self.profile.camera_backend)
        self.profile=new;self.engine=GestureEngine(new);self.selector=HandSelector(new.dominant_hand)
        self.stability=FingerStability(new.finger_thresholds or None,
                                       new.finger_marginals or None)
        self._unusable_since=None;self._guidance_given=False
        if camera_changed and self.vision is not None:self.start_camera()
        self._event('profile',revision=new.revision);self._emit()

    def start_calibration(self):
        self.set_enabled(False);self.practice=True;self.error=''
        self.calibration=Calibrator(self.profile)
        if self.vision is None:self.start_camera()
        self._calibration_step=0;self._calibration_text=self.calibration.text
        self._emit()

    def calibration_next(self):
        if self.calibration is None:return
        try:
            self.error=""
            if self.calibration.step<len(PHASES):
                self.calibration.next()
                self._calibration_step=self.calibration.step
                self._calibration_text=self.calibration.text
            else:
                new=self.calibration.finish()
                new.save(self.data_dir/'profile.json')
                self.profile=new;self.engine=GestureEngine(new)
                self.selector=HandSelector(new.dominant_hand)
                self.stability=FingerStability(new.finger_thresholds or None,new.finger_marginals or None)
                self._unusable_since=None;self._guidance_given=False
                self.calibration=None;self._calibration_step=6
                self._calibration_text='校准已保存。启用 Practice，在右侧练习点击与拖拽。'
                self._event('calibration_saved',revision=new.revision)
        except (ValueError,OSError) as exc:self._show_error(exc)
        self._emit()

    def calibration_start(self):
        if self.calibration is not None:
            self.error='';self.calibration.start()
            self._calibration_text=self.calibration.text
            self._emit()

    def calibration_retry(self):
        if self.calibration is not None:
            self.error='';self.calibration.retry()
            self._calibration_text=self.calibration.text
            self._emit()

    def cancel_calibration(self):
        self.set_enabled(False)
        self.calibration=None;self._calibration_step=0;self._calibration_text=''
        self.practice=True;self.error=''
        self.stability.reset()
        self._emit()

    def _release(self):
        self.engine.cancel()
        self.dragging=False
        if self.broker is not None and self._broker_active:self.broker.pause()
        self._broker_active=False

    def _tick(self):
        if self._closed:return
        try:
            now=time.monotonic()
            if self.broker is not None:
                status=self.broker.poll()
                if status.get('emergency') and self.enabled and not self._acknowledge_emergency:
                    self.set_enabled(False);self._show_error('Emergency pause: Ctrl+Alt+Space; click Enable to resume.')
                elif self._broker_active and not status.get('enabled'):
                    self.set_enabled(False)
                    self._show_error(status.get('error') or 'Desktop control paused by safety watchdog.')
            if self.vision is not None:
                packet=self.vision.poll()
                if packet is not None:self.process_payload(packet)
                elif self.camera_state=='running' and now-self._last_received>.1:
                    self.engine.step(None,now);self._release();self.hand_state='Tracking delayed — resumes when fresh hand frames return'
                elif self.camera_state=='starting' and now-self._camera_started>15:
                    self.process_payload({'error':'Camera startup timed out. Try another camera index/backend in Settings.'})
            if now-self._last_status>.08:
                self._emit();self._last_status=now
        except Exception as exc:
            self.set_enabled(False);self._show_error(f'Control stopped safely: {exc}')

    def process_payload(self,packet):
        now=time.monotonic()
        if packet.get('error'):
            self.set_enabled(False);self.engine.cancel();self.camera_state='error'
            self._show_error(packet['error']);self._emit();return
        seq=packet.get('frame_seq',-1)
        if seq<=self._frame_seq:return
        self._frame_seq=seq;self._last_received=now;self.camera_state='running'
        source=packet['t'];self._history.append(source)
        self._capture_fps=packet.get('capture_fps',packet.get('fps',0.))
        self._fps=(len(self._history)-1)/(source-self._history[0]) if len(self._history)>1 and source>self._history[0] else 0.
        self._inference=packet.get('inference_ms',0.);self._latency=(now-source)*1000
        frame=packet.get('frame')
        width=packet.get('source_width',frame.shape[1] if frame is not None else 640)
        height=packet.get('source_height',frame.shape[0] if frame is not None else 480)
        chosen=self.selector.select(packet.get('hands',[]),source)
        sample=None;quality={'score':0.,'reason':self.selector.reason}
        if chosen is not None:
            sample,quality=extract_sample(chosen,source,width,height,self.selector.track_id,self.stability)
        elif not packet.get('hands'):
            self.stability.reset()
        if self.calibration is None:self._watch_unusable_pose(chosen,sample,now)
        self.hand_state=quality['reason'];self._quality=quality['score']
        self._scale=sample.scale if sample is not None else 0.
        if self.calibration is not None:
            if -.005<=now-source<=.1:
                self.calibration.add(measurement_row(chosen,width,height,quality))
            self._calibration_text=self.calibration.text
        elif self.enabled:
            # A camera FPS report cannot replace measured fresh control samples. The floor
            # is low on purpose: a brief hiccup must not drop control, only a sustained
            # stall should.
            low_rate=len(self._history)>30 and source-self._history[0]>1.5 and self._fps<12
            if low_rate:sample=None;self.hand_state='Low FPS — improve lighting or change camera'
            actions=self.engine.step(sample,now)
            if self.practice:
                self._apply_practice(actions)
            elif sample is None or self.engine.state=='LOST_TRACKING':
                self._release()
            else:
                # A frame may be processed outside _tick, or emergency may arrive while
                # vision is being decoded. Only an explicit Enable may acknowledge it.
                status=self.broker.poll() if self.broker is not None else {}
                if status.get('emergency') and not self._acknowledge_emergency:
                    self.set_enabled(False)
                    self._show_error('Emergency pause: Ctrl+Alt+Space; click Enable to resume.')
                    return
                if not self._broker_active and self.engine.state in BROKER_ARM_STATES:
                    if self.broker is not None and self.broker.enable():
                        self._acknowledge_emergency=False
                        self._broker_active=True
                        self._enable_attempts=0
                        # Start with a fresh following observation after the enable handshake.
                        self.engine.motion.reset()
                        actions=[]
                    else:
                        # A starved machine can miss the enable handshake; that is not the
                        # same as a broken broker, so try again on the next frames instead
                        # of ending the session on the first refusal.
                        self._enable_attempts+=1
                        status=self.broker.poll() if self.broker else {}
                        if self._enable_attempts>=3:
                            self.set_enabled(False)
                            self._show_error(status.get('error') or 'Input broker is unavailable')
                            return
                        self.hand_state='Starting desktop input…'
                        return
                if self._broker_active and not self.broker.submit(actions,source):
                    # The broker's own 80 ms observation limit and 150 ms lease are the
                    # safety boundary, so a slow frame only costs this one batch. Turning
                    # the whole session off here meant a momentary stall silently disabled
                    # control until the user noticed and clicked Enable again.
                    self._release()
                    self.hand_state='Frames too slow - input paused, resumes automatically'
            if self.engine.state!=self._last_state:
                self._event('state',state=self.engine.state);self._last_state=self.engine.state
            for action in actions:
                if action.kind not in ('move','wheel','release_all'):
                    self._event('input',kind_name=action.kind,practice=self.practice)
        if frame is not None and now-self._last_preview>=1/15:
            self.preview.emit(self._overlay(frame,packet.get('hands',[])))
            self._last_preview=now
        if now-self._last_log>1:
            self._event('performance',fps=round(self._fps,1),capture_fps=round(self._capture_fps,1),read_to_controller_ms=round(self._latency,1),inference_ms=round(self._inference,1))
            self._last_log=now

    def _apply_practice(self,actions):
        for action in actions:
            if action.kind=='move':
                self.practice_x=max(.02,min(.98,self.practice_x+action.x/1100))
                self.practice_y=max(.02,min(.98,self.practice_y+action.y/800))
            elif action.kind=='click':self.click_count+=1
            elif action.kind=='right_click':self.right_click_count+=1
            elif action.kind=='wheel':
                self.practice_scroll_x+=action.x;self.practice_scroll_y+=action.y
                if action.x or action.y:self.scroll_count+=1
            elif action.kind=='left_down':self.dragging=True
            elif action.kind in ('left_up','release_all'):self.dragging=False

    @staticmethod
    def _overlay(frame,hands):
        import cv2
        canvas=frame.copy();h,w=canvas.shape[:2]
        edges=((0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),(5,9),(9,10),(10,11),(11,12),(9,13),(13,14),(14,15),(15,16),(13,17),(0,17),(17,18),(18,19),(19,20))
        for hand in hands:
            points=hand.get('landmarks',[])
            if len(points)!=21:continue
            pts=[(int(x*w),int(y*h)) for x,y,z in points]
            for a,b in edges:cv2.line(canvas,pts[a],pts[b],(157,220,81),2,cv2.LINE_AA)
            for i,pt in enumerate(pts):
                cv2.circle(canvas,pt,4 if i in (4,8,12) else 2,(218,251,225),-1,cv2.LINE_AA)
        return canvas

    def _watch_unusable_pose(self,chosen,sample,now):
        """Tell the user what to do when their hand is visible but never usable.

        A hand the selector accepted but the geometry gate refused means the pose is not
        being recognised - the exact symptom of thresholds that do not fit this hand.
        Staying silent leaves the user staring at an app that looks broken.
        """
        if chosen is None or sample is not None:
            self._unusable_since=None
            return
        if self._unusable_since is None:
            self._unusable_since=now
            return
        if self._guidance_given or now-self._unusable_since<GUIDANCE_AFTER_SECONDS:
            return
        self._guidance_given=True
        if not self.profile.finger_thresholds:
            self._show_error('A hand is visible but its pose is not recognised. Use '
                             'Guide me through calibration to measure your hand (five phases).' )
        else:
            self._show_error('A hand is visible but its pose is not recognised even with the '
                             'measured thresholds. Re-run Guide me through calibration and keep your '
                             'hand in view for all five phases.')

    def _emit(self):
        self.updated.emit({'camera_state':self.camera_state,'hand_state':self.hand_state,
            'state':self.engine.state if self.enabled else 'PAUSED','enabled':self.enabled,'practice':self.practice,
            'fps':self._fps,'inference_ms':self._inference,'latency_ms':self._latency,
            'velocity':self.engine.velocity,'pinch':self.engine.pinch_ratio,'scale':self._scale,'quality':self._quality,
            'pointer_dx':self.engine.last_delta[0],'pointer_dy':self.engine.last_delta[1],
            'practice_x':self.practice_x,'practice_y':self.practice_y,'click_count':self.click_count,'dragging':self.dragging,
            'right_click_count':self.right_click_count,'scroll_count':self.scroll_count,
            'practice_scroll_x':self.practice_scroll_x,'practice_scroll_y':self.practice_scroll_y,
            'two_finger_scroll':self.profile.two_finger_scroll,'drag_hold':self.profile.drag_hold,
            'calibration_active':self.calibration is not None,
            'calibration_collecting':bool(self.calibration and self.calibration.collecting),
            'calibration_count':len(self.calibration.samples) if self.calibration else 0,
            'calibration_target':self.calibration.target if self.calibration else 0,
            'calibration_ready':bool(self.calibration and self.calibration.ready),
            'error':self.error or None,'calibration_step':self._calibration_step,'calibration_text':self._calibration_text})

    def shutdown(self):
        if self._closed:return
        self.set_enabled(False);self._closed=True;self.timer.stop()
        if self.broker is not None:self.broker.close()
        if self.vision is not None:self.vision.close();self.vision=None
        if self.log is not None:self.log.close();self.log=None
