"""Live hand check: measure your own hand and derive thresholds from it.

Fixed thresholds are a guess about somebody else's hand. This tool runs a short guided
sequence - open palm, fist, point, pinch - through the same geometry gate and gesture
state machine the app uses, shows the live numbers, then places each finger's threshold in
the gap between your extended and curled score distributions.

It never touches the desktop: no input broker is created.

Usage:
    .venv\\Scripts\\python.exe tools\\hand_check.py                # measure only
    .venv\\Scripts\\python.exe tools\\hand_check.py --apply        # also write the result
                                                                  # into runtime-data/profile.json
"""
import argparse
import json
import multiprocessing
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PHASES = (
    ('open', '1 / 5   张开手掌\n五指全部伸直，对着镜头保持', 2.5),
    ('fist', '2 / 5   握拳\n五指全部收起，握成一个拳', 2.5),
    ('point', '3 / 5   只伸食指\n像指点屏幕那样，其余三指收起', 2.5),
    ('sweep', '4 / 5   左右移动食指\n在你觉得舒服的范围内来回移动，不要勉强够', 3.0),
    ('pinch', '5 / 5   捏合\n拇指与食指反复捏合、张开，约 5 次', 4.0),
)
# A phase needs at least this many usable frames before its measurement means anything.
PHASE_MINIMUM = 12


from airpad.thresholds import percentile, recommend_pinch
from airpad.calibration import measurement_row


class FixtureVision:
    """Replay a landmark file instead of opening a camera, in the VisionClient's shape.

    Used by --source fixture so the measurement path can be exercised without a person.
    `speed` scales the timeline so a test can run the whole five-phase sequence quickly.
    """

    def __init__(self, path, speed=1.0):
        rows = []
        self.width, self.height = 640, 480
        for line in Path(path).open(encoding='utf-8-sig'):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('type') == 'header':
                self.width, self.height = int(row['width']), int(row['height'])
                continue
            rows.append(row)
        self.rows = rows
        self.speed = max(0.05, float(speed))
        self.sequence = 0
        self.index = 0
        self.started = None

    def start(self):
        self.started = time.monotonic()

    def poll(self):
        if self.started is None or self.index >= len(self.rows):
            return None
        now = time.monotonic()
        # Release every frame that is due and keep the newest, exactly as the real
        # latest-slot pipeline does. Returning one frame per call made the replay fall
        # behind its own timeline, which shifted every phase window.
        due = None
        while self.index < len(self.rows):
            row = self.rows[self.index]
            if now < self.started + float(row['t']) * self.speed:
                break
            due = row
            self.index += 1
        if due is None:
            return None
        self.sequence += 1
        hands = []
        if due.get('type') == 'landmarks':
            hands = [{'landmarks': due['landmarks'],
                      'handedness': due.get('handedness', 'Unknown'),
                      'handedness_score': due.get('handedness_score', 0.0)}]
        return {'frame_seq': self.sequence, 't': now, 'fps': 30.0, 'capture_fps': 30.0,
                'inference_ms': 5.0, 'frame': None, 'hands': hands, 'error': None,
                'source_width': self.width, 'source_height': self.height}

    def close(self):
        self.index = len(self.rows)


def app_is_running(data_dir):
    """True when an AIRPAD instance holds the single-instance lock in this data directory.

    Writing the profile while the app runs is a trap: the app later saves its own in-memory
    profile when any setting changes, silently overwriting the measurement.
    """
    from PySide6.QtCore import QLockFile

    directory = Path(data_dir)
    # QLockFile cannot create its file in a missing directory, and that must not be
    # mistaken for "the app is running".
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / 'airpad.lock'))
    if lock.tryLock(0):
        lock.unlock()
        return False
    return True


def thin_phases(phase_samples, needed=PHASE_MINIMUM):
    """Phases that collected too few usable frames, excluding the pinch phase."""
    return [name for name, count in phase_samples.items()
            if name != 'pinch' and count < needed]


def apply_blocker(phase_samples, running):
    """Why this measurement must not be written, or '' when it may be.

    Writing an unusable measurement is how a hand that never actually pinched during the
    phase ended up with a pinch threshold below its own closed level: the file looked
    valid, the app loaded it, and no pinch could ever cross it, so every click silently
    vanished. An unusable run is refused out loud, with the phases to redo.
    """
    thin = thin_phases(phase_samples)
    if thin:
        return ('Measurement is not usable yet, so nothing was written. These phases '
                'collected too few frames: ' + ', '.join(thin) + '.')
    if running:
        return ('AIRPAD is running. It would overwrite this file the next time a setting '
                'changes, losing the measurement. Close AIRPAD and re-run.')
    return ''


def build_hint(phase_samples, report, pinch, sensitivity=None, sweep_report=None):
    """Say what to redo, instead of just reporting that something fell back."""
    thin = thin_phases(phase_samples)
    if thin:
        return ('These phases collected too few frames: ' + ', '.join(thin) +
                '. Re-run and keep your hand in view for the whole sequence.')
    unusable = list(report.get('defaults_used') or [])
    if unusable:
        details = ', '.join(f"{finger} ({report['per_finger'][finger].get('reason', 'no reason')})"
                            for finger in unusable)
        return ('No threshold could be measured for: ' + details +
                '. Those fingers keep the built-in default and may still misread. Re-run and '
                'make each posture clearly different from the others.')
    notes = []
    narrow = list(report.get('narrow_fingers') or [])
    if narrow:
        notes.append('the gap between your extended and curled scores is narrow for '
                     + ', '.join(narrow) + ', so those thresholds have little margin '
                     '(re-run with a firmer fist and a wider open hand to widen it)')
    if sensitivity is None:
        notes.append('the sweep phase moved too little to measure a comfortable range, so '
                     'the pointer gain was left unchanged (repeat it moving your index '
                     'across the range you would actually use)')
    clamped = (sweep_report or {}).get('clamped')
    if clamped == 'high':
        notes.append('the sweep was small enough that the pointer gain hit its maximum, so '
                     'the cursor will move less than one comfortable sweep should (repeat '
                     'the sweep phase moving your index across the range you would actually '
                     'use, and keep your whole hand inside the frame)')
    elif clamped == 'low':
        notes.append('the sweep was so wide that the pointer gain hit its minimum, so the '
                     'cursor may feel twitchy (repeat the sweep phase over the range you '
                     'would actually use)')
    if not pinch:
        notes.append('your pinch samples did not separate into a closed and an open level, '
                     'or the closed level was too high to place a threshold below it, so '
                     'the pinch thresholds were left unchanged (repeat the pinch phase and '
                     'really touch thumb and index tip together, then open them wide)')
    if not notes:
        return 'All five phases measured cleanly.'
    return 'Measured, with caveats: ' + '; '.join(notes) + '.'


def main():
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
    from PySide6.QtWidgets import QApplication, QWidget
    from airpad.config import Profile
    from airpad.core import GestureEngine
    from airpad.geometry import FINGERS, FingerStability, HandSelector, extract_sample
    from airpad.vision import VisionClient

    parser = argparse.ArgumentParser(description='Guided hand measurement (never touches the desktop).')
    parser.add_argument('--apply', action='store_true',
                        help='Write the measured thresholds into the profile')
    parser.add_argument('--source', choices=['camera', 'fixture'], default='camera',
                        help='fixture replays a scripted hand: no camera, no person')
    parser.add_argument('--fixture', default='tests/fixtures/landmarks/measure.jsonl')
    parser.add_argument('--speed', type=float, default=1.0,
                        help='Scale the phase timings and the fixture timeline (testing)')
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--backend', default='auto', choices=['auto', 'msmf', 'dshow'])
    parser.add_argument('--output', default='artifacts/hand-check.json')
    parser.add_argument('--screenshot', default='artifacts/hand-check.png')
    parser.add_argument('--data-dir', default='runtime-data')
    args = parser.parse_args()

    profile = Profile()
    engine = GestureEngine(profile)
    selector = HandSelector(profile.dominant_hand)
    stability = FingerStability()
    if args.source == 'fixture':
        vision = FixtureVision(ROOT / args.fixture, args.speed)
    else:
        vision = VisionClient(camera=args.camera, backend=args.backend)
    phase_seconds = [seconds * max(0.05, args.speed) for _, _, seconds in PHASES]

    collected = {name: [] for name, _, _ in PHASES}
    free = {'frames': 0, 'with_hand': 0, 'valid': 0, 'poses': {}, 'reasons': {},
            'states': {}, 'actions': {}, 'fps': [], 'inference_ms': [], 'source_size': None}
    app = QApplication([])

    class CheckWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle('AIRPAD hand check - follow the prompts')
            self.resize(1180, 720)
            self.frame = None
            self.prompt = PHASES[0][1]
            self.remaining = PHASES[0][2]
            self.bars = []
            self.status = 'starting camera...'

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('#0d1512'))
            panel = 340
            if self.frame is not None:
                image = self.frame
                frame_height, frame_width = image.shape[:2]
                target_width = max(1, self.width() - panel)
                qimage = QImage(image.data, frame_width, frame_height, image.strides[0],
                                QImage.Format_BGR888)
                painter.drawImage(0, 0, qimage.scaled(target_width,
                                                      int(frame_height * target_width / frame_width),
                                                      Qt.KeepAspectRatio, Qt.SmoothTransformation))
            painter.setPen(QColor('#e8f6ef'))
            painter.setFont(QFont('Segoe UI', 15, QFont.Bold))
            y = 34
            for line in self.prompt.split('\n'):
                painter.drawText(14, y, line)
                y += 26
            painter.setFont(QFont('Consolas', 11))
            painter.setPen(QColor('#80f0c2'))
            painter.drawText(14, y + 6, f'{self.remaining:0.1f} s')
            painter.setFont(QFont('Consolas', 9))
            painter.setPen(QColor('#bfe9d5'))
            y += 34
            painter.drawText(14, y, self.status)
            y += 20
            for name, score, extended in self.bars:
                if score is None:
                    continue
                painter.setPen(QPen(QColor('#2c5a4a'), 1))
                painter.drawRect(112, y - 9, 150, 9)
                painter.fillRect(112, y - 9, int(150 * max(0.0, min(1.0, score))), 9,
                                 QColor('#80f0c2') if extended else QColor('#c9a227'))
                painter.setPen(QColor('#bfe9d5'))
                painter.drawText(14, y, f'{name:<7}{score:.3f}')
                y += 15
            if self.frame is None:
                painter.setPen(QColor('#e8f6ef'))
                painter.drawText(self.rect(), Qt.AlignCenter, 'waiting for the camera...')

    window = CheckWindow()
    window.show()
    phase = {'index': 0, 'until': 0.0, 'done': False}
    finished = {'done': False}

    def begin_phase(index):
        window.prompt = PHASES[index][1]
        window.remaining = phase_seconds[index]
        phase['until'] = time.monotonic() + phase_seconds[index]

    def observe():
        packet = vision.poll()
        now = time.monotonic()
        if phase['index'] < len(PHASES):
            left = max(0.0, phase['until'] - now)
            window.remaining = left
            window.update()
            if left <= 0 and not phase['done']:
                phase['index'] += 1
                if phase['index'] < len(PHASES):
                    begin_phase(phase['index'])
                else:
                    finish()
                return
        if packet is None:
            return
        if packet.get('error'):
            window.status = f"camera error: {packet['error']}"
            window.update()
            return
        free['frames'] += 1
        free['source_size'] = [packet.get('source_width'), packet.get('source_height')]
        free['fps'].append(round(float(packet.get('fps') or 0), 2))
        free['inference_ms'].append(round(float(packet.get('inference_ms') or 0), 2))
        hands = packet.get('hands') or []
        sample = None
        if hands:
            free['with_hand'] += 1
            chosen = selector.select(hands, packet['t'])
            if chosen is not None:
                sample, quality = extract_sample(chosen, packet['t'],
                                                 packet.get('source_width', 640),
                                                 packet.get('source_height', 480),
                                                 selector.track_id, stability)
            else:
                # The selector refused (crossing, ambiguous, or the wrong hand). Record
                # *its* reason, not the one captured before the call.
                quality = {'valid': False, 'reason': selector.reason}
        else:
            quality = {'valid': False, 'reason': 'No hand detected'}
        if quality.get('valid'):
            free['valid'] += 1
            free['poses'][sample.pose] = free['poses'].get(sample.pose, 0) + 1
        # Collect whenever the geometry actually scored the fingers - even when the pose
        # itself is refused. A fist is deliberately invalid for control (it must release),
        # but it is the only source of the "curled" distribution the thresholds are
        # derived from; skipping it left every finger without a measured threshold.
        row = measurement_row(chosen, packet.get('source_width', 640),
                              packet.get('source_height', 480), quality)
        if phase['index'] < len(PHASES) and row is not None:
            collected[PHASES[phase['index']][0]].append(row)
        free['reasons'][quality.get('reason', selector.reason)] = \
            free['reasons'].get(quality.get('reason', selector.reason), 0) + 1
        actions = engine.step(sample, now)
        free['states'][engine.state] = free['states'].get(engine.state, 0) + 1
        for action in actions:
            free['actions'][action.kind] = free['actions'].get(action.kind, 0) + 1
        window.frame = packet.get('frame')
        window.bars = [(name, (quality.get('scores') or {}).get(name),
                        bool((quality.get('fingers') or {}).get(name))) for name in FINGERS]
        window.status = (f"{packet.get('source_width')}x{packet.get('source_height')}   "
                         f"{packet.get('fps', 0):.1f} fps   "
                         f"{packet.get('inference_ms', 0):.1f} ms   "
                         f"hand: {selector.reason}   pose: "
                         f"{(sample.pose if sample else '-')}   state: {engine.state}")
        window.update()

    def finish():
        if finished['done']:
            return
        finished['done'] = True
        timer.stop()

        from airpad.thresholds import derive_finger_thresholds, derive_sensitivity

        open_scores = [row['scores'] for row in collected['open']]
        fist_scores = [row['scores'] for row in collected['fist']]
        pointer_scores = [row['scores'] for row in collected['point']]
        thresholds, report = derive_finger_thresholds(open_scores, fist_scores, pointer_scores)
        pinch = recommend_pinch([row['pinch'] for row in collected['pinch']])
        sensitivity, sweep_report = derive_sensitivity(
            [row['index_x'] for row in collected['sweep']])

        if args.screenshot:
            path = ROOT / args.screenshot
            path.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(path))

        def summary(values):
            if not values:
                return None
            return {'min': min(values), 'median': round(statistics.median(values), 4),
                    'max': max(values), 'samples': len(values)}

        result = {
            'hand_check': ('guided measurement on a real hand' if args.source == 'camera'
                           else 'scripted fixture: HARNESS SELF-CHECK, thresholds are '
                                'meaningless as a measurement'),
            'source': args.source,
            'note': ('Every number here came from this run. Nothing was produced by a model '
                     'or a fixture.' if args.source == 'camera' else
                     'Scripted landmarks from tests/fixtures/landmarks/measure.jsonl: this '
                     'run proves the measurement path works, not what your hand scores.'),
            'camera': {'source_size': free['source_size'], 'backend': args.backend,
                       'device': args.camera},
            'fps': summary(free['fps']), 'inference_ms': summary(free['inference_ms']),
            'frames': free['frames'], 'frames_with_hand': free['with_hand'],
            'frames_valid_geometry': free['valid'],
            'poses': free['poses'], 'states': free['states'], 'actions': free['actions'],
            'rejection_reasons': free['reasons'],
            'phase_samples': {name: len(rows) for name, rows in collected.items()},
            'enough_data': not thin_phases({name: len(rows)
                                            for name, rows in collected.items()}),
            'hint': build_hint({name: len(rows) for name, rows in collected.items()},
                               report, pinch, sensitivity, sweep_report),
            'recommended': {
                'finger_thresholds': list(thresholds),
                'finger_marginals': list(report.get('marginals', ())),
                'finger_order': ['thumb', 'index', 'middle', 'ring', 'pinky'],
                'threshold_report': report,
                'sensitivity': sensitivity,
                'sweep_report': sweep_report,
                'pinch': pinch,
            },
            'applied': False,
            'screenshot': args.screenshot,
        }

        if args.apply:
            path = ROOT / args.data_dir / 'profile.json'
            blocker = apply_blocker(result['phase_samples'],
                                    app_is_running(ROOT / args.data_dir))
            if blocker:
                result['apply_error'] = blocker
                result['next_step'] = (
                    'Re-run this command and keep your whole hand in view for every phase.'
                    if not result['enough_data'] else
                    'Close AIRPAD, then re-run this command with --apply.')
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                stored = Profile.load(path)
                changes = {'finger_thresholds': thresholds,
                           'finger_marginals': report.get('marginals', ()),
                           'calibrated': True}
                if sensitivity:
                    changes['sensitivity'] = sensitivity
                if pinch:
                    changes.update({key: pinch[key] for key in
                                    ('pinch_close', 'pinch_release', 'pinch_open',
                                     'pinch_contact')})
                try:
                    updated = stored.updated(changes)
                    updated.save(path)
                    result['applied'] = True
                    result['applied_to'] = path.resolve().as_posix()
                    result['applied_values'] = changes_to_json(changes)
                    result['next_step'] = ('Start AIRPAD (or restart it if it was already '
                                           'open before this measurement) to use these values.')
                except (ValueError, OSError) as exc:
                    result['apply_error'] = str(exc)

        out = ROOT / args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps(result, indent=2))
        app.quit()

    def changes_to_json(changes):
        return {key: (list(value) if isinstance(value, tuple) else value)
                for key, value in changes.items()}

    timer = QTimer()
    timer.setInterval(15)
    timer.timeout.connect(observe)
    timer.start()
    app.aboutToQuit.connect(lambda: None)
    vision.start()
    begin_phase(0)
    app.exec()
    vision.close()
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
