import json
import sys
from pathlib import Path

from airpad import resources
from airpad.__main__ import main


def test_frozen_data_defaults_to_per_user_location(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / '用户资料'))
    monkeypatch.delenv('AIRPAD_DATA_DIR', raising=False)
    assert resources.data_path() == tmp_path / '用户资料' / 'AIRPAD'
    monkeypatch.setenv('AIRPAD_DATA_DIR', str(tmp_path / '环境'))
    assert resources.data_path() == tmp_path / '环境'
    assert resources.data_path(str(tmp_path / '指定')) == tmp_path / '指定'


def test_doctor_exports_report_without_stdout(tmp_path, monkeypatch):
    target = tmp_path / '中文目录' / 'report.json'
    monkeypatch.setattr(sys, 'stdout', None)
    assert main(['doctor', '--output', str(target)]) == 0
    assert json.loads(target.read_text(encoding='utf-8'))['model_ok'] is True


def test_self_check_runs_offline_in_spawned_workers(tmp_path):
    import subprocess
    target = tmp_path / '离线检查.json'
    result = subprocess.run([sys.executable, '-m', 'airpad', 'self-check', '--output', str(target)],
                            capture_output=True, timeout=90)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    report = json.loads(target.read_text(encoding='utf-8'))
    assert report['ok'] is True
    assert report['camera_opened'] is False and report['os_input'] is False
    assert report['checks']['model']['hands_detected'] == 0
    assert report['checks']['broker']['processed_seq'] >= 1
    assert report['checks']['broker']['dry_run'] is True
    assert report['checks']['gui']['exit_code'] == 0


def test_practice_launch_enables_local_canvas_without_broker(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from airpad.runtime import AirpadController
    from airpad import __main__ as cli
    from types import SimpleNamespace
    app = QApplication.instance() or QApplication([])
    class Camera:
        def __init__(self, **kwargs): pass
        def start(self): pass
        def close(self): pass
    def no_broker(**kwargs):
        raise AssertionError('Startup must not create an OS input broker')
    controller = AirpadController(tmp_path, vision_factory=Camera, broker_factory=no_broker)
    try:
        cli.start_session(controller, SimpleNamespace(practice=True, calibrate=False))
        assert controller.practice and controller.enabled
        assert controller.broker is None and controller.vision is not None
        assert not controller.error
    finally:
        controller.shutdown()


def test_calibration_launch_never_enables_desktop(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from airpad.runtime import AirpadController
    from airpad import __main__ as cli
    from types import SimpleNamespace
    app = QApplication.instance() or QApplication([])
    class Camera:
        def __init__(self, **kwargs): pass
        def start(self): pass
        def close(self): pass
    def no_broker(**kwargs):
        raise AssertionError('Calibration must not create an OS input broker')
    controller = AirpadController(tmp_path, vision_factory=Camera, broker_factory=no_broker)
    try:
        cli.start_session(controller, SimpleNamespace(practice=False, calibrate=True))
        assert controller.calibration is not None and not controller.enabled
        assert controller.broker is None
        assert not controller.error
    finally:
        controller.shutdown()
