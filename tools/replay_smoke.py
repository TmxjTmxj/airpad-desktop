"""End-to-end smoke: the real controller and the real UI driven by the scripted fixture.

This runs the actual AirpadController, the actual geometry gate, the actual gesture state
machine and the actual MainWindow -- the only substituted part is the vision client, which
replays 21-landmark observations from tests/fixtures/landmarks/pointer.jsonl instead of a
camera. It therefore proves the wired-together application works, and says nothing about
camera or human performance.

Safety: practice mode is used throughout, so no desktop input may be injected. The broker
factory raises if the controller ever tries to create a real input broker, and that is
asserted in the verdict.

Usage:
    python tools/replay_smoke.py [--seconds N] [--output artifacts/replay-smoke.json]
"""
import argparse
import json
import multiprocessing
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / 'tests' / 'fixtures' / 'landmarks' / 'pointer.jsonl'
SHOTS = (('click', 1.80), ('drag', 2.85), ('two-finger', 4.05))


class FixtureVision:
    """Vision-client shaped replay of the landmark fixture, paced in real time."""

    def __init__(self, fixture=FIXTURE, camera=0, backend='auto'):
        self.rows = []
        for line in Path(fixture).open(encoding='utf-8-sig'):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('type') == 'header':
                self.width, self.height = int(row['width']), int(row['height'])
                continue
            self.rows.append(row)
        self.camera, self.backend = camera, backend
        self._sequence = 0
        self._index = 0
        self._started = None
        self._background = None

    def start(self):
        self._started = time.monotonic()
        self._background = self._render_background()

    def _render_background(self):
        """A deliberately synthetic frame, watermarked so it cannot be mistaken for a camera."""
        import cv2
        import numpy as np
        frame = np.zeros((self.height, self.width, 3), np.uint8)
        frame[:] = (34, 26, 20)
        for x in range(0, self.width, 40):
            cv2.line(frame, (x, 0), (x, self.height), (52, 42, 34), 1)
        for y in range(0, self.height, 40):
            cv2.line(frame, (0, y), (self.width, y), (52, 42, 34), 1)
        cv2.putText(frame, 'SYNTHETIC HAND - NO CAMERA', (16, self.height - 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (150, 224, 180), 2, cv2.LINE_AA)
        return frame

    def poll(self):
        if self._started is None or self._index >= len(self.rows):
            return None
        now = time.monotonic()
        row = self.rows[self._index]
        if now < self._started + float(row['t']):
            return None
        self._index += 1
        self._sequence += 1
        hands = []
        if row.get('type') == 'landmarks':
            hands = [{'landmarks': row['landmarks'],
                      'handedness': row.get('handedness', 'Unknown'),
                      'handedness_score': row.get('handedness_score', 0.0)}]
        return {'frame_seq': self._sequence, 't': now, 'fps': 30.0, 'capture_fps': 30.0,
                'inference_ms': 8.0, 'frame': self._background, 'hands': hands,
                'error': None, 'source_width': self.width, 'source_height': self.height}

    def close(self):
        self._index = len(self.rows)

    @property
    def delivered(self):
        return self._sequence


def overlay_pixels(path):
    """Count the app's own skeleton colours in a saved screenshot.

    runtime.py draws bones in BGR (157, 220, 81) and joints in BGR (218, 251, 225), so
    finding those pixels proves the overlay actually ran over the replayed landmarks.
    """
    import cv2
    import numpy as np
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return 0, 0

    def count(colour):
        low = np.array([max(0, c - 24) for c in colour], dtype=np.int16)
        high = np.array([min(255, c + 24) for c in colour], dtype=np.int16)
        data = image.astype(np.int16)
        return int(np.all((data >= low) & (data <= high), axis=2).sum())

    bones = max(count((157, 220, 81)), count((81, 220, 157)))
    joints = max(count((218, 251, 225)), count((225, 251, 218)))
    return bones, joints


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from airpad.runtime import AirpadController
    from airpad.ui import MainWindow

    parser = argparse.ArgumentParser(description='Fixture-driven end-to-end UI smoke (practice mode only).')
    parser.add_argument('--output', default='artifacts/replay-smoke.json')
    parser.add_argument('--data-dir', default='runtime-data/replay-smoke')
    parser.add_argument('--seconds', type=float, default=5.5, help='Total run length')
    args = parser.parse_args()

    app = QApplication([])
    data_dir = ROOT / args.data_dir
    broker_calls = []

    def broker_factory(**kwargs):
        broker_calls.append(kwargs)
        raise RuntimeError('practice mode must never create a desktop input broker')

    vision = FixtureVision()
    controller = AirpadController(data_dir, vision_factory=lambda **kw: vision,
                                  broker_factory=broker_factory)
    window = MainWindow(controller)
    window.resize(1180, 760)
    window.show()

    shots = []
    observed = {'states': [], 'inputs': [], 'performance': []}
    controller.updated.connect(lambda payload: observed['states'].append(payload['state']))

    def shoot(name, moment):
        path = ROOT / 'artifacts' / f'replay-smoke-{name}.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        window.grab().save(str(path))
        skeleton, joints = overlay_pixels(path)
        shots.append({'name': name, 'at_s': moment, 'path': path.relative_to(ROOT).as_posix(),
                      'bytes': path.stat().st_size, 'skeleton_px': skeleton, 'joint_px': joints,
                      'practice_x': round(controller.practice_x, 4),
                      'practice_y': round(controller.practice_y, 4),
                      'click_count': controller.click_count, 'dragging': bool(controller.dragging),
                      'state': controller.engine.state})

    captured = set()

    def capture_once(name):
        """Capture when the state actually happens, not at a guessed wall-clock time.

        The Qt loop lags the fixture timeline by a few hundred milliseconds, so fixed
        timers were catching the wrong moment; these triggers cannot miss.
        """
        if name in captured:
            return
        captured.add(name)
        shoot(name, round(time.monotonic() - started, 3))

    controller.updated.connect(
        lambda payload: (capture_once('click') if payload['click_count'] >= 1 else None,
                         capture_once('drag') if payload['dragging'] else None,
                         capture_once('two-finger') if vision.delivered >= 118 else None))

    started = time.monotonic()
    controller.start_camera()
    controller.set_practice(True)
    controller.set_enabled(True)
    QTimer.singleShot(int(args.seconds * 1000), app.quit)
    app.aboutToQuit.connect(controller.shutdown)
    app.exec()
    controller.shutdown()
    elapsed = time.monotonic() - started

    log_path = data_dir / 'logs' / 'session.jsonl'
    log_events = []
    if log_path.is_file():
        for line in log_path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                try:
                    log_events.append(json.loads(line))
                except ValueError:
                    pass
    logged_states = sorted({row['state'] for row in log_events if row.get('event') == 'state'})
    logged_inputs = sorted({row['kind_name'] for row in log_events if row.get('event') == 'input'})

    expected_states = {'CLUTCH', 'TRACKING', 'PINCH_HOLD', 'DRAGGING'}
    checks = [
        ('every fixture frame delivered', vision.delivered >= 130, vision.delivered),
        ('camera reported running', controller.camera_state == 'running', controller.camera_state),
        ('gesture states reached', expected_states <= set(logged_states), sorted(logged_states)),
        ('click registered in practice', controller.click_count == 1, controller.click_count),
        ('drag engaged and released',
         'left_down' in logged_inputs and 'left_up' in logged_inputs, logged_inputs),
        ('drag screenshot caught the drag', any(s['dragging'] for s in shots),
         [(s['name'], s['state']) for s in shots]),
        ('practice pointer moved', abs(controller.practice_x - 0.5) > 0.02,
         round(controller.practice_x, 4)),
        ('no desktop broker created', not broker_calls, broker_calls),
        ('no error raised', not controller.error, controller.error or None),
        ('screenshots captured', len(shots) == len(SHOTS), sorted(captured)),
        ('screenshots show the drawn skeleton',
         all(s['skeleton_px'] >= 200 and s['joint_px'] >= 100 for s in shots),
         [(s['name'], s['skeleton_px'], s['joint_px']) for s in shots]),
    ]
    result = {
        'smoke': 'replay-smoke', 'source': 'synthetic-scripted fixture, no camera, no person',
        'fixture': FIXTURE.relative_to(ROOT).as_posix(),
        'profile': 'shipping defaults (two-finger scroll is opt-in and stays off here)',
        'elapsed_s': round(elapsed, 2), 'frames_delivered': vision.delivered,
        'practice_only': True, 'desktop_broker_created': bool(broker_calls),
        'frames_saved': 0,
        'logged_states': logged_states, 'logged_inputs': logged_inputs,
        'screenshots': shots,
        'checks': [{'name': name, 'passed': bool(ok), 'detail': detail} for name, ok, detail in checks],
    }
    result['status'] = 'passed' if all(ok for _, ok, _ in checks) else 'failed'
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
