import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, Signal, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from airpad.ui import MainWindow


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


class FakeController(QObject):
    updated = Signal(dict)
    preview = Signal(object)
    problem = Signal(str)

    def __init__(self, tmp_path: Path):
        super().__init__()
        self.profile = SimpleNamespace(
            sensitivity=1000.0,
            smoothing=1.5,
            scroll_speed=480.0,
            pinch_close=0.42,
            pinch_release=0.62,
            pinch_open=0.78,
            dominant_hand="auto",
            camera=0,
            camera_backend="auto",
            zoom_enabled=False,
            three_finger_enabled=False,
            two_finger_scroll=False,
            natural_scroll=True,
            click_drag_travel=.12,
            drag_hold=.18,
        )
        self.data_dir = tmp_path
        self.calls = []
        self.practice = False

    def start_camera(self):
        self.calls.append(("start_camera",))

    def stop_camera(self):
        self.calls.append(("stop_camera",))

    def set_enabled(self, enabled):
        self.calls.append(("set_enabled", enabled))

    def set_practice(self, practice):
        self.calls.append(("set_practice", practice))

    def update_profile(self, values):
        self.calls.append(("update_profile", values))

    def start_calibration(self):
        self.calls.append(("start_calibration",))

    def calibration_start(self):
        self.calls.append(("calibration_start",))

    def calibration_retry(self):
        self.calls.append(("calibration_retry",))

    def cancel_calibration(self):
        self.calls.append(("cancel_calibration",))

    def calibration_next(self):
        self.calls.append(("calibration_next",))

    def set_debug(self, enabled):
        self.calls.append(("set_debug", enabled))

    def shutdown(self):
        self.calls.append(("shutdown",))


@pytest.fixture
def window(app, tmp_path):
    controller = FakeController(tmp_path)
    widget = MainWindow(controller)
    widget.show()
    app.processEvents()
    yield widget, controller
    widget._shutdown_requested = True
    widget.close()
    app.processEvents()


def click(widget, app):
    QTest.mouseClick(widget, Qt.LeftButton)
    app.processEvents()


def test_safe_controls_invoke_controller_and_status_tracks_updates(window, app):
    """Catches enabling on startup or a disconnected camera/enable/pause control."""
    widget, controller = window

    assert not widget.practice_toggle.isChecked(), "control mode is the default"
    assert not widget.tray_actions["practice"].isChecked()
    assert "Paused" in widget.control_status.text()
    assert "local" in widget.privacy_note.text().lower()

    click(widget.camera_button, app)
    assert controller.calls[-1] == ("start_camera",)

    controller.updated.emit(
        {
            "camera_state": "running",
            "hand_state": "tracked",
            "state": "pointer",
            "enabled": True,
            "practice": False,
            "fps": 29.8,
            "inference_ms": 11.2,
            "latency_ms": 24.0,
            "velocity": 146.0,
            "pinch": 0.31,
            "scale": 0.18,
            "quality": 1.0,
            "pointer_dx": 4.0,
            "pointer_dy": -2.0,
            "error": None,
            "calibration_step": 0,
            "calibration_text": "",
        }
    )
    app.processEvents()
    assert "Stop" in widget.camera_button.text()
    assert "active" in widget.control_status.text().lower()
    assert "29.8" in widget.fps_value.text()
    assert widget.quality_value.text() == "Valid"
    assert "READ → CONTROL" in {label.text() for label in widget.findChildren(QLabel)}

    click(widget.pause_button, app)
    assert controller.calls[-1] == ("set_enabled", False)
    controller.updated.emit({"enabled": False})
    app.processEvents()
    click(widget.enable_button, app)
    assert controller.calls[-1] == ("set_enabled", True)


def test_control_status_says_what_it_is_waiting_for(window, app):
    """Control is armed as soon as the app starts, so "Active" with no hand in view reads
    as "this thing is broken". The status must say what it is actually waiting for."""
    widget, controller = window

    controller.updated.emit({"camera_state": "running", "enabled": True, "quality": 0.0,
                             "hand_state": "No hand detected", "practice": False})
    app.processEvents()
    assert "waiting" in widget.control_status.text().lower(), widget.control_status.text()

    controller.updated.emit({"camera_state": "running", "enabled": True, "quality": 1.0,
                             "hand_state": "Hand detected", "practice": False})
    app.processEvents()
    assert "active" in widget.control_status.text().lower()

    controller.updated.emit({"camera_state": "off", "enabled": True, "quality": 0.0,
                             "hand_state": "No hand detected", "practice": False})
    app.processEvents()
    assert "camera" in widget.control_status.text().lower()

    controller.updated.emit({"camera_state": "off", "enabled": False, "quality": 0.0,
                             "hand_state": "No hand detected", "practice": False})
    app.processEvents()
    assert "paused" in widget.control_status.text().lower()


def test_practice_canvas_uses_pointer_and_reports_target_feedback(window, app):
    """Catches practice updates being ignored or target clicks lacking feedback."""
    widget, controller = window
    canvas = widget.practice_canvas
    canvas.resize(620, 320)
    app.processEvents()
    target = canvas.target_rect()
    x = target.center().x() / canvas.width()
    y = target.center().y() / canvas.height()

    controller.updated.emit(
        {
            "practice": True,
            "practice_x": x,
            "practice_y": y,
            "click_count": 1,
            "dragging": False,
        }
    )
    app.processEvents()

    assert canvas.pointer_position == pytest.approx((x, y), abs=0.01)
    assert canvas.success_count == 1
    assert "hit" in canvas.feedback.lower()

    controller.updated.emit({"dragging": True, "practice_x": 0.8, "practice_y": 0.7})
    app.processEvents()
    assert canvas.dragging is True
    assert "drag" in canvas.feedback.lower()

    controller.updated.emit({"dragging": False})
    app.processEvents()
    assert "released" in canvas.feedback.lower()


def test_idle_practice_updates_preserve_the_getting_started_instruction(window, app):
    """Catches a routine non-dragging update masquerading as a completed drag."""
    widget, controller = window

    controller.updated.emit(
        {"practice": True, "practice_x": 0.3, "practice_y": 0.6, "click_count": 0, "dragging": False}
    )
    app.processEvents()

    assert "move your hand" in widget.practice_canvas.feedback.lower()


def test_touchpad_preset_is_explicit_and_changes_only_gesture_modes(window, app):
    widget, controller = window
    assert widget.touchpad_button.text() == '启用触控板手势'
    assert '未启用' in widget.gesture_help.text()
    click(widget.touchpad_button, app)
    assert controller.calls[-1] == ('update_profile', {
        'two_finger_scroll': True, 'zoom_enabled': False, 'three_finger_enabled': False})
    controller.updated.emit({'enabled': False, 'two_finger_scroll': True, 'drag_hold': .18})
    assert '已启用' in widget.gesture_help.text()
    assert 'Enable' in widget.gesture_help.text()
    assert '180' in widget.gesture_help.text()
    assert '右键' in widget.gesture_help.text()


def test_practice_right_click_menu_and_scroll_change_rendered_canvas(window, app):
    widget, controller = window
    canvas = widget.practice_canvas
    before = canvas.grab().toImage()
    controller.updated.emit({'right_click_count': 1})
    app.processEvents()
    assert canvas.context_menu_visible and canvas.right_click_count == 1
    assert '右键' in canvas.feedback
    menu_image = canvas.grab().toImage()
    assert menu_image != before
    controller.updated.emit({'practice_scroll_x': 25, 'practice_scroll_y': -53, 'scroll_count': 1})
    app.processEvents()
    assert canvas.scroll_offset == (25, -53) and canvas.scroll_count == 1
    assert not canvas.context_menu_visible
    assert canvas.grab().toImage() != menu_image
    controller.updated.emit({'right_click_count': 2})
    assert canvas.context_menu_visible
    controller.updated.emit({'click_count': 1})
    assert not canvas.context_menu_visible


def test_settings_expose_drag_controls_without_recognition_values(window, app):
    widget, controller = window
    click(widget.settings_button, app)
    dialog = widget.settings_dialog
    assert dialog.drag_hold.value() == pytest.approx(.18)
    assert dialog.click_drag_travel.value() == pytest.approx(.12)
    dialog.drag_hold.setValue(.25)
    dialog.click_drag_travel.setValue(.2)
    click(dialog.apply_button, app)
    _, changes = controller.calls[-1]
    assert changes['drag_hold'] == .25 and changes['click_drag_travel'] == .2
    assert not any(key.startswith('pinch') or key.startswith('finger') for key in changes)


def test_practice_target_can_be_repositioned_with_the_mouse(window, app):
    """Catches a static practice target that cannot be placed for a useful drill."""
    widget, _controller = window
    canvas = widget.practice_canvas
    canvas.resize(620, 320)
    app.processEvents()
    start = canvas.target_rect().center().toPoint()
    destination = QPoint(180, 210)

    QTest.mousePress(canvas, Qt.LeftButton, pos=start)
    QTest.mouseMove(canvas, destination)
    QTest.mouseRelease(canvas, Qt.LeftButton, pos=destination)
    app.processEvents()

    assert (canvas.target_rect().center() - destination).manhattanLength() < 8


def test_hand_drag_moves_practice_target_and_release_keeps_it_placed(window, app):
    canvas = window[0].practice_canvas
    canvas.resize(620, 320)
    start = canvas._target_position
    canvas.update_state({'practice_x': start[0] + .01, 'practice_y': start[1], 'dragging': True})
    canvas.update_state({'practice_x': .41, 'practice_y': .72, 'dragging': True})
    assert canvas._target_position == pytest.approx((.40, .72)), 'hand drag must move the object'
    canvas.update_state({'dragging': False})
    placed = canvas._target_position
    canvas.update_state({'practice_x': .8, 'practice_y': .2, 'dragging': False})
    assert canvas._target_position == placed, 'release must drop the object'


def test_hand_drag_started_outside_target_does_not_grab_it_later(window, app):
    canvas = window[0].practice_canvas
    initial = canvas._target_position
    canvas.update_state({'practice_x': .1, 'practice_y': .1, 'dragging': True})
    canvas.update_state({'practice_x': initial[0], 'practice_y': initial[1], 'dragging': True})
    canvas.update_state({'practice_x': .4, 'practice_y': .7, 'dragging': True})
    assert canvas._target_position == initial


def test_settings_apply_meaningful_profile_values(window, app):
    """Catches settings controls that look editable but do not update the controller."""
    widget, controller = window
    click(widget.settings_button, app)
    dialog = widget.settings_dialog
    dialog.sensitivity.setValue(1450)
    dialog.smoothing.setValue(2.2)
    dialog.natural_scroll.setChecked(False)
    dialog.zoom_enabled.setChecked(True)
    click(dialog.apply_button, app)

    name, payload = controller.calls[-1]
    assert name == "update_profile"
    assert payload["sensitivity"] == 1450.0
    assert payload["smoothing"] == 2.2
    assert payload["natural_scroll"] is False
    assert payload["zoom_enabled"] is True


def test_calibration_and_debug_disclosure_are_wired(window, app):
    """Catches inert calibration/debug controls or hidden live diagnostics."""
    widget, controller = window
    click(widget.calibrate_button, app)
    assert controller.calls[-1] == ("start_calibration",)
    assert widget.calibration_panel.isVisible()

    controller.updated.emit(
        {"calibration_step": 2, "calibration_text": "Pinch gently, then release.", "calibration_ready": True}
    )
    app.processEvents()
    assert widget.calibration_progress.value() == 2
    assert "Pinch gently" in widget.calibration_text.text()
    click(widget.calibration_next_button, app)
    assert controller.calls[-1] == ("calibration_next",)

    controller.updated.emit(
        {"calibration_step": 5, "calibration_text": "Review the recommended thresholds."}
    )
    app.processEvents()
    assert widget.calibration_next_button.text() == "Save profile"

    controller.updated.emit(
        {"calibration_step": 6, "calibration_text": "Calibration saved."}
    )
    app.processEvents()
    assert widget.calibration_next_button.text() == "Done"
    calls_before_done = list(controller.calls)
    click(widget.calibration_next_button, app)
    assert not widget.calibration_panel.isVisible()
    assert controller.calls == calls_before_done
    controller.updated.emit(
        {"calibration_step": 6, "calibration_text": "Calibration saved."}
    )
    app.processEvents()
    assert not widget.calibration_panel.isVisible()

    widget.debug_toggle.setChecked(True)
    app.processEvents()
    assert controller.calls[-1] == ("set_debug", True)
    assert widget.debug_panel.isVisible()
    controller.updated.emit({"velocity": 72.5, "pinch": 0.22, "pointer_dx": 3, "pointer_dy": -4})
    app.processEvents()
    assert widget.debug_values["velocity"].text() == "72.5 palm/s"
    assert "3.0" in widget.debug_values["pointer"].text()


def test_preview_problem_tray_and_close_cleanup(window, app):
    """Catches missing preview/error handling, dead tray actions, or duplicate shutdown."""
    widget, controller = window
    frame = __import__("numpy").zeros((60, 90, 3), dtype="uint8")
    frame[:, :, 1] = 170
    controller.preview.emit(frame)
    controller.problem.emit("Camera permission denied")
    app.processEvents()
    assert widget.preview_widget.has_frame
    assert widget.problem_banner.isVisible()
    assert "permission" in widget.problem_banner_text.text().lower()

    click(widget.problem_dismiss_button, app)
    controller.problem.emit("Camera permission denied")
    app.processEvents()
    assert not widget.problem_banner.isVisible()
    controller.problem.emit("Camera disconnected")
    app.processEvents()
    assert widget.problem_banner.isVisible()

    # The tray action mirrors the controller, which starts in desktop control mode.
    assert not widget.tray_actions["practice"].isChecked()
    widget.tray_actions["practice"].trigger()
    assert controller.calls[-1] == ("set_practice", True)
    widget.tray_actions["practice"].trigger()
    assert controller.calls[-1] == ("set_practice", False)
    widget.tray_actions["calibrate"].trigger()
    assert controller.calls[-1] == ("start_calibration",)

    widget.tray_actions["exit"].trigger()
    app.processEvents()
    assert not widget.isVisible()
    assert [call for call in controller.calls if call[0] == "shutdown"] == [("shutdown",)]


def test_window_uses_a_registered_font_and_balances_workspace_at_minimum_size(window, app):
    """Catches unreadable tofu text or a practice canvas crushed by preview sizing."""
    widget, _controller = window
    widget.resize(widget.minimumSize())
    app.processEvents()

    assert widget.font().family() in QFontDatabase.families()
    assert widget.preview_widget.width() >= 230
    assert widget.practice_canvas.width() >= 230


def test_calibration_collection_retry_and_cancel_controls(window, app):
    widget, controller=window
    click(widget.calibrate_button,app)
    controller.updated.emit({'calibration_active':True,'calibration_step':0,
                             'calibration_count':0,'calibration_target':45,
                             'calibration_ready':False,'calibration_collecting':False})
    assert not widget.calibration_next_button.isEnabled()
    assert not widget.enable_button.isEnabled()
    click(widget.calibration_start_button,app)
    assert controller.calls[-1]==('calibration_start',)
    controller.updated.emit({'calibration_count':45,'calibration_target':45,
                             'calibration_ready':True,'calibration_collecting':False})
    assert widget.calibration_next_button.isEnabled()
    assert widget.calibration_sample_progress.value()==45
    click(widget.calibration_retry_button,app)
    assert controller.calls[-1]==('calibration_retry',)
    click(widget.calibration_cancel_button,app)
    assert controller.calls[-1]==('cancel_calibration',)
    controller.updated.emit({'calibration_active':False,'calibration_step':0,
                             'calibration_text':'','enabled':False})
    assert not widget.calibration_panel.isVisible()
    assert widget.enable_button.isEnabled()
