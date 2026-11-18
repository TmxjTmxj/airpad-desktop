"""Human click evaluation: the MVP accuracy gate that only a person can run.

Displays a sequence of targets. You move the practice pointer onto each target and pinch;
the tool records hits, misses, unintended activations and acquisition time, then writes a
report. Practice mode is used throughout, so nothing is ever injected into the desktop.

Usage (real camera, the actual measurement):
    python tools\\click_eval.py --targets 100

Harness self-check (scripted hand, proves the tool runs; hit counts are meaningless there
because the scripted hand does not aim at targets):
    python tools\\click_eval.py --source fixture --targets 5

The scoring logic is pure and unit-tested in tests/test_click_eval.py.
"""
import argparse
import json
import math
import multiprocessing
from pathlib import Path
import random
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))


class TrialScorer:
    """Pure scoring: targets in, pointer/click observations in, metrics out."""

    def __init__(self, targets, radius=0.09, timeout=8.0):
        self.targets = list(targets)
        self.radius = radius
        self.timeout = timeout
        self.index = 0
        self.trials = []
        self.started = 0.0
        self.last_clicks = 0
        self._dragging = False
        self.hits = 0
        self.unintended = 0
        self.timeouts = 0
        self.drag_events = 0

    @property
    def target(self):
        return self.targets[self.index] if self.index < len(self.targets) else None

    @property
    def finished(self):
        return self.index >= len(self.targets)

    def begin(self, now):
        self.started = now

    def within(self, x, y):
        if self.target is None:
            return False
        tx, ty = self.target
        return math.hypot(x - tx, y - ty) <= self.radius

    def observe(self, x, y, clicks, dragging, now):
        """Feed one status update; returns the events this update produced."""
        events = []
        if dragging and not self._dragging:
            self.drag_events += 1
            events.append('drag')
        self._dragging = bool(dragging)
        if clicks > self.last_clicks and not self.finished:
            for _ in range(clicks - self.last_clicks):
                if self.finished:
                    break
                if self.within(x, y):
                    self.hits += 1
                    self._close_trial('hit', now, event='hit', events=events)
                else:
                    # A click that was not on the target is an unintended activation.
                    self.unintended += 1
                    events.append('unintended')
        self.last_clicks = clicks
        if not self.finished and now - self.started > self.timeout:
            self.timeouts += 1
            self._close_trial('timeout', now, event='timeout', events=events)
        return events

    def _close_trial(self, result, now, event, events):
        self.trials.append({'target': self.index, 'result': result,
                            'seconds': round(now - self.started, 3),
                            'target_xy': [round(v, 4) for v in self.targets[self.index]]})
        events.append(event)
        self.index += 1
        self.started = now

    def report(self):
        attempted = self.hits + self.unintended
        acquisitions = [t['seconds'] for t in self.trials if t['result'] == 'hit']
        return {
            'targets': len(self.targets),
            'trials_completed': len(self.trials),
            'hits': self.hits,
            'timeouts': self.timeouts,
            'unintended_clicks': self.unintended,
            'drag_events': self.drag_events,
            'precision': round(self.hits / attempted, 4) if attempted else None,
            'recall': round(self.hits / len(self.targets), 4) if self.targets else None,
            'median_seconds': round(statistics.median(acquisitions), 3) if acquisitions else None,
            'trials': self.trials,
        }


def make_targets(count, seed, radius):
    """Seeded target positions, kept clear of the edges by the target radius."""
    generator = random.Random(seed)
    margin = radius + 0.02
    return [(round(generator.uniform(margin, 1 - margin), 4),
             round(generator.uniform(margin, 1 - margin), 4)) for _ in range(count)]


def run_window(controller, scorer, window_title, screenshot, on_finish):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QPainter, QPen
    from PySide6.QtWidgets import QWidget

    class EvalWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(window_title)
            self.resize(760, 560)
            self.pointer = (0.5, 0.5)
            self.flash = ''
            self.flash_until = 0.0

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('#101a16'))
            width, height = self.width(), self.height()
            painter.setPen(QPen(QColor('#24463a'), 1))
            for step in range(1, 10):
                painter.drawLine(int(width * step / 10), 0, int(width * step / 10), height)
                painter.drawLine(0, int(height * step / 10), width, int(height * step / 10))
            target = scorer.target
            if target is not None:
                centre = (target[0] * width, target[1] * height)
                radius = scorer.radius * width
                painter.setPen(QPen(QColor('#80f0c2'), 3))
                painter.drawEllipse(int(centre[0] - radius), int(centre[1] - radius),
                                    int(radius * 2), int(radius * 2))
                painter.drawLine(int(centre[0] - 8), int(centre[1]), int(centre[0] + 8), int(centre[1]))
                painter.drawLine(int(centre[0]), int(centre[1] - 8), int(centre[0]), int(centre[1] + 8))
            px, py = self.pointer[0] * width, self.pointer[1] * height
            painter.setPen(QPen(QColor('#e8f6ef'), 2))
            painter.drawEllipse(int(px - 9), int(py - 9), 18, 18)
            painter.setFont(QFont('Segoe UI', 11))
            painter.setPen(QColor('#9fd8c0'))
            lines = [f'trial {min(scorer.index + 1, len(scorer.targets))} / {len(scorer.targets)}',
                     f'hits {scorer.hits}   unintended {scorer.unintended}   '
                     f'timeouts {scorer.timeouts}   drags {scorer.drag_events}']
            if scorer.finished:
                lines.append('finished - writing report')
            else:
                lines.append('move the pointer onto the target, then pinch to click')
            y = 24
            for line in lines:
                painter.drawText(16, y, line)
                y += 20
            if self.flash and time.monotonic() < self.flash_until:
                painter.setFont(QFont('Segoe UI', 26, QFont.Bold))
                painter.setPen(QColor('#80f0c2') if self.flash == 'hit' else QColor('#ff9d7a'))
                painter.drawText(self.rect(), Qt.AlignCenter, self.flash.upper())

        def show_event(self, name):
            self.flash = name
            self.flash_until = time.monotonic() + 0.35

    window = EvalWindow()
    window.show()

    def on_update(payload):
        window.pointer = (payload['practice_x'], payload['practice_y'])
        events = scorer.observe(window.pointer[0], window.pointer[1], payload['click_count'],
                                payload['dragging'], time.monotonic())
        for name in events:
            if name in ('hit', 'unintended', 'timeout'):
                window.show_event(name)
        window.update()
        if scorer.finished:
            on_finish()

    controller.updated.connect(on_update)
    if screenshot:
        QTimer.singleShot(2000, lambda: (window.grab().save(str(screenshot))))
    return window


def main():
    from PySide6.QtWidgets import QApplication
    from airpad.runtime import AirpadController

    parser = argparse.ArgumentParser(description='Click accuracy evaluation (practice mode only).')
    parser.add_argument('--source', choices=['camera', 'fixture'], default='camera')
    parser.add_argument('--targets', type=int, default=100)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--radius', type=float, default=0.09, help='Target radius, normalised')
    parser.add_argument('--timeout', type=float, default=8.0, help='Seconds allowed per target')
    parser.add_argument('--output', default='artifacts/click-eval.json')
    parser.add_argument('--screenshot', default='artifacts/click-eval.png')
    parser.add_argument('--data-dir', default='runtime-data/click-eval')
    args = parser.parse_args()

    app = QApplication([])
    data_dir = ROOT / args.data_dir
    broker_calls = []

    def broker_factory(**kwargs):
        broker_calls.append(kwargs)
        raise RuntimeError('the evaluation must never create a desktop input broker')

    if args.source == 'fixture':
        from replay_smoke import FixtureVision
        vision = FixtureVision()
        controller = AirpadController(data_dir, vision_factory=lambda **kw: vision,
                                      broker_factory=broker_factory)
    else:
        controller = AirpadController(data_dir, broker_factory=broker_factory)

    scorer = TrialScorer(make_targets(args.targets, args.seed, args.radius),
                         radius=args.radius, timeout=args.timeout)
    started = time.monotonic()
    scorer.begin(started)

    finished = {'done': False}

    def finish():
        if finished['done']:
            return
        finished['done'] = True
        report = scorer.report()
        report.update({
            'evaluation': 'click-eval',
            'source': ('real camera and real hand' if args.source == 'camera'
                       else 'scripted fixture: HARNESS SELF-CHECK ONLY, hit counts are meaningless'),
            'seed': args.seed, 'radius': args.radius, 'timeout_s': args.timeout,
            'elapsed_s': round(time.monotonic() - started, 2),
            'practice_only': True, 'desktop_broker_created': bool(broker_calls),
            'frames_saved': 0,
            'caveat': ('Only a person using their own hand and camera can produce a valid '
                       'measurement. Targets are random per seed; a run of 100 targets is '
                       'the MVP gate.'),
        })
        out = ROOT / args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({k: v for k, v in report.items() if k != 'trials'}, indent=2))
        app.quit()

    from PySide6.QtCore import QTimer
    screenshot = ROOT / args.screenshot if args.screenshot else None
    window = run_window(controller, scorer, 'AIRPAD · click evaluation (practice only)',
                        screenshot, finish)
    controller.start_camera()
    controller.set_practice(True)
    controller.set_enabled(True)
    # A generous ceiling so a fixture self-check cannot hang; a person will finish sooner.
    guard = args.targets * args.timeout + 30
    QTimer.singleShot(int(guard * 1000), finish)
    app.aboutToQuit.connect(controller.shutdown)
    app.exec()
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
