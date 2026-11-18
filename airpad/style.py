"""Visual tokens and application stylesheet for AIRPAD."""

GRAPHITE_950 = "#0C1110"
GRAPHITE_900 = "#111816"
GRAPHITE_850 = "#151E1B"
GRAPHITE_800 = "#1A2421"
GRAPHITE_700 = "#26332F"
MINT = "#74F2C1"
MINT_SOFT = "#B5F9DE"
TEXT = "#F1F7F4"
TEXT_MUTED = "#91A39C"
WARNING = "#FFC970"
DANGER = "#FF7B83"


APP_STYLESHEET = f"""
QWidget {{
    color: {TEXT};
    font-family: "Segoe UI";
    font-size: 13px;
}}
QMainWindow, QDialog {{ background: {GRAPHITE_950}; }}
QFrame#sidebar, QFrame#panel, QFrame#metricCard, QFrame#calibrationPanel,
QFrame#debugPanel, QFrame#problemBanner {{
    background: {GRAPHITE_850};
    border: 1px solid {GRAPHITE_700};
    border-radius: 16px;
}}
QFrame#metricCard {{ border-radius: 12px; }}
QFrame#problemBanner {{
    background: #2A1D1F;
    border-color: #5A3035;
}}
QLabel#brand {{ font-size: 25px; font-weight: 700; letter-spacing: 2px; }}
QLabel#eyebrow {{
    color: {MINT};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#pageTitle {{ font-size: 22px; font-weight: 650; }}
QLabel#sectionTitle {{ font-size: 15px; font-weight: 650; }}
QLabel#metricValue {{ font-size: 18px; font-weight: 650; }}
QLabel#muted, QLabel#metricLabel, QLabel#privacyNote {{ color: {TEXT_MUTED}; }}
QLabel#privacyNote {{ font-size: 12px; }}
QLabel#statusPill {{
    background: #172820;
    color: {MINT_SOFT};
    border: 1px solid #2B5746;
    border-radius: 11px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
}}
QLabel#statusPill[status="paused"] {{
    background: #252823;
    color: #D6D8C6;
    border-color: #41453A;
}}
QLabel#statusPill[status="error"] {{
    background: #321E21;
    color: #FFC8CC;
    border-color: #6B343B;
}}
QPushButton {{
    min-height: 38px;
    padding: 0 15px;
    background: {GRAPHITE_800};
    border: 1px solid #31403B;
    border-radius: 10px;
    font-weight: 600;
}}
QPushButton:hover {{ background: #22302B; border-color: #455B53; }}
QPushButton:pressed {{ background: #101714; }}
QPushButton#primaryButton {{
    color: #082017;
    background: {MINT};
    border-color: {MINT};
}}
QPushButton#primaryButton:hover {{ background: #8AF6CC; border-color: #8AF6CC; }}
QPushButton#dangerButton {{ color: #FFC4C8; }}
QToolButton {{
    min-height: 34px;
    padding: 0 12px;
    border-radius: 9px;
    background: transparent;
    border: 1px solid transparent;
    color: {TEXT_MUTED};
}}
QToolButton:hover {{ background: {GRAPHITE_800}; color: {TEXT}; border-color: {GRAPHITE_700}; }}
QCheckBox {{ spacing: 9px; }}
QCheckBox::indicator {{
    width: 34px; height: 18px;
    border-radius: 9px;
    background: #303D38;
    border: 1px solid #47574F;
}}
QCheckBox::indicator:checked {{ background: {MINT}; border-color: {MINT}; }}
QSpinBox, QDoubleSpinBox, QComboBox {{
    min-height: 34px;
    padding: 0 10px;
    background: {GRAPHITE_900};
    border: 1px solid {GRAPHITE_700};
    border-radius: 8px;
    selection-background-color: {MINT};
    selection-color: #082017;
}}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {MINT}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QProgressBar {{
    min-height: 7px; max-height: 7px;
    border: none; border-radius: 3px;
    background: #25302D;
    text-align: center;
}}
QProgressBar::chunk {{ background: {MINT}; border-radius: 3px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #35443E; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMenu {{
    background: {GRAPHITE_850};
    border: 1px solid {GRAPHITE_700};
    border-radius: 9px;
    padding: 6px;
}}
QMenu::item {{ padding: 8px 24px 8px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: #22352E; color: {MINT_SOFT}; }}
QToolTip {{
    color: {TEXT}; background: {GRAPHITE_800};
    border: 1px solid {GRAPHITE_700}; padding: 6px;
}}
"""
