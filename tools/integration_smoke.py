"""Real camera + real UI + real core smoke. Practice mode only; no frame is ever saved.

This is the hardware half of the pair: tools/replay_smoke.py proves the wired-together
application with scripted landmarks, and this proves the camera and the model actually
feed it. It reports honestly whether a hand was ever in view, because with an empty frame
the gesture path remains untested by this run.

Usage:
    python tools/integration_smoke.py --seconds 10
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


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from airpad.runtime import AirpadController
    from airpad.ui import MainWindow

    parser = argparse.ArgumentParser(description='Real camera end-to-end smoke (practice only).')
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--output', default='artifacts/integration-smoke.json')
    parser.add_argument('--screenshot', default='artifacts/integration-camera.png',
                        help='Save the running window here (empty string disables it)')
    parser.add_argument('--data-dir', default='runtime-data/integration-smoke')
    args = parser.parse_args()

    app = QApplication([])
    data_dir = ROOT / args.data_dir
    broker_calls = []

    def broker_factory(**kwargs):
        broker_calls.append(kwargs)
        raise RuntimeError('practice mode must never create a desktop input broker')

    controller = AirpadController(data_dir, broker_factory=broker_factory)
    window = MainWindow(controller)
    window.resize(1180, 760)
    window.show()

    snapshots = []
    controller.updated.connect(lambda row: snapshots.append(dict(row)))
    controller.start_camera()
    controller.set_practice(True)
    controller.set_enabled(True)

    started = time.monotonic()
    shot = {}
    if args.screenshot:
        def capture():
            path = ROOT / args.screenshot
            path.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(path))
            shot.update({'path': path.relative_to(ROOT).as_posix(),
                         'bytes': path.stat().st_size,
                         'captured_at_s': round(time.monotonic() - started, 2)})
        QTimer.singleShot(max(1500, int(args.seconds * 700)), capture)

    QTimer.singleShot(args.seconds * 1000, app.quit)
    app.aboutToQuit.connect(controller.shutdown)
    app.exec()
    controller.shutdown()
    elapsed = time.monotonic() - started

    active = [row for row in snapshots if row['camera_state'] == 'running']
    inferences = [row['inference_ms'] for row in active if row['inference_ms']]
    hand_rows = [row for row in active if row['quality']]
    fps_samples = [row['fps'] for row in active if row['fps']]

    checks = [
        ('camera opened and produced frames', bool(active) and controller._frame_seq > 0,
         controller._frame_seq),
        ('frame rate measured', bool(fps_samples) and max(fps_samples) > 10,
         round(max(fps_samples), 1) if fps_samples else 0),
        ('inference timed', bool(inferences), len(inferences)),
        ('no desktop broker created', not broker_calls, broker_calls),
        ('no error raised', not controller.error, controller.error or None),
        ('practice mode stayed on', bool(active) and all(row['practice'] for row in active), True),
        ('screenshot captured', bool(shot), shot.get('path')),
    ]
    result = {
        'smoke': 'integration-smoke', 'source': 'real camera, real model, real UI',
        'elapsed_s': round(elapsed, 2), 'frames_processed': controller._frame_seq,
        'active_snapshots': len(active),
        'fps_max': round(max(fps_samples), 2) if fps_samples else None,
        'fps_median': round(statistics.median(fps_samples), 2) if fps_samples else None,
        'inference_ms_p50': round(statistics.median(inferences), 2) if inferences else None,
        'inference_ms_max': round(max(inferences), 2) if inferences else None,
        'geometry_valid_snapshots': len(hand_rows),
        'hand_seen': bool(hand_rows),
        'hand_note': ('a hand was tracked during this run' if hand_rows else
                      'NO HAND WAS IN VIEW: the camera, model and UI ran, but the gesture '
                      'path was not exercised by this run'),
        'states': sorted({row['state'] for row in snapshots}),
        'hand_states': sorted({row['hand_state'] for row in active}),
        'practice_only': True, 'desktop_broker_created': bool(broker_calls),
        'frames_saved': 0, 'screenshot': shot or None,
        'checks': [{'name': n, 'passed': bool(ok), 'detail': d} for n, ok, d in checks],
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
