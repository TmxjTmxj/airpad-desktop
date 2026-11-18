"""The whole control path, with production classes and no fakes at the seams.

Every earlier test substituted either the broker or the geometry. This one wires the real
gesture state machine, the real controller, the real `BrokerState` safety policy and the
real `OwnedBackend` release/ledger logic together, and drives them with a scripted hand.
Only the Win32 boundary (a recording backend) and the camera are replaced.

It never touches the desktop: the recording backend cannot inject anything.
"""
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import hand_model  # noqa: E402

from airpad.backend import OWNED_KEYS, RecordingBackend  # noqa: E402
from airpad.broker import BrokerState  # noqa: E402

FRAME_SECONDS = 0.033     # 30 fps, comfortably above the ~15.6 ms Windows clock step


class ScriptedVision:
    """No camera/process: these tests inject their landmark packets directly."""

    def __init__(self, **kwargs):
        pass

    def start(self):
        pass

    def poll(self):
        return None

    def close(self):
        pass


class InProcessBroker:
    """The BrokerClient API over the real BrokerState and RecordingBackend.

    This is the same policy object the worker process drives; only the process boundary
    and SendInput are absent, so the controller-to-broker contract is exercised for real.
    """

    def __init__(self, **kwargs):
        self.backend = RecordingBackend()
        self.state = BrokerState(self.backend, clock=time.monotonic)
        self.epoch = 0
        self.sequence = 0
        self.closed = False

    def start(self):
        pass

    def enable(self):
        self.epoch += 1
        self.sequence = 0
        self.state.enable(self.epoch, time.monotonic())
        return self.state.enabled

    def pause(self):
        self.state.pause()

    def submit(self, actions, observed_at):
        self.sequence += 1
        return self.state.submit(self.epoch, self.sequence, observed_at, list(actions),
                                 time.monotonic())

    def poll(self):
        return {'ready': True, 'enabled': self.state.enabled, 'emergency': self.state.emergency,
                'error': self.state.error or None,
                'owned': [key for index, key in enumerate(OWNED_KEYS) if self.backend.ledger[index]]}

    def close(self):
        self.closed = True
        self.state.pause()

    # --- inspection helpers -------------------------------------------------
    def kinds(self):
        return [event[0] for event in self.backend.events]

    def presses(self):
        """Return the (down, up) key pairs in order, ignoring motion."""
        stack, pairs = [], []
        for event in self.backend.events:
            if event[0] == 'down':
                stack.append(event[1])
            elif event[0] == 'up' and stack:
                pairs.append((stack.pop(), event[1]))
        return pairs


def packet(sequence, pose='pointer', pinch=0.9, palm=(0.66, 0.55), two_spread=None, stamp=None):
    return {
        'frame_seq': sequence, 't': stamp if stamp is not None else time.monotonic(),
        'fps': 30.0, 'capture_fps': 30.0, 'inference_ms': 5.0, 'frame': None, 'error': None,
        'source_width': 1280, 'source_height': 720,
        'hands': [hand_model.as_hand(pose=pose, pinch=pinch, palm=palm, two_spread=two_spread,
                                     width=1280, height=720)],
    }


@pytest.fixture
def running_controller():
    from PySide6.QtWidgets import QApplication

    from airpad.runtime import AirpadController

    QApplication.instance() or QApplication([])
    brokers = []

    def factory(**kwargs):
        broker = InProcessBroker(**kwargs)
        brokers.append(broker)
        return broker

    import tempfile
    controller = AirpadController(tempfile.mkdtemp(), vision_factory=ScriptedVision,
                                  broker_factory=factory)
    controller.start_camera()
    controller.set_practice(False)
    controller.set_enabled(True)
    try:
        yield controller, brokers[0]
    finally:
        controller.shutdown()


@pytest.fixture
def scroll_controller():
    """Same rig with the opt-in two-finger gestures enabled."""
    from PySide6.QtWidgets import QApplication

    from airpad.runtime import AirpadController

    QApplication.instance() or QApplication([])
    brokers = []

    def factory(**kwargs):
        broker = InProcessBroker(**kwargs)
        brokers.append(broker)
        return broker

    import tempfile
    controller = AirpadController(tempfile.mkdtemp(), vision_factory=ScriptedVision,
                                  broker_factory=factory)
    controller.update_profile({'two_finger_scroll': True, 'zoom_enabled': True})
    controller.start_camera()
    controller.set_practice(False)
    controller.set_enabled(True)
    try:
        yield controller, brokers[0]
    finally:
        controller.shutdown()


def feed(controller, script):
    """Play a (pose, pinch, palm[, two_spread]) script at a camera-like cadence.

    The sequence continues from the controller's last accepted frame: a repeated
    frame_seq is dropped by process_payload, which would silently skip the frames.
    """
    for row in script:
        pose, pinch, palm = row[0], row[1], row[2]
        spread = row[3] if len(row) > 3 else None
        time.sleep(FRAME_SECONDS)
        controller.process_payload(packet(controller._frame_seq + 1, pose=pose, pinch=pinch,
                                          palm=palm, two_spread=spread))


CLICK_AND_DRAG = (
    [('open', 0.95, (0.66, 0.55))] * 3          # clutch / warm-up
    # The per-finger hysteresis needs a few frames to release the three curled fingers
    # after an open palm, so the pointer segment must be long enough for that decay.
    + [('pointer', 0.95, (0.66, 0.55))] * 8     # take control
    + [('pointer', 0.35, (0.66, 0.55)),         # pinch to click
       ('pointer', 0.25, (0.66, 0.55)),
       ('pointer', 0.18, (0.66, 0.55)),
       ('pointer', 0.70, (0.66, 0.55))]         # release
    + [('pointer', 0.95, (0.66, 0.55))] * 3
    + [('pointer', 0.35, (0.66, 0.55)),         # pinch again
       ('pointer', 0.25, (0.66, 0.55)),
       ('pointer', 0.18, (0.66, 0.55))]
    + [('pointer', 0.18, (0.66 + 0.03 * step, 0.55)) for step in range(1, 7)]   # drag
    + [('pointer', 0.70, (0.84, 0.55))]         # release the drag
    + [('open', 0.95, (0.84, 0.55))] * 2        # clutch
)


def test_a_scripted_hand_reaches_the_backend_as_real_input_events(running_controller):
    controller, broker = running_controller
    feed(controller, CLICK_AND_DRAG)

    kinds = broker.kinds()
    assert broker.presses() == [('left', 'left'), ('left', 'left')], \
        f'expected one click and one drag, got {broker.presses()}'
    assert kinds.count('down') == 2 and kinds.count('up') == 2
    assert kinds.count('move') > 0, 'the drag must produce relative motion'

    # The drag's motion must land between its own down and up, not before the click.
    events = broker.backend.events
    first_up = events.index(('up', 'left'))
    second_down = events.index(('down', 'left'), first_up)
    second_up = events.index(('up', 'left'), second_down)
    assert any(event[0] == 'move' for event in events[second_down:second_up]), \
        'no movement while the drag was held'


def test_the_ledger_is_empty_after_the_session(running_controller):
    """The one invariant that matters most: nothing is left pressed."""
    controller, broker = running_controller
    feed(controller, CLICK_AND_DRAG)
    assert broker.poll()['owned'] == []
    assert all(bit == 0 for bit in broker.backend.ledger)


def test_losing_the_hand_releases_everything_immediately(running_controller):
    """A drag held when the hand disappears must not keep the button down."""
    controller, broker = running_controller
    script = (CLICK_AND_DRAG[:-1]                       # stop mid-drag release
              + [('pointer', 0.18, (0.84, 0.55))] * 2)  # still pinched, hand still there
    feed(controller, script)
    assert ('down', 'left') in broker.backend.events, 'the drag should have started'

    # The hand leaves the frame entirely.
    time.sleep(FRAME_SECONDS)
    controller.process_payload({'frame_seq': 999, 't': time.monotonic(), 'fps': 30.0,
                                'capture_fps': 30.0, 'inference_ms': 5.0, 'frame': None,
                                'hands': [], 'error': None,
                                'source_width': 1280, 'source_height': 720})
    assert broker.poll()['owned'] == [], 'a lost hand must release the held button'
    assert not broker.poll()['enabled'], 'the broker must stop acting once the hand is gone'

    presses_before = broker.presses()
    # The same closed pinch returning and moving must not become a second drag,
    # and its eventual release must not become a click.
    feed(controller, [('pointer', .18, (.66 + frame * .004, .55)) for frame in range(18)]
         + [('pointer', .9, (.728, .55))] * 4)
    assert broker.presses() == presses_before
    assert broker.poll()['owned'] == []
    # Visible separation restores a fresh click without requiring an open palm.
    feed(controller, [('pointer', .18, (.728, .55))] * 5
         + [('pointer', .9, (.728, .55))] * 2)
    assert broker.presses() == presses_before + [('left', 'left')]


def test_no_input_is_ever_emitted_before_a_hand_is_tracked(running_controller):
    controller, broker = running_controller
    feed(controller, [('open', 0.95, (0.66, 0.55))] * 5)
    assert broker.backend.events == [], 'an open palm must never inject anything'
    feed(controller, [('unknown', 0.95, (0.66, 0.55))] * 4)
    assert broker.backend.events == [], 'a fist must never inject anything'


# ------------------------------------------------------------------ the promise
# "A hand the default thresholds cannot read becomes usable after measuring it."
# This drives that whole loop: measure -> write the profile -> restart -> take control.

RELAXED_CURL = 0.30     # index finger at ~0.375 extension score: below the default bar


def relaxed_packet(sequence, palm=(0.66, 0.55)):
    hand = hand_model.as_hand(pose='pointer', curls=(RELAXED_CURL, 1.0, 1.0, 1.0),
                              palm=palm, width=1280, height=720)
    return {'frame_seq': sequence, 't': time.monotonic(), 'fps': 30.0, 'capture_fps': 30.0,
            'inference_ms': 5.0, 'frame': None, 'error': None,
            'source_width': 1280, 'source_height': 720, 'hands': [hand]}


def score_rows(value, count=20):
    return [{name: value for name in ('thumb', 'index', 'middle', 'ring', 'pinky')}
            for _ in range(count)]


def _controller_for(data_dir):
    from PySide6.QtWidgets import QApplication

    from airpad.runtime import AirpadController

    QApplication.instance() or QApplication([])
    brokers = []

    def factory(**kwargs):
        broker = InProcessBroker(**kwargs)
        brokers.append(broker)
        return broker

    controller = AirpadController(data_dir, vision_factory=ScriptedVision,
                                  broker_factory=factory)
    return controller, brokers


def test_measuring_the_hand_is_what_makes_it_work(tmp_path):
    """The product promise, end to end.

    A relaxed pointing finger scores below the built-in threshold, so the app cannot read
    it. After the guided measurement writes thresholds derived from that same hand's
    distributions, the identical posture takes control.
    """
    from airpad.config import Profile
    from airpad.thresholds import derive_finger_thresholds

    # 1. Before measuring: the hand is visible but unusable, and nothing is injected.
    before, before_brokers = _controller_for(tmp_path)
    try:
        before.start_camera()
        before.set_practice(False)
        before.set_enabled(True)
        for frame in range(12):
            time.sleep(.033)
            before.process_payload(relaxed_packet(before._frame_seq + 1))
        assert not before._broker_active, 'the default thresholds must not read this hand'
        assert before.engine.state != 'TRACKING'
        assert before_brokers[0].backend.events == []
        assert 'recognis' in (before.hand_state or '') or 'pointing' in (before.hand_state or ''), \
            before.hand_state
    finally:
        before.shutdown()

    # 2. The guided measurement, with distributions that describe this hand.
    thresholds, report = derive_finger_thresholds(
        score_rows(0.70),                    # open palm: every finger high
        score_rows(0.10),                    # fist: every finger low
        [{**{name: 0.10 for name in ('thumb', 'middle', 'ring', 'pinky')}, 'index': 0.375}
         for _ in range(20)])                # pointing: the relaxed index scores 0.375
    index = report['per_finger']['index']
    assert index['source'] == 'measured'
    assert index['threshold'] < 0.375, 'the measured threshold must accept this hand'
    Profile().updated({'finger_thresholds': thresholds,
                       'finger_marginals': report['marginals'],
                       'calibrated': True}).save(tmp_path / 'profile.json')

    # 3. After measuring: the same hand takes control.
    after, after_brokers = _controller_for(tmp_path)
    try:
        assert after.profile.finger_thresholds == tuple(thresholds), 'the profile must load'
        after.start_camera()
        after.set_practice(False)
        after.set_enabled(True)
        # Keep feeding after control is taken: the frame that takes control also resets the
        # motion baseline, so movement can only appear on the frames after it.
        for frame in range(20):
            time.sleep(.033)
            after.process_payload(relaxed_packet(after._frame_seq + 1,
                                                 palm=(0.66 + 0.004 * frame, 0.55)))
        assert after._broker_active, f'measured thresholds did not take control: {after.hand_state}'
        assert after.engine.state == 'TRACKING'
    finally:
        after.shutdown()

    moves = [event for event in after_brokers[0].backend.events if event[0] == 'move']
    assert moves, 'the relaxed hand should now move the pointer'
    assert after_brokers[0].poll()['owned'] == []


def test_the_physical_emergency_keys_stop_a_held_drag(running_controller):
    """Ctrl+Alt+Space while a drag is held must release it and stop the broker.

    The backend only exposes an async key state, so setting the physical set is exactly
    what the real one reports when a person presses the combination.
    """
    controller, broker = running_controller
    release_frame = CLICK_AND_DRAG.index(('pointer', 0.70, (0.84, 0.55)))
    hold_drag = CLICK_AND_DRAG[:release_frame] + [('pointer', 0.18, (0.84, 0.55))] * 2
    feed(controller, hold_drag)
    assert broker.poll()['owned'] == ['left'], 'the drag should be holding the button'

    broker.backend.physical = {'ctrl', 'alt', 'space'}
    feed(controller, [('pointer', 0.18, (0.84, 0.55))])
    assert broker.poll()['owned'] == [], 'the emergency stop must release the held button'
    assert broker.state.emergency, 'the broker must latch the emergency state'
    assert not broker.poll()['enabled']


def test_a_physically_held_button_is_never_fought(running_controller):
    """If the user is holding the real mouse button, AIRPAD must refuse rather than click."""
    controller, broker = running_controller
    broker.backend.physical = {'left'}
    reported = []
    for row in CLICK_AND_DRAG:
        pose, pinch, palm = row[0], row[1], row[2]
        time.sleep(FRAME_SECONDS)
        controller.process_payload(packet(controller._frame_seq + 1, pose=pose, pinch=pinch,
                                          palm=palm))
        if broker.state.error:
            reported.append(broker.state.error)
    assert broker.backend.events == [], 'no synthetic button events while the real one is held'
    assert reported, 'the refusal must be reported when it happens'
    assert any('physically' in message or 'held' in message for message in reported), reported
    assert all(bit == 0 for bit in broker.backend.ledger)


def test_two_finger_motion_reaches_the_backend_as_wheel_events(scroll_controller):
    controller, broker = scroll_controller
    script = ([('open', 0.95, (0.66, 0.55))] * 3
              + [('pointer', 0.95, (0.66, 0.55))] * 8
              + [('two', 0.95, (0.66, 0.55 + 0.03 * step)) for step in range(1, 14)])
    feed(controller, script)
    wheel = [event for event in broker.backend.events if event[0] == 'wheel']
    assert wheel, f'expected wheel events, got {broker.backend.events[:8]}'
    assert all(event[2] != 0 for event in wheel), 'the scroll must be vertical'
    assert broker.presses() == [], 'scrolling must never press a button'


def test_two_finger_separation_reaches_the_backend_as_a_ctrl_wheel_chord(scroll_controller):
    controller, broker = scroll_controller
    # two_spread is the fan angle between the two fingers, in degrees: it has to open far
    # enough for the separation ratio to clear the 0.12 log threshold.
    script = ([('open', 0.95, (0.66, 0.55))] * 3
              + [('pointer', 0.95, (0.66, 0.55))] * 8
              + [('two', 0.95, (0.66, 0.55), 8 + 3 * step) for step in range(1, 14)])
    feed(controller, script)
    events = broker.backend.events
    chord = [event for event in events if event[0] == 'wheel']
    assert chord, f'expected a ctrl+wheel chord, got {events[:8]}'
    keys = [event[1] for event in events if event[0] in ('down', 'up')]
    assert keys and set(keys) == {'ctrl'}, f'zoom must use Ctrl only, got {set(keys)}'
    assert keys.count('ctrl') % 2 == 0, 'every Ctrl press must be released'
    assert broker.poll()['owned'] == [], 'no key may be left down'
