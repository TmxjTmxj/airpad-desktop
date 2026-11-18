"""PySide6 desktop interface for AIRPAD.

The module intentionally depends only on the controller contract. Camera and
input implementations live behind that boundary.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QSignalBlocker, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCloseEvent,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .style import (
    APP_STYLESHEET,
    DANGER,
    GRAPHITE_700,
    GRAPHITE_800,
    GRAPHITE_900,
    MINT,
    MINT_SOFT,
    TEXT,
    TEXT_MUTED,
)


def _label(text: str, role: str | None = None, *, word_wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if role:
        label.setObjectName(role)
    label.setWordWrap(word_wrap)
    return label


def _ensure_ui_font() -> None:
    """Register the Windows UI font when Qt's offscreen backend finds none."""
    if "Segoe UI" in QFontDatabase.families():
        return
    fonts_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "NotoSansSC-VF.ttf"):
        path = fonts_dir / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))


def _airpad_icon(size: int = 64) -> QIcon:
    """Create the app mark with Qt vector painting; no asset file is needed."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, size, size, 0)
    gradient.setColorAt(0.0, QColor("#42C999"))
    gradient.setColorAt(1.0, QColor(MINT_SOFT))
    painter.setBrush(gradient)
    painter.setPen(Qt.NoPen)
    path = QPainterPath()
    path.moveTo(size * 0.16, size * 0.75)
    path.cubicTo(size * 0.26, size * 0.44, size * 0.32, size * 0.22, size * 0.50, size * 0.16)
    path.cubicTo(size * 0.68, size * 0.22, size * 0.75, size * 0.45, size * 0.84, size * 0.75)
    path.lineTo(size * 0.66, size * 0.75)
    path.lineTo(size * 0.58, size * 0.54)
    path.lineTo(size * 0.38, size * 0.54)
    path.lineTo(size * 0.31, size * 0.75)
    path.closeSubpath()
    painter.drawPath(path)
    painter.setBrush(QColor(GRAPHITE_900))
    painter.drawEllipse(QPointF(size * 0.48, size * 0.38), size * 0.08, size * 0.08)
    painter.end()
    return QIcon(pixmap)


class CameraPreview(QWidget):
    """Displays the controller's already-overlaid BGR preview frame."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._image: QImage | None = None
        self.setMinimumSize(230, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    @property
    def has_frame(self) -> bool:
        return self._image is not None

    def set_frame(self, frame: object) -> None:
        try:
            height, width = frame.shape[:2]  # type: ignore[attr-defined]
            stride = int(frame.strides[0])  # type: ignore[attr-defined]
            image = QImage(frame.data, width, height, stride, QImage.Format_BGR888)  # type: ignore[attr-defined]
            self._image = image.copy()
        except (AttributeError, TypeError, ValueError):
            self._image = None
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(GRAPHITE_700), 1))
        painter.setBrush(QColor("#090E0D"))
        painter.drawRoundedRect(bounds, 16, 16)
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 16, 16)
        painter.setClipPath(clip)
        if self._image is not None:
            scaled = self._image.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            x = (scaled.width() - self.width()) // 2
            y = (scaled.height() - self.height()) // 2
            painter.drawImage(self.rect(), scaled, scaled.rect().adjusted(x, y, -x, -y))
            painter.fillRect(self.rect(), QColor(5, 14, 11, 32))
        else:
            self._paint_placeholder(painter)

    def _paint_placeholder(self, painter: QPainter) -> None:
        center = QPointF(self.width() / 2, self.height() / 2 - 14)
        painter.setPen(QPen(QColor("#31413B"), 2))
        painter.setBrush(QColor("#17221E"))
        painter.drawRoundedRect(QRectF(center.x() - 42, center.y() - 30, 84, 60), 13, 13)
        painter.setBrush(QColor(MINT))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(center, 11, 11)
        painter.setPen(QColor(TEXT_MUTED))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(
            QRectF(0, center.y() + 48, self.width(), 24),
            Qt.AlignCenter,
            "Start the camera to see your hand tracking",
        )


class PracticeCanvas(QWidget):
    """Local-only pointer canvas with visible click and drag feedback."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.pointer_position = (0.26, 0.58)
        self.dragging = False
        self.feedback = "Move your hand to the target, then pinch"
        self.success_count = 0
        self._last_click_count = 0
        self._flash = 0
        self._target_position = (0.68, 0.42)
        self._moving_target = False
        self._gesture_target_offset = None
        self.right_click_count = 0
        self.scroll_count = 0
        self.scroll_offset = (0., 0.)
        self.context_menu_visible = False
        self._context_menu_position = self.pointer_position
        self.setMinimumSize(230, 250)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def target_rect(self) -> QRectF:
        radius = max(25.0, min(self.width(), self.height()) * 0.075)
        center = QPointF(
            self.width() * self._target_position[0],
            self.height() * self._target_position[1],
        )
        return QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self.target_rect().contains(event.position()):
            self._moving_target = True
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._moving_target:
            margin = max(28.0, min(self.width(), self.height()) * 0.08)
            x = min(self.width() - margin, max(margin, event.position().x()))
            y = min(self.height() - margin, max(margin, event.position().y()))
            self._target_position = (x / max(1, self.width()), y / max(1, self.height()))
            self.feedback = "Target moved — release to place it"
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._moving_target and event.button() == Qt.LeftButton:
            self.mouseMoveEvent(event)
            self._moving_target = False
            self.unsetCursor()
            self.feedback = "Target ready — point and pinch"
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def update_state(self, payload: dict[str, Any]) -> None:
        x = float(payload.get("practice_x", self.pointer_position[0]))
        y = float(payload.get("practice_y", self.pointer_position[1]))
        self.pointer_position = (min(1.0, max(0.0, x)), min(1.0, max(0.0, y)))
        was_dragging = self.dragging
        self.dragging = bool(payload.get("dragging", self.dragging))
        if self.dragging and not was_dragging:
            point = QPointF(self.pointer_position[0] * self.width(), self.pointer_position[1] * self.height())
            if self.target_rect().contains(point):
                self._gesture_target_offset = tuple(
                    self._target_position[j] - self.pointer_position[j] for j in range(2))
        if self.dragging and self._gesture_target_offset is not None:
            self._target_position = tuple(max(.08, min(.92,
                self.pointer_position[j] + self._gesture_target_offset[j])) for j in range(2))
        elif not self.dragging:
            self._gesture_target_offset = None
        click_count = int(payload.get("click_count", self._last_click_count))
        if click_count > self._last_click_count:
            point = QPointF(self.pointer_position[0] * self.width(), self.pointer_position[1] * self.height())
            if self.target_rect().contains(point):
                self.success_count += click_count - self._last_click_count
                self.feedback = "Target hit — clean click"
                self._flash = 1
            else:
                self.feedback = "Click seen — move closer to the target"
        elif self.dragging:
            self.feedback = "Dragging — open your hand to release"
        elif was_dragging and not self.dragging and payload.get("dragging") is False:
            self.feedback = "Released safely"
        right_count = int(payload.get('right_click_count', self.right_click_count))
        scroll_count = int(payload.get('scroll_count', self.scroll_count))
        offset = (float(payload.get('practice_scroll_x', self.scroll_offset[0])),
                  float(payload.get('practice_scroll_y', self.scroll_offset[1])))
        if right_count > self.right_click_count:
            self.context_menu_visible = True
            self._context_menu_position = self.pointer_position
            self.feedback = '右键已触发 · 练习菜单已打开'
        elif offset != self.scroll_offset:
            self.context_menu_visible = False
            self.feedback = '滚动中 · 网格跟随两指移动'
        elif click_count > self._last_click_count or self.dragging:
            self.context_menu_visible = False
        self.right_click_count = right_count
        self._last_click_count = max(click_count, self._last_click_count)
        self.scroll_count = scroll_count
        self.scroll_offset = offset
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor(GRAPHITE_700), 1))
        painter.setBrush(QColor("#101815"))
        painter.drawRoundedRect(bounds, 16, 16)

        painter.setPen(QPen(QColor(39, 55, 49, 115), 1))
        step = 34
        for x in range(int(self.scroll_offset[0]) % step, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(int(self.scroll_offset[1]) % step, self.height(), step):
            painter.drawLine(0, y, self.width(), y)

        target = self.target_rect()
        painter.setPen(QPen(QColor(MINT), 2))
        painter.setBrush(QColor(116, 242, 193, 35 if not self._flash else 75))
        painter.drawEllipse(target)
        inner = target.adjusted(target.width() * 0.3, target.height() * 0.3,
                                -target.width() * 0.3, -target.height() * 0.3)
        painter.drawEllipse(inner)
        self._flash = 0

        px = self.pointer_position[0] * self.width()
        py = self.pointer_position[1] * self.height()
        painter.setPen(QPen(QColor(116, 242, 193, 70), 2))
        painter.setBrush(QColor(116, 242, 193, 35))
        painter.drawEllipse(QPointF(px, py), 19 if self.dragging else 15, 19 if self.dragging else 15)
        painter.setBrush(QColor(MINT))
        painter.setPen(QPen(QColor("#07130F"), 2))
        painter.drawEllipse(QPointF(px, py), 7, 7)

        if self.context_menu_visible:
            menu_width = min(188, self.width() - 24)
            mx = min(self.width() - menu_width - 12, max(12, self._context_menu_position[0] * self.width()))
            my = min(self.height() - 130, max(46, self._context_menu_position[1] * self.height()))
            menu = QRectF(mx, my, menu_width, 84)
            painter.setBrush(QColor(GRAPHITE_800))
            painter.setPen(QPen(QColor(MINT), 1))
            painter.drawRoundedRect(menu, 9, 9)
            painter.setPen(QColor(TEXT))
            painter.drawText(menu.adjusted(12, 8, -8, -42), Qt.AlignVCenter, '右键菜单 · 练习')
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(menu.adjusted(12, 40, -8, -8), Qt.AlignVCenter, '左键点击或滚动关闭')

        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(QRectF(16, self.height() - 32, self.width() - 32, 24),
                         Qt.AlignLeft | Qt.AlignVCenter,
                         f'左键 {self._last_click_count}  右键 {self.right_click_count}  滚动 {self.scroll_count}'
                         f'  X {self.scroll_offset[0]:.0f} / Y {self.scroll_offset[1]:.0f}')

        painter.setPen(QColor(TEXT_MUTED))
        feedback_rect = QRectF(20, 8, max(0, self.width() - 132), 30)
        feedback = painter.fontMetrics().elidedText(
            self.feedback, Qt.ElideRight, max(0, int(feedback_rect.width()))
        )
        painter.drawText(feedback_rect, Qt.AlignLeft | Qt.AlignVCenter, feedback)
        painter.setPen(QColor(MINT_SOFT))
        painter.drawText(
            QRectF(max(0, self.width() - 108), 8, 88, 30),
            Qt.AlignRight | Qt.AlignVCenter,
            f"HITS  {self.success_count:02d}",
        )


class SettingsDialog(QDialog):
    def __init__(self, controller, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("AIRPAD Settings")
        self.setModal(False)
        self.setMinimumWidth(470)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)
        root.addWidget(_label("SETTINGS", "eyebrow"))
        root.addWidget(_label("Tune movement and gestures", "pageTitle"))
        intro = _label(
            "Changes are applied through the controller and saved to your local profile.",
            "muted",
            word_wrap=True,
        )
        root.addWidget(intro)

        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        self.sensitivity = QDoubleSpinBox()
        self.sensitivity.setRange(200, 3000)
        self.sensitivity.setSingleStep(50)
        self.sensitivity.setSuffix("  gain")
        self.smoothing = QDoubleSpinBox()
        self.smoothing.setRange(0.1, 8.0)
        self.smoothing.setSingleStep(0.1)
        self.smoothing.setDecimals(1)
        self.smoothing.setSuffix("  Hz")
        self.scroll_speed = QDoubleSpinBox()
        self.scroll_speed.setRange(60, 1600)
        self.scroll_speed.setSingleStep(20)
        self.scroll_speed.setSuffix("  units")
        self.click_drag_travel = QDoubleSpinBox()
        self.click_drag_travel.setRange(.02, .6)
        self.click_drag_travel.setSingleStep(.01)
        self.click_drag_travel.setSuffix('  掌宽')
        self.drag_hold = QDoubleSpinBox()
        self.drag_hold.setRange(.18, 1.)
        self.drag_hold.setSingleStep(.01)
        self.drag_hold.setSuffix('  秒')
        self.camera = QSpinBox()
        self.camera.setRange(0, 12)
        self.camera_backend = QComboBox()
        self.camera_backend.addItems(["auto", "msmf", "dshow"])
        self.dominant_hand = QComboBox()
        self.dominant_hand.addItems(["auto", "right", "left"])
        form.addRow("Pointer sensitivity", self.sensitivity)
        form.addRow("Motion smoothing", self.smoothing)
        form.addRow("Scroll speed", self.scroll_speed)
        form.addRow('拖拽所需移掌距离', self.click_drag_travel)
        form.addRow('拖拽最短捏住时间', self.drag_hold)
        form.addRow("Camera", self.camera)
        form.addRow("Camera backend", self.camera_backend)
        form.addRow("Dominant hand", self.dominant_hand)
        root.addLayout(form)

        self.natural_scroll = QCheckBox("Natural scroll direction")
        self.two_finger_scroll = QCheckBox('两指滚动与右键')
        self.two_finger_scroll.setToolTip(
            "Off by default: a pointing hand with a relaxed middle finger looks identical to\n"
            "a two-finger scroll, so enabling this can freeze the pointer.")
        self.zoom_enabled = QCheckBox("Two-finger zoom  ·  experimental")
        self.three_finger_enabled = QCheckBox("Three-finger navigation  ·  experimental")
        root.addWidget(self.natural_scroll)
        root.addWidget(self.two_finger_scroll)
        root.addWidget(self.zoom_enabled)
        root.addWidget(self.three_finger_enabled)

        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.hide)
        self.apply_button = QPushButton("Apply settings")
        self.apply_button.setObjectName("primaryButton")
        self.apply_button.clicked.connect(self._apply)
        footer.addWidget(cancel)
        footer.addWidget(self.apply_button)
        root.addLayout(footer)
        self.refresh()

    def refresh(self) -> None:
        profile = self.controller.profile
        self.sensitivity.setValue(float(profile.sensitivity))
        self.smoothing.setValue(float(profile.smoothing))
        self.scroll_speed.setValue(float(profile.scroll_speed))
        self.click_drag_travel.setValue(float(profile.click_drag_travel))
        self.drag_hold.setValue(float(profile.drag_hold))
        self.camera.setValue(int(profile.camera))
        self.camera_backend.setCurrentText(str(profile.camera_backend))
        self.dominant_hand.setCurrentText(str(profile.dominant_hand))
        self.natural_scroll.setChecked(bool(profile.natural_scroll))
        self.two_finger_scroll.setChecked(bool(profile.two_finger_scroll))
        self.zoom_enabled.setChecked(bool(profile.zoom_enabled))
        self.three_finger_enabled.setChecked(bool(profile.three_finger_enabled))

    def _apply(self) -> None:
        self.controller.update_profile(
            {
                "sensitivity": self.sensitivity.value(),
                "smoothing": self.smoothing.value(),
                "scroll_speed": self.scroll_speed.value(),
                'click_drag_travel': self.click_drag_travel.value(),
                'drag_hold': self.drag_hold.value(),
                "camera": self.camera.value(),
                "camera_backend": self.camera_backend.currentText(),
                "dominant_hand": self.dominant_hand.currentText(),
                "natural_scroll": self.natural_scroll.isChecked(),
                "two_finger_scroll": self.two_finger_scroll.isChecked(),
                "zoom_enabled": self.zoom_enabled.isChecked(),
                "three_finger_enabled": self.three_finger_enabled.isChecked(),
            }
        )
        self.hide()


class MainWindow(QMainWindow):
    """AIRPAD's controller-only desktop window."""

    def __init__(self, controller):
        super().__init__()
        _ensure_ui_font()
        self.controller = controller
        self._camera_running = False
        self._enabled = False
        self._quality = False
        self._shutdown_requested = False
        self._calibration_step = 0
        self._calibration_completion_dismissed = False
        self._dismissed_problem: str | None = None
        self._latest: dict[str, Any] = {}
        self.settings_dialog: SettingsDialog | None = None

        self.setWindowTitle("AIRPAD · Air Gesture Desktop Control")
        self.setWindowIcon(_airpad_icon())
        self.resize(1180, 760)
        self.setMinimumSize(940, 650)
        self.setStyleSheet(APP_STYLESHEET)

        self._build_ui()
        self._build_tray()
        controller.updated.connect(self._apply_update)
        controller.preview.connect(self.preview_widget.set_frame)
        controller.problem.connect(self._show_problem)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(22, 18, 22, 22)
        outer.setSpacing(16)

        outer.addLayout(self._header())
        self.problem_banner = QFrame()
        self.problem_banner.setObjectName("problemBanner")
        problem_layout = QHBoxLayout(self.problem_banner)
        problem_layout.setContentsMargins(14, 9, 14, 9)
        self.problem_icon = _label("!")
        self.problem_icon.setStyleSheet(f"color: {DANGER}; font-weight: 700;")
        self.problem_banner_text = _label("", word_wrap=True)
        self.problem_dismiss_button = QToolButton()
        self.problem_dismiss_button.setText("Dismiss")
        self.problem_dismiss_button.clicked.connect(self._dismiss_problem)
        problem_layout.addWidget(self.problem_icon)
        problem_layout.addWidget(self.problem_banner_text, 1)
        problem_layout.addWidget(self.problem_dismiss_button)
        self.problem_banner.hide()
        outer.addWidget(self.problem_banner)

        content = QHBoxLayout()
        content.setSpacing(16)
        content.addWidget(self._sidebar(), 0)
        content.addWidget(self._workspace(), 1)
        outer.addLayout(content, 1)

    def _header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(_airpad_icon(46).pixmap(40, 40))
        mark.setFixedSize(44, 44)
        brand_stack = QVBoxLayout()
        brand_stack.setSpacing(0)
        brand_stack.addWidget(_label("AIRPAD", "brand"))
        brand_stack.addWidget(_label("Air gesture desktop control", "muted"))
        row.addWidget(mark)
        row.addLayout(brand_stack)
        row.addStretch()
        self.camera_status = _label("●  Camera off", "statusPill")
        self.camera_status.setProperty("status", "paused")
        row.addWidget(self.camera_status)
        self.settings_button = QToolButton()
        self.settings_button.setText("Settings")
        self.settings_button.clicked.connect(self._open_settings)
        row.addWidget(self.settings_button)
        return row

    def _sidebar(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sidebar")
        frame.setFixedWidth(306)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addWidget(_label("CONTROL", "eyebrow"))

        state_row = QHBoxLayout()
        self.control_status = _label("●  Paused", "statusPill")
        self.control_status.setProperty("status", "paused")
        state_row.addWidget(self.control_status)
        state_row.addStretch()
        self.hand_status = _label("No hand", "muted")
        state_row.addWidget(self.hand_status)
        layout.addLayout(state_row)

        self.camera_button = QPushButton("Start camera")
        self.camera_button.clicked.connect(self._toggle_camera)
        layout.addWidget(self.camera_button)

        control_row = QHBoxLayout()
        self.enable_button = QPushButton("Enable control")
        self.enable_button.setObjectName("primaryButton")
        self.enable_button.clicked.connect(lambda: self.controller.set_enabled(True))
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(lambda: self.controller.set_enabled(False))
        self.pause_button.setEnabled(False)
        control_row.addWidget(self.enable_button, 2)
        control_row.addWidget(self.pause_button, 1)
        layout.addLayout(control_row)

        practice_card = QFrame()
        practice_card.setObjectName("metricCard")
        practice_layout = QVBoxLayout(practice_card)
        practice_layout.setContentsMargins(13, 11, 13, 11)
        self.practice_toggle = QCheckBox("Practice mode")
        # The controller owns the default (desktop control); the UI only mirrors it.
        self.practice_toggle.setChecked(bool(getattr(self.controller, "practice", False)))
        self.practice_toggle.toggled.connect(self._set_practice)
        practice_layout.addWidget(self.practice_toggle)
        practice_layout.addWidget(
            _label("Pointer feedback stays inside this window.", "muted", word_wrap=True)
        )
        layout.addWidget(practice_card)

        self.touchpad_button = QPushButton('启用触控板手势')
        self.touchpad_button.clicked.connect(self._enable_touchpad_gestures)
        layout.addWidget(self.touchpad_button)
        self.gesture_help = _label('', 'muted', word_wrap=True)
        layout.addWidget(self.gesture_help)
        self._update_gesture_help()

        layout.addWidget(_label("QUICK SETUP", "eyebrow"))
        self.calibrate_button = QPushButton("Guide me through calibration")
        self.calibrate_button.clicked.connect(self._start_calibration)
        layout.addWidget(self.calibrate_button)

        self.calibration_panel = QFrame()
        self.calibration_panel.setObjectName("calibrationPanel")
        cal = QVBoxLayout(self.calibration_panel)
        cal.setContentsMargins(13, 12, 13, 12)
        self.calibration_heading = _label("Calibration · 1/5 · Open hand", "sectionTitle")
        self.calibration_text = _label(
            "Hold an open hand comfortably in view.", "muted", word_wrap=True
        )
        self.calibration_progress = QProgressBar()
        self.calibration_progress.setRange(0, 5)
        self.calibration_progress.setValue(0)
        self.calibration_progress.setTextVisible(False)
        self.calibration_next_button = QPushButton("Next")
        self.calibration_next_button.clicked.connect(self._advance_calibration)
        cal.addWidget(self.calibration_heading)
        cal.addWidget(self.calibration_text)
        cal.addWidget(self.calibration_progress)
        self.calibration_sample_progress = QProgressBar()
        self.calibration_sample_progress.setRange(0, 45)
        self.calibration_sample_progress.setFormat("%v / %m frames")
        self.calibration_start_button = QPushButton("Start capture")
        self.calibration_start_button.clicked.connect(self.controller.calibration_start)
        self.calibration_retry_button = QPushButton("Retry phase")
        self.calibration_retry_button.clicked.connect(self.controller.calibration_retry)
        self.calibration_cancel_button = QPushButton("Cancel")
        self.calibration_cancel_button.clicked.connect(self.controller.cancel_calibration)
        cal.addWidget(self.calibration_sample_progress)
        capture_row = QHBoxLayout()
        capture_row.addWidget(self.calibration_start_button)
        capture_row.addWidget(self.calibration_retry_button)
        cal.addLayout(capture_row)
        action_row = QHBoxLayout()
        action_row.addWidget(self.calibration_next_button)
        action_row.addWidget(self.calibration_cancel_button)
        cal.addLayout(action_row)
        self.calibration_panel.hide()
        layout.addWidget(self.calibration_panel)

        layout.addStretch()
        self.debug_toggle = QCheckBox("Show diagnostics")
        self.debug_toggle.toggled.connect(self._set_debug)
        layout.addWidget(self.debug_toggle)
        self.privacy_note = _label(
            "Private by design · camera frames stay local and are not recorded by default.",
            "privacyNote",
            word_wrap=True,
        )
        layout.addWidget(self.privacy_note)
        return frame

    def _workspace(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(_label("LIVE WORKSPACE", "eyebrow"))
        titles.addWidget(_label("Practice before you control", "pageTitle"))
        title_row.addLayout(titles)
        title_row.addStretch()
        self.mode_note = _label("PRACTICE · NO DESKTOP INPUT", "statusPill")
        title_row.addWidget(self.mode_note)
        layout.addLayout(title_row)

        split = QHBoxLayout()
        split.setSpacing(12)
        preview_column = QVBoxLayout()
        preview_column.addWidget(_label("CAMERA PREVIEW", "eyebrow"))
        self.preview_widget = CameraPreview()
        preview_column.addWidget(self.preview_widget, 1)
        split.addLayout(preview_column, 5)
        practice_column = QVBoxLayout()
        practice_column.addWidget(_label("POINTER PRACTICE", "eyebrow"))
        self.practice_canvas = PracticeCanvas()
        practice_column.addWidget(self.practice_canvas, 1)
        split.addLayout(practice_column, 6)
        layout.addLayout(split, 1)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.fps_value = self._metric(metrics, "FRAME RATE", "— fps")
        self.latency_value = self._metric(metrics, "READ → CONTROL", "— ms")
        self.latency_value.setToolTip(
            "Time from camera frame read to controller update; excludes display and desktop input."
        )
        self.quality_value = self._metric(metrics, "TRACKING", "Waiting")
        self.gesture_value = self._metric(metrics, "GESTURE", "Idle")
        layout.addLayout(metrics)

        self.debug_panel = QFrame()
        self.debug_panel.setObjectName("debugPanel")
        debug_layout = QGridLayout(self.debug_panel)
        debug_layout.setContentsMargins(14, 10, 14, 10)
        debug_layout.setHorizontalSpacing(18)
        self.debug_values: dict[str, QLabel] = {}
        debug_specs = [
            ("velocity", "Velocity", "0.0 palm/s"),
            ("pinch", "Pinch ratio", "—"),
            ("scale", "Palm scale", "—"),
            ("pointer", "Delta", "0.0, 0.0"),
            ("inference", "Inference", "— ms"),
        ]
        for column, (key, caption, value) in enumerate(debug_specs):
            box = QVBoxLayout()
            box.setSpacing(1)
            box.addWidget(_label(caption.upper(), "metricLabel"))
            field = _label(value)
            self.debug_values[key] = field
            box.addWidget(field)
            debug_layout.addLayout(box, 0, column)
        self.debug_panel.hide()
        layout.addWidget(self.debug_panel)
        return panel

    def _metric(self, row: QHBoxLayout, caption: str, value: str) -> QLabel:
        card = QFrame()
        card.setObjectName("metricCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(13, 9, 13, 9)
        card_layout.setSpacing(1)
        card_layout.addWidget(_label(caption, "metricLabel"))
        field = _label(value, "metricValue")
        card_layout.addWidget(field)
        row.addWidget(card, 1)
        return field

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_airpad_icon(), self)
        self.tray.setToolTip("AIRPAD · Paused")
        menu = QMenu()
        self.tray_actions: dict[str, QAction] = {}
        self.tray_actions["show"] = menu.addAction("Open AIRPAD")
        self.tray_actions["show"].triggered.connect(self._show_from_tray)
        menu.addSeparator()
        self.tray_actions["enable"] = menu.addAction("Enable control")
        self.tray_actions["enable"].triggered.connect(lambda: self.controller.set_enabled(True))
        self.tray_actions["pause"] = menu.addAction("Pause")
        self.tray_actions["pause"].triggered.connect(lambda: self.controller.set_enabled(False))
        self.tray_actions["practice"] = menu.addAction("Practice mode")
        self.tray_actions["practice"].setCheckable(True)
        self.tray_actions["practice"].setChecked(bool(getattr(self.controller, "practice", False)))
        self.tray_actions["practice"].toggled.connect(self._set_practice_from_tray)
        menu.addSeparator()
        self.tray_actions["calibrate"] = menu.addAction("Calibrate…")
        self.tray_actions["calibrate"].triggered.connect(self._start_calibration)
        self.tray_actions["settings"] = menu.addAction("Settings…")
        self.tray_actions["settings"].triggered.connect(self._open_settings)
        self.tray_actions["debug"] = menu.addAction("Show diagnostics")
        self.tray_actions["debug"].setCheckable(True)
        self.tray_actions["debug"].toggled.connect(self.debug_toggle.setChecked)
        menu.addSeparator()
        self.tray_actions["exit"] = menu.addAction("Exit AIRPAD")
        self.tray_actions["exit"].triggered.connect(self._exit_from_tray)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def _toggle_camera(self) -> None:
        if self._camera_running:
            self.controller.stop_camera()
        else:
            self.controller.start_camera()

    def _set_practice(self, enabled: bool) -> None:
        self.controller.set_practice(enabled)
        with QSignalBlocker(self.tray_actions["practice"]):
            self.tray_actions["practice"].setChecked(enabled)
        self._update_mode_note(enabled)

    def _set_practice_from_tray(self, enabled: bool) -> None:
        with QSignalBlocker(self.practice_toggle):
            self.practice_toggle.setChecked(enabled)
        self.controller.set_practice(enabled)
        self._update_mode_note(enabled)

    def _set_debug(self, enabled: bool) -> None:
        self.debug_panel.setVisible(enabled)
        with QSignalBlocker(self.tray_actions["debug"]):
            self.tray_actions["debug"].setChecked(enabled)
        self.controller.set_debug(enabled)

    def _enable_touchpad_gestures(self) -> None:
        self.controller.update_profile({'two_finger_scroll': True, 'zoom_enabled': False,
                                        'three_finger_enabled': False})

    def _update_gesture_help(self) -> None:
        enabled = self._latest.get('two_finger_scroll', self.controller.profile.two_finger_scroll)
        hold = self._latest.get('drag_hold', self.controller.profile.drag_hold)
        self.gesture_help.setText(
            f'触控板手势：{"已启用" if enabled else "未启用"}（可选）\n'
            f'单指移动 · 捏合点击\n捏住 {float(hold) * 1000:.0f} ms + 移掌拖拽\n'
            '两指移动滚动 · 两指短停后收回为单指右键\n'
            '应用后保持暂停，点击 Enable 继续。')

    def _start_calibration(self) -> None:
        self._calibration_completion_dismissed = False
        self._set_calibration_step(0)
        self.calibration_panel.show()
        self.controller.start_calibration()

    def _advance_calibration(self) -> None:
        if self._calibration_step >= 6:
            self._calibration_completion_dismissed = True
            self.calibration_panel.hide()
            return
        self.controller.calibration_next()

    def _set_calibration_step(self, step: int) -> None:
        self._calibration_step = max(0, min(6, step))
        self.calibration_progress.setValue(min(5, self._calibration_step))
        headings = (
            "Calibration · 1/5 · Open hand",
            "Calibration · 2/5 · Fist",
            "Calibration · 3/5 · Point",
            "Calibration · 4/5 · Sweep",
            "Calibration · 5/5 · Pinch",
            "Review measurement",
            "Calibration complete",
        )
        self.calibration_heading.setText(headings[self._calibration_step])
        self.calibration_next_button.setText(
            "Done" if step == 6 else "Save profile" if step == 5 else "Next")
        self.calibration_sample_progress.setVisible(step < 5)
        self.calibration_start_button.setVisible(step < 5)
        self.calibration_retry_button.setVisible(step < 5)
        self.calibration_cancel_button.setVisible(step < 6)

    def _open_settings(self) -> None:
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self.controller, self)
        else:
            self.settings_dialog.refresh()
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    def _show_problem(self, message: str) -> None:
        if not message:
            self._dismissed_problem = None
            self.problem_banner.hide()
            return
        if message == self._dismissed_problem:
            self.problem_banner.hide()
            return
        self.problem_banner_text.setText(message)
        self.problem_banner.show()

    def _dismiss_problem(self) -> None:
        self._dismissed_problem = self.problem_banner_text.text()
        self.problem_banner.hide()

    def _apply_update(self, payload: dict[str, Any]) -> None:
        self._latest.update(payload)
        if "camera_state" in payload:
            state = str(payload["camera_state"] or "off")
            self._camera_running = state in {"running", "starting", "ready"}
            self.camera_button.setText("Stop camera" if self._camera_running else "Start camera")
            camera_labels = {
                "running": "●  Camera live",
                "starting": "●  Camera starting",
                "ready": "●  Camera ready",
                "error": "●  Camera error",
            }
            self.camera_status.setText(camera_labels.get(state, "●  Camera off"))
            self._set_status_property(
                self.camera_status,
                "error" if state == "error" else ("active" if self._camera_running else "paused"),
            )
        if "enabled" in payload:
            self._enabled = bool(payload["enabled"])
            self.enable_button.setEnabled(not self._enabled)
            self.pause_button.setEnabled(self._enabled)
        if "hand_state" in payload:
            hand = str(payload["hand_state"] or "No hand")
            self.hand_status.setText(hand.replace("_", " ").title())
        if "quality" in payload:
            self._quality = bool(payload["quality"])
            self.quality_value.setText("Valid" if self._quality else "Check hand")
        if "enabled" in payload or "quality" in payload or "camera_state" in payload:
            # "Active" while no hand is tracked reads as "it is broken". Say what it is
            # actually waiting for: control is armed, but the pointer only engages once a
            # hand is seen.
            if not self._enabled:
                label, status = "●  Paused", "paused"
            elif not self._camera_running:
                label, status = "●  No camera", "paused"
            elif self._quality:
                label, status = "●  Control active", "active"
            else:
                label, status = "●  Waiting for hand", "paused"
            self.control_status.setText(label)
            self._set_status_property(self.control_status, status)
            self.tray.setToolTip(f"AIRPAD · {label.replace('●', '').strip()}")
        if "practice" in payload:
            practice = bool(payload["practice"])
            with QSignalBlocker(self.practice_toggle):
                self.practice_toggle.setChecked(practice)
            with QSignalBlocker(self.tray_actions["practice"]):
                self.tray_actions["practice"].setChecked(practice)
            self._update_mode_note(practice)
        if "fps" in payload:
            self.fps_value.setText(f"{float(payload['fps'] or 0):.1f} fps")
        if "latency_ms" in payload:
            self.latency_value.setText(f"{float(payload['latency_ms'] or 0):.0f} ms")
        if "state" in payload:
            self.gesture_value.setText(str(payload["state"] or "Idle").replace("_", " ").title())
        if any(key in payload for key in ('two_finger_scroll', 'drag_hold')):
            self._update_gesture_help()
        if any(key in payload for key in ("practice_x", "practice_y", "click_count", "dragging",
                                          'right_click_count', 'scroll_count', 'practice_scroll_x', 'practice_scroll_y')):
            self.practice_canvas.update_state(payload)
        if "calibration_step" in payload:
            step = max(0, min(6, int(payload["calibration_step"] or 0)))
            step_changed = step != self._calibration_step
            self._set_calibration_step(step)
            if step == 6 and self._calibration_completion_dismissed:
                self.calibration_panel.hide()
            elif step == 6 and step_changed:
                self.calibration_panel.show()
            elif step < 6 and (step > 0 or payload.get("calibration_text")):
                self.calibration_panel.show()
        active = bool(self._latest.get("calibration_active", False))
        if "calibration_active" in payload:
            if not active and self._calibration_step < 6:
                self.calibration_panel.hide()
            elif active:
                self.calibration_panel.show()
        ready = bool(self._latest.get("calibration_ready", False))
        collecting = bool(self._latest.get("calibration_collecting", False))
        count = int(self._latest.get("calibration_count", 0))
        target = max(1, int(self._latest.get("calibration_target", 45)))
        self.calibration_sample_progress.setRange(0, target)
        self.calibration_sample_progress.setValue(count)
        self.calibration_start_button.setEnabled(active and not collecting and not ready)
        self.calibration_start_button.setText("Capturing…" if collecting else "Start capture")
        self.calibration_retry_button.setEnabled(active and count > 0)
        self.calibration_next_button.setEnabled(self._calibration_step >= 5 or ready)
        self.enable_button.setEnabled(not self._enabled and not active)
        self.tray_actions["enable"].setEnabled(not self._enabled and not active)
        self.practice_toggle.setEnabled(not active)
        self.tray_actions["practice"].setEnabled(not active)
        self.settings_button.setEnabled(not active)
        self.touchpad_button.setEnabled(not active)
        self.tray_actions["settings"].setEnabled(not active)
        if "calibration_text" in payload and payload["calibration_text"]:
            self.calibration_text.setText(str(payload["calibration_text"]))
        if payload.get("error"):
            self._show_problem(str(payload["error"]))
        self._update_debug(payload)

    def _update_debug(self, payload: dict[str, Any]) -> None:
        if "velocity" in payload:
            self.debug_values["velocity"].setText(
                f"{float(payload['velocity'] or 0):.1f} palm/s"
            )
        if "pinch" in payload:
            self.debug_values["pinch"].setText(f"{float(payload['pinch'] or 0):.3f}")
        if "scale" in payload:
            self.debug_values["scale"].setText(f"{float(payload['scale'] or 0):.3f}")
        if "pointer_dx" in payload or "pointer_dy" in payload:
            dx = float(self._latest.get("pointer_dx", 0) or 0)
            dy = float(self._latest.get("pointer_dy", 0) or 0)
            self.debug_values["pointer"].setText(f"{dx:.1f}, {dy:.1f}")
        if "inference_ms" in payload:
            self.debug_values["inference"].setText(f"{float(payload['inference_ms'] or 0):.1f} ms")

    def _update_mode_note(self, practice: bool) -> None:
        self.mode_note.setText("PRACTICE · NO DESKTOP INPUT" if practice else "DESKTOP CONTROL MODE")
        self.mode_note.setProperty("status", "active" if practice else "paused")
        self.mode_note.style().unpolish(self.mode_note)
        self.mode_note.style().polish(self.mode_note)

    @staticmethod
    def _set_status_property(widget: QWidget, value: str) -> None:
        widget.setProperty("status", value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def _exit_from_tray(self) -> None:
        self.close()

    def _shutdown_once(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self.tray.hide()
        self.controller.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._shutdown_once()
        event.accept()


__all__ = ["MainWindow", "PracticeCanvas", "SettingsDialog"]
