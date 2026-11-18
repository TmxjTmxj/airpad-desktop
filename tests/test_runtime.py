import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication


class FakeVision:
    def __init__(self,**kwargs):self.started=False;self.closed=False
    def start(self):self.started=True
    def close(self):self.closed=True
    def poll(self):return None


class FakeBroker:
    def __init__(self,**kwargs):self.enabled=False;self.actions=[];self.closed=False
    def start(self):pass
    def enable(self):self.enabled=True;return True
    def pause(self):self.enabled=False
    def submit(self,actions,observed_at):self.actions+=actions;return True
    def poll(self):return {'ready':True,'enabled':self.enabled,'error':None,'emergency':False}
    def close(self):self.closed=True;self.enabled=False


@pytest.fixture
def controller(tmp_path):
    from airpad.runtime import AirpadController
    app=QApplication.instance() or QApplication([])
    c=AirpadController(tmp_path,vision_factory=FakeVision,broker_factory=FakeBroker)
    yield c
    c.shutdown()


def test_control_mode_is_the_default(controller):
    """Controlling the desktop is the point, so it must not need an arming ceremony."""
    c=controller
    assert not c.enabled and not c.practice
    c.start_camera();c.set_enabled(True)
    assert c.enabled
    assert c.broker is not None,'control mode creates the input broker'
    assert not c.broker.enabled,'the broker only enables once a hand is tracked'


def test_practice_mode_never_creates_a_desktop_broker(controller):
    c=controller
    c.set_practice(True)
    c.start_camera();c.set_enabled(True)
    assert c.enabled and c.practice
    assert c.broker is None


def test_camera_fault_cancels_control(controller):
    c=controller;c.start_camera();c.set_enabled(True)
    c.process_payload({'error':'camera unplugged'})
    assert not c.enabled and c.camera_state=='error'
    assert c.engine.state in ('READY','LOST_TRACKING')


def test_settings_apply_atomically_and_pause_control(controller):
    c=controller;c.start_camera();c.set_enabled(True)
    c.update_profile({'sensitivity':850})
    assert not c.enabled and c.profile.sensitivity==850
    assert (c.data_dir/'profile.json').is_file()
    c.update_profile({'pinch_close':1.4})
    assert c.profile.pinch_close==.42


def test_shutdown_closes_camera_and_broker(controller):
    c=controller;c.start_camera()
    camera=c.vision
    c.set_practice(False);c.set_enabled(True)
    broker=c.broker
    c.shutdown()
    assert camera.closed
    assert broker is None or broker.closed


class FailingBroker(FakeBroker):
    """Accepts the enable handshake, then refuses one submit like a stale frame would."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fail_submits = 0

    def submit(self, actions, observed_at):
        if self.fail_submits > 0:
            self.fail_submits -= 1
            return False
        return super().submit(actions, observed_at)


def _packet(sequence, pose='pointer', palm=(0.66, 0.55)):
    import sys
    import time
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import hand_model
    return {
        'frame_seq': sequence, 't': time.monotonic(), 'fps': 30.0, 'capture_fps': 30.0,
        'inference_ms': 5.0, 'frame': None, 'error': None,
        'source_width': 1280, 'source_height': 720,
        'hands': [hand_model.as_hand(pose=pose, palm=palm, width=1280, height=720)],
    }


class FlakyEnableBroker(FakeBroker):
    """Refuses the enable handshake a few times, like a starved machine would."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fail_enables = 0

    def enable(self):
        if self.fail_enables > 0:
            self.fail_enables -= 1
            return False
        return super().enable()


def _stream(controller, frames, palm_step=0.0, start=(0.66, 0.55)):
    """Feed frames at a camera-like cadence, continuing the controller's sequence.

    A repeated frame_seq is silently dropped by process_payload, so each frame must take
    the next number rather than restarting at 1.
    """
    import time
    for index in range(frames):
        time.sleep(.033)
        controller.process_payload(
            _packet(controller._frame_seq + 1, palm=(start[0] + palm_step * index, start[1])))


def _controller_with(broker_factory):
    import tempfile

    from PySide6.QtWidgets import QApplication

    from airpad.runtime import AirpadController

    QApplication.instance() or QApplication([])
    return AirpadController(tempfile.mkdtemp(), vision_factory=FakeVision,
                            broker_factory=broker_factory)


def test_a_transient_enable_failure_is_retried_not_fatal():
    """A starved machine missing the enable handshake must not end the session."""
    brokers = []

    def factory(**kwargs):
        broker = FlakyEnableBroker(**kwargs)
        brokers.append(broker)
        return broker

    controller = _controller_with(factory)
    try:
        controller.start_camera()
        controller.set_enabled(True)
        brokers[0].fail_enables = 2
        for _ in range(10):
            _stream(controller, 1)
            if controller._broker_active:
                break
        assert controller._broker_active, f'never recovered: {controller.hand_state}'
        assert controller.enabled, 'the session must survive a transient refusal'
        assert controller.error in ('', None)
    finally:
        controller.shutdown()


def test_repeated_enable_failures_do_end_the_session_with_an_error():
    """The retry budget exists so a genuinely broken broker still stops control."""
    brokers = []

    def factory(**kwargs):
        broker = FlakyEnableBroker(**kwargs)
        brokers.append(broker)
        return broker

    controller = _controller_with(factory)
    try:
        controller.start_camera()
        controller.set_enabled(True)
        brokers[0].fail_enables = 50
        for _ in range(10):
            _stream(controller, 1)
            if not controller.enabled:
                break
        assert not controller.enabled, 'a broker that never enables must stop the session'
        assert controller.error
    finally:
        controller.shutdown()


def test_the_app_explains_what_to_do_when_the_hand_is_not_recognised(monkeypatch):
    """A visible hand that the geometry gate keeps refusing is the exact symptom of
    thresholds that do not fit this hand. Staying silent looks like a broken app."""
    import sys
    import time
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import hand_model

    from airpad import runtime as runtime_module

    monkeypatch.setattr(runtime_module, 'GUIDANCE_AFTER_SECONDS', 0.05)
    controller = _controller_with(lambda **kwargs: FakeBroker(**kwargs))
    try:
        controller.start_camera()
        controller.set_enabled(True)

        def unusable_packet(sequence):
            # A relaxed index: below the built-in threshold, so the gate refuses it.
            hand = hand_model.as_hand(pose='pointer', curls=(0.28, 1.0, 1.0, 1.0),
                                      width=1280, height=720)
            return {'frame_seq': sequence, 't': time.monotonic(), 'fps': 30.0,
                    'capture_fps': 30.0, 'inference_ms': 5.0, 'frame': None, 'error': None,
                    'source_width': 1280, 'source_height': 720, 'hands': [hand]}

        for _ in range(4):
            time.sleep(.033)
            controller.process_payload(unusable_packet(controller._frame_seq + 1))
        assert 'calibration' in (controller.error or ''), controller.error
        assert controller._guidance_given
    finally:
        controller.shutdown()


def test_a_recognised_hand_never_triggers_the_guidance_message(monkeypatch):
    import time

    from airpad import runtime as runtime_module

    monkeypatch.setattr(runtime_module, 'GUIDANCE_AFTER_SECONDS', 0.05)
    controller = _controller_with(lambda **kwargs: FakeBroker(**kwargs))
    try:
        controller.start_camera()
        controller.set_enabled(True)
        for _ in range(8):
            time.sleep(.033)
            controller.process_payload(_packet(controller._frame_seq + 1))
        assert not controller._guidance_given
        assert not controller.error
    finally:
        controller.shutdown()


def test_first_use_guidance_switches_to_re_measure_once_thresholds_are_set(monkeypatch):
    """With measured thresholds already applied, the advice must change accordingly."""
    import sys
    import time
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
    import hand_model

    from airpad import runtime as runtime_module

    monkeypatch.setattr(runtime_module, 'GUIDANCE_AFTER_SECONDS', 0.05)
    controller = _controller_with(lambda **kwargs: FakeBroker(**kwargs))
    try:
        controller.update_profile({'finger_thresholds': (0.62, 0.62, 0.62, 0.62, 0.62)})
        controller.start_camera()
        controller.set_enabled(True)
        for _ in range(4):
            time.sleep(.033)
            hand = hand_model.as_hand(pose='pointer', curls=(0.28, 1.0, 1.0, 1.0),
                                      width=1280, height=720)
            controller.process_payload(
                {'frame_seq': controller._frame_seq + 1, 't': time.monotonic(), 'fps': 30.0,
                 'capture_fps': 30.0, 'inference_ms': 5.0, 'frame': None, 'error': None,
                 'source_width': 1280, 'source_height': 720, 'hands': [hand]})
        assert 'Re-run' in (controller.error or ''), controller.error
    finally:
        controller.shutdown()


def test_a_slow_frame_pauses_input_but_resumes_by_itself():
    """A momentary stall must not silently end the session: the broker's own observation
    limit already guarantees nothing stale is injected."""
    import tempfile
    import time

    from PySide6.QtWidgets import QApplication

    from airpad.runtime import AirpadController

    QApplication.instance() or QApplication([])
    brokers = []

    def factory(**kwargs):
        broker = FailingBroker(**kwargs)
        brokers.append(broker)
        return broker

    controller = AirpadController(tempfile.mkdtemp(), vision_factory=FakeVision,
                                  broker_factory=factory)
    try:
        controller.start_camera()
        controller.set_enabled(True)

        sequence = [0]

        def send(palm=(0.66, 0.55)):
            """Every frame gets a fresh sequence number and a distinct timestamp.

            Frames are spaced like a real 30 fps camera: Windows advances
            time.monotonic() in ~15.6 ms steps, and two frames inside one tick share a
            timestamp, which the state machine correctly treats as a repeated observation
            and releases on. Sending faster than the clock resolves would test an
            artificial failure, not the product.
            """
            sequence[0] += 1
            time.sleep(.033)
            controller.process_payload(_packet(sequence[0], palm=palm))

        # Feed a short stream, as the camera would: the per-finger hysteresis needs a few
        # frames before any finger counts as extended.
        for _ in range(10):
            send()
            if controller._broker_active:
                break
        assert controller._broker_active, f'never took control: {controller.hand_state}'
        assert controller.enabled

        brokers[0].fail_submits = 1
        send(palm=(0.68, 0.55))
        assert not controller._broker_active, 'the refused batch must release input'
        assert controller.enabled, 'a slow frame must not switch the session off'
        assert 'slow' in controller.hand_state.lower()

        for _ in range(4):
            send(palm=(0.70, 0.55))
            if controller._broker_active:
                break
        assert controller._broker_active, 'control must come back on its own'
        assert controller.enabled
    finally:
        controller.shutdown()


def test_calibration_rejects_missing_samples():
    from airpad.calibration import Calibrator
    from airpad.config import Profile
    cal=Calibrator(Profile())
    with pytest.raises(ValueError):cal.next()


def test_calibration_learns_separated_pinch_distributions():
    from airpad.calibration import Calibrator
    from airpad.config import Profile
    cal=Calibrator(Profile())
    for phase in ('open', 'fist', 'point', 'sweep', 'pinch'):
        cal.start()
        for i in range(150):
            scores={name: .9 if phase=='open' or (phase=='point' and name=='index') else .1
                    for name in ('thumb','index','middle','ring','pinky')}
            cal.add({'scores':scores, 'scale':.2, 'index_x':i*.02,
                     'pinch':.15 if i%20<10 else .9})
        cal.next()
    result=cal.finish()
    assert result.calibrated
    assert len(result.finger_thresholds)==5
    assert all(value==pytest.approx(.46) for value in result.finger_thresholds)
    assert all(value==pytest.approx(.22) for value in result.finger_marginals)
    assert result.sensitivity!=1000
    assert .15<result.pinch_close<result.pinch_release<result.pinch_open<=.9


def test_calibration_collects_fist_geometry_without_a_control_pose(controller):
    c=controller
    c.start_calibration()
    c.calibration.start()
    for i in range(60):
        c.process_payload(_packet(i, pose='open'))
    c.calibration_next()
    c.calibration.start()
    for i in range(60,120):
        packet=_packet(i, pose='unknown')
        import hand_model
        packet['hands']=[hand_model.as_hand(pose='unknown',thumb_tucked=True,width=1280,height=720)]
        c.process_payload(packet)
    assert len(c.calibration.samples)>=45
    assert not c.enabled and c.broker is None


def test_cancel_calibration_exits_without_writing_or_resuming_input(controller):
    c=controller
    c.set_enabled(True)
    c.start_calibration()
    c.cancel_calibration()
    c.process_payload(_packet(1))
    assert c.calibration is None
    assert not c.enabled and c.practice
    assert not c.broker.enabled and not c.broker.actions
    assert not (c.data_dir/'profile.json').exists()


def test_calibration_waits_for_start_and_retry_clears_only_current_phase():
    from airpad.calibration import Calibrator
    from airpad.config import Profile
    cal=Calibrator(Profile())
    row={'scores':dict.fromkeys(('thumb','index','middle','ring','pinky'),.9),
         'scale':.2,'index_x':1.,'pinch':.8}
    cal.add(row)
    assert not cal.samples
    cal.start()
    cal.add(row)
    assert len(cal.samples)==1
    cal.retry()
    assert not cal.samples and not cal.collecting
    with pytest.raises(ValueError):cal.next()


def test_saved_calibration_reloads_finger_stability_and_stays_in_practice(controller):
    from airpad.config import Profile
    c=controller
    c.start_calibration()
    c.calibration.result=Profile(finger_thresholds=(.4,)*5,finger_marginals=(.2,)*5,calibrated=True)
    c.calibration.step=5
    c.calibration_next()
    assert c.calibration is None and c.profile.calibrated
    assert c.stability.bands['index']['strong']==.4
    assert c.practice and not c.enabled and c.broker is None
    assert Profile.load(c.data_dir/'profile.json').finger_thresholds==(.4,)*5
    c.set_enabled(True)
    assert c.enabled and c.broker is None


def test_release_cancels_a_drag_before_a_new_input_epoch(controller):
    c=controller
    c.set_enabled(True)
    c._broker_active=True
    c.broker.enabled=True
    c.engine.state='DRAGGING'
    c.engine.pinch_confirmed=True
    c._release()
    assert c.engine.state=='LOST_TRACKING'
    assert not c.engine.pinch_confirmed
    assert not c.broker.enabled


def test_tick_consumes_emergency_before_new_frames(controller):
    c=controller
    c.set_enabled(True)
    _stream(c,3)
    c._release()
    emergency={'value':True}
    c.broker.poll=lambda: {'enabled':c.broker.enabled,'emergency':emergency['value'],'error':None}
    def enable():
        emergency['value']=False
        c.broker.enabled=True
        return True
    c.broker.enable=enable
    c.vision.poll=lambda: _packet(c._frame_seq+1)
    import time
    time.sleep(.033)
    c._tick()
    assert not c.enabled and not c.broker.enabled
    assert emergency['value'] and 'Emergency' in c.error


@pytest.mark.parametrize('state', ['TWO_CANDIDATE', 'TWO_PENDING', 'SCROLLING', 'ZOOMING', 'THREE_PENDING'])
def test_direct_gesture_arms_broker_and_discards_handshake_frame(controller, monkeypatch, state):
    from types import SimpleNamespace
    from airpad.types import Action
    c = controller
    c.set_enabled(True)
    monkeypatch.setattr('airpad.runtime.extract_sample', lambda *a: (SimpleNamespace(scale=.2), {'score': 1., 'reason': 'ok'}))
    def step(*args):
        c.engine.state = state
        return [Action('wheel', 12, -24)]
    monkeypatch.setattr(c.engine, 'step', step)
    c.process_payload(_packet(1))
    assert c._broker_active
    assert c.broker.actions == []
    c.process_payload(_packet(2))
    assert c.broker.actions == [Action('wheel', 12, -24)]


def test_payload_cannot_auto_acknowledge_emergency(controller, monkeypatch):
    from types import SimpleNamespace
    c = controller
    c.set_enabled(True)
    c.broker.poll = lambda: {'enabled': False, 'emergency': True}
    monkeypatch.setattr('airpad.runtime.extract_sample', lambda *a: (SimpleNamespace(scale=.2), {'score': 1., 'reason': 'ok'}))
    def step(*args):
        c.engine.state = 'SCROLLING'
        return []
    monkeypatch.setattr(c.engine, 'step', step)
    c.process_payload(_packet(1))
    assert not c.enabled and not c._broker_active and not c.broker.enabled
    assert 'Emergency' in c.error


def test_practice_tracks_right_click_and_two_axis_scroll_and_mode_exit_pauses(controller, monkeypatch):
    from airpad.types import Action
    c = controller
    c.set_practice(True)
    c.set_enabled(True)
    c._apply_practice([Action('click'), Action('right_click'), Action('wheel', 24, -48)])
    assert c.click_count == 1 and c.right_click_count == 1
    assert (c.practice_scroll_x, c.practice_scroll_y, c.scroll_count) == (24, -48, 1)
    updates = []
    c.updated.connect(updates.append)
    c._emit()
    assert updates[-1]['right_click_count'] == 1
    assert updates[-1]['practice_scroll_x'] == 24
    assert updates[-1]['practice_scroll_y'] == -48
    c.set_practice(False)
    assert not c.enabled and c.broker is None
    c.process_payload(_packet(1))
    assert c.right_click_count == 1 and c.scroll_count == 1 and c.broker is None


def test_touchpad_preset_preserves_recognition_and_other_settings(controller):
    from dataclasses import asdict
    c = controller
    c.update_profile({'finger_thresholds': (.5,) * 5, 'finger_marginals': (.3,) * 5,
                      'sensitivity': 1337, 'natural_scroll': False, 'zoom_enabled': True,
                      'three_finger_enabled': True})
    before = asdict(c.profile)
    c.set_enabled(True)
    c.update_profile({'two_finger_scroll': True, 'zoom_enabled': False, 'three_finger_enabled': False})
    after = asdict(c.profile)
    for key in set(before) - {'two_finger_scroll', 'zoom_enabled', 'three_finger_enabled', 'revision'}:
        assert after[key] == before[key], key
    assert c.profile.two_finger_scroll and not c.enabled
    assert not c.profile.zoom_enabled and not c.profile.three_finger_enabled
