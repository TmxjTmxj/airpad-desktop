"""Bounded offline diagnostics, explicitly invoked; never camera or OS input."""
import multiprocessing as mp
import os
import tempfile
import time
from pathlib import Path


def _probe(kind, result):
    try:
        if kind == 'model':
            import mediapipe as mediapipe
            import numpy as np
            from .resources import model_path, verify_model, MODEL_SHA256
            from .vision import _create_tracker
            path = model_path()
            if not verify_model(path):
                raise ValueError('Pinned model hash mismatch or model missing')
            with _create_tracker(path) as tracker:
                image = mediapipe.Image(image_format=mediapipe.ImageFormat.SRGB,
                                        data=np.zeros((480, 640, 3), dtype=np.uint8))
                detected = tracker.detect_for_video(image, 1)
            result.send({'ok': True, 'sha256': MODEL_SHA256,
                         'hands_detected': len(detected.hand_landmarks), 'frames': 1})
        elif kind == 'gui':
            os.environ['QT_QPA_PLATFORM'] = 'offscreen'
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication
            from .runtime import AirpadController
            from .ui import MainWindow
            # Factories fail closed even if a future UI change tries to autostart.
            attempts = []
            def forbidden(**kwargs):
                attempts.append(kwargs)
                raise RuntimeError('Device access is forbidden during offline self-check')
            app = QApplication([])
            app.setApplicationName('AIRPAD self-check')
            with tempfile.TemporaryDirectory(prefix='airpad-self-check-') as directory:
                controller = AirpadController(Path(directory), vision_factory=forbidden,
                                              broker_factory=forbidden)
                window = MainWindow(controller)
                window.show()
                QTimer.singleShot(700, app.quit)
                try:
                    code = app.exec()
                    idle = controller.vision is None and controller.broker is None
                finally:
                    controller.shutdown()
                    window.close()
                result.send({'ok': code == 0 and idle and not attempts, 'exit_code': code,
                             'device_attempts': len(attempts), 'platform': 'offscreen'})
        else:
            raise ValueError('Unknown diagnostic')
    except Exception as exc:
        result.send({'ok': False, 'error': f'{type(exc).__name__}: {exc}'})
    finally:
        result.close()


def _spawn_probe(kind, timeout=45):
    context = mp.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_probe, args=(kind, sender), name=f'AIRPAD check {kind}')
    try:
        process.start()
        sender.close()
        if not receiver.poll(timeout):
            return {'ok': False, 'error': f'{kind} timed out after {timeout} seconds'}
        try:
            report = receiver.recv()
        except EOFError:
            return {'ok': False, 'error': f'{kind} worker exited without a report'}
        process.join(5)
        report['worker_exit_code'] = process.exitcode
        report['ok'] = report['ok'] and process.exitcode == 0
        return report
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(3)
            process.close()
        receiver.close()
        sender.close()


def _broker_probe():
    from .broker import BrokerClient
    from .types import Action
    broker = BrokerClient(dry_run=True)
    try:
        if not broker.enable():
            raise RuntimeError(str(broker.poll()))
        if not broker.submit([Action('move', x=.5, y=.5)], time.monotonic()):
            raise RuntimeError('Dry-run broker rejected fresh synthetic observation')
        deadline = time.monotonic() + 2
        state = broker.poll()
        while state['processed_seq'] < 1 and time.monotonic() < deadline:
            time.sleep(.005)
            state = broker.poll()
        return {'ok': state['processed_seq'] >= 1 and not state['error'],
                'dry_run': state['dry_run'], 'processed_seq': state['processed_seq'],
                'error': state['error']}
    except Exception as exc:
        return {'ok': False, 'dry_run': True, 'error': f'{type(exc).__name__}: {exc}'}
    finally:
        broker.close()


def self_check():
    checks = {'model': _spawn_probe('model'), 'broker': _broker_probe(),
              'gui': _spawn_probe('gui')}
    return {'schema_version': 1, 'ok': all(item['ok'] for item in checks.values()),
            'kind': 'offline synthetic installation check; not real gesture accuracy',
            'camera_opened': False, 'os_input': False, 'checks': checks}
