"""Synthetic hand -> geometry -> controller -> practice UI. No camera or OS input."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def run(output):
    import numpy as np
    import cv2
    from PySide6.QtWidgets import QApplication
    from airpad.runtime import AirpadController
    from airpad.ui import MainWindow
    from hand_model import as_hand

    class ScriptedCamera:
        def __init__(self, **kwargs): pass
        def start(self): pass
        def poll(self): return None
        def close(self): pass

    attempts = []
    def no_broker(**kwargs):
        attempts.append(kwargs)
        raise AssertionError('Offline practice must never create a desktop broker')

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    report = {'source': 'synthetic landmarks; NOT human usability data',
              'camera_opened': False, 'os_input': False}
    with tempfile.TemporaryDirectory(prefix='airpad-touchpad-') as directory:
        controller = AirpadController(directory, vision_factory=ScriptedCamera,
                                      broker_factory=no_broker)
        controller.timer.stop()
        controller.set_practice(True)
        controller.update_profile({'two_finger_scroll': True})
        controller.start_camera()
        controller.set_enabled(True)
        window = MainWindow(controller)
        window.show()
        events, states, snapshots = [], [], []
        controller.updated.connect(lambda state: snapshots.append(dict(state)))
        apply_practice = controller._apply_practice
        def record(actions):
            events.extend(action.kind for action in actions)
            apply_practice(actions)
        controller._apply_practice = record
        canvas = np.full((480, 640, 3), (34, 26, 20), np.uint8)
        cv2.putText(canvas, 'SYNTHETIC HAND - NO CAMERA', (15, 455),
                    cv2.FONT_HERSHEY_SIMPLEX, .65, (150, 224, 180), 2)
        sequence = []
        def add(pose, count, pinch=.9, palm=(.66, .55)):
            sequence.extend((pose, pinch, palm) for _ in range(count))
        add('pointer', 12)
        add('pointer', 5, .18)
        add('pointer', 8)
        add('pointer', 8, .18)
        for i in range(1, 11): add('pointer', 1, .18, (.66 + i*.012, .55))
        add('pointer', 8, palm=(.78, .55))
        add('two', 7, palm=(.78, .55))
        add('pointer', 10, palm=(.78, .55))
        add('two', 7, palm=(.78, .55))
        for i in range(1, 14): add('two', 1, palm=(.78, .55 + i*.009))
        add('open', 8, palm=(.78, .667))
        try:
            for i, (pose, pinch, palm) in enumerate(sequence):
                timestamp = 100. + i/30
                hand = as_hand(pose=pose, pinch=pinch, palm=palm)
                packet = {'frame_seq': i, 't': timestamp, 'fps': 30., 'capture_fps': 30.,
                          'inference_ms': 0., 'frame': canvas, 'source_width': 640,
                          'source_height': 480, 'hands': [hand]}
                with patch('airpad.runtime.time.monotonic', return_value=timestamp):
                    controller.process_payload(packet)
                    controller._emit()
                states.append(controller.engine.state)
                app.processEvents()
            shot = output / 'touchpad-practice.png'
            saved = window.grab().save(str(shot))
            checks = {
                'one_left_click': events.count('click') == 1,
                'one_drag': events.count('left_down') == 1 and events.count('left_up') == 1,
                'one_right_click': events.count('right_click') == 1,
                'scroll_produced': events.count('wheel') > 0,
                'no_broker_created': controller.broker is None and not attempts,
                'no_button_held': not controller.dragging,
                'screenshot_saved': saved,
            }
            report.update(frames=len(sequence), actions={kind: events.count(kind) for kind in sorted(set(events))},
                          states=list(dict.fromkeys(states)), checks=checks,
                          ok=all(checks.values()), screenshot=str(shot.resolve()))
        finally:
            controller.shutdown()
            window.close()
            app.processEvents()
    path = output / 'touchpad-smoke.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report.get('ok') else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'artifacts'))
    raise SystemExit(run(parser.parse_args().output))
