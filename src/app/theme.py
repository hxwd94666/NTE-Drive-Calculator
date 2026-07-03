# 定义深色主题样式和调色板。
"""Shared visual theme constants."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

GRADE_COLORS = {"ACE": "#ffa726", "SSS": "#ffa726", "SS": "#f0883e", "S": "#f0883e", "A": "#7ec8e3", "B": "#5b9bd5", "C": "#4a7fb5", "D": "#3d5a80"}
GRADE_BGS = {"ACE": "#ffa72630", "SSS": "#ffa72620", "SS": "#f0883e18", "S": "#f0883e18", "A": "#7ec8e318", "B": "#5b9bd515", "C": "#4a7fb512", "D": "#3d5a8010"}

STYLE = """
QMainWindow{background:#0d1117;border:1px solid #21262d;border-radius:10px}
QDialog{background:#0d1117;border:1px solid #21262d;border-radius:8px}
QWidget{color:#c9d1d9;font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;font-size:13px}

#sidebar{background:#161b22;border-right:1px solid #21262d;min-width:200px;max-width:200px;border-bottom-left-radius:10px}
#sidebar QPushButton{background:transparent;color:#8b949e;border:none;border-radius:8px;padding:10px 14px;text-align:left;font-size:13px;font-weight:500;margin:2px 8px}
#sidebar QPushButton:hover{background:#1c2128;color:#c9d1d9}
#sidebar QPushButton:checked{background:#1f6feb33;color:#58a6ff}

#titleBar{background:#161b22;border-bottom:1px solid #21262d;border-top-left-radius:10px;border-top-right-radius:10px}
#titleBar QLabel{font-size:13px;font-weight:600;color:#c9d1d9}
#titleBar QPushButton{background:transparent;border:none;border-radius:6px;color:#8b949e;font-size:14px;padding:4px 10px;font-weight:bold}
#titleBar QPushButton:hover{background:#21262d;color:#c9d1d9}
#titleBar #btnClose:hover{background:#da3633;color:#fff}

#topbar{background:#161b22;border-bottom:1px solid #21262d;padding:10px 20px}
#topbar QLabel{font-size:15px;font-weight:600;color:#c9d1d9}

QPushButton{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:7px 16px;font-weight:500}
QPushButton:hover{background:#30363d}
QPushButton:pressed{background:#161b22}
QPushButton#btnPrimary{background:#238636;color:#fff;border:1px solid #2ea043;font-weight:600}
QPushButton#btnPrimary:hover{background:#2ea043}
QPushButton#btnPrimary:disabled{background:#1b3a24;color:#6e7681}
QPushButton#btnDanger{background:#da3633;color:#fff;border:1px solid #f85149}
QPushButton#btnDanger:hover{background:#f85149}
QPushButton#btnAction{background:#1f6feb33;color:#58a6ff;border:1px solid #1f6feb;font-size:12px;padding:5px 12px}
QPushButton#btnAction:hover{background:#1f6feb66}
QPushButton#btnSm{font-size:11px;padding:4px 8px;min-width:28px;min-height:24px}
QPushButton#btnHelp{background:transparent;border:1px solid #30363d;border-radius:10px;color:#8b949e;font-size:11px;font-weight:700;padding:2px 7px;min-width:20px;max-width:20px;min-height:20px;max-height:20px}
QPushButton#btnHelp:hover{background:#1f6feb33;color:#58a6ff;border-color:#58a6ff}

QLineEdit,QTextEdit,QSpinBox,QDoubleSpinBox,QComboBox{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:7px 10px}
QLineEdit:focus,QTextEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus,QComboBox:focus{border:1px solid #58a6ff}
QComboBox::drop-down{border:none;width:20px}
QComboBox QAbstractItemView{background:#161b22;border:1px solid #30363d;selection-background-color:#1f6feb33}

QRadioButton{spacing:10px;padding:6px 0}
QRadioButton::indicator{width:22px;height:22px;border-radius:11px;border:2px solid #30363d;background:#0d1117}
QRadioButton::indicator:checked{border:2px solid #58a6ff;background:qradialgradient(cx:0.5,cy:0.5,radius:0.5,fx:0.5,fy:0.5,stop:0 #58a6ff,stop:0.45 #1f6feb,stop:0.5 #0d1117,stop:1 #0d1117)}
QRadioButton::indicator:hover{border:2px solid #58a6ff}
QCheckBox{spacing:8px}
QCheckBox::indicator{width:18px;height:18px;border-radius:4px;border:2px solid #30363d;background:#0d1117}
QCheckBox::indicator:checked{background:#238636;border-color:#2ea043}
QCheckBox#autoDiscardToggle{spacing:10px;padding:6px 0}
QCheckBox#autoDiscardToggle::indicator{width:22px;height:22px;border-radius:11px;border:2px solid #30363d;background:#0d1117}
QCheckBox#autoDiscardToggle::indicator:checked{border:2px solid #58a6ff;background:qradialgradient(cx:0.5,cy:0.5,radius:0.5,fx:0.5,fy:0.5,stop:0 #58a6ff,stop:0.45 #1f6feb,stop:0.5 #0d1117,stop:1 #0d1117)}
QCheckBox#autoDiscardToggle::indicator:hover{border:2px solid #58a6ff}

QScrollArea{border:none;background:transparent}
QScrollBar:vertical{background:#0d1117;width:8px;border-radius:4px}
QScrollBar::handle:vertical{background:#30363d;border-radius:4px;min-height:30px}
QScrollBar::handle:vertical:hover{background:#484f58}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}

QTabWidget::pane{border:1px solid #21262d;background:#0d1117;border-radius:8px}
QTabBar::tab{background:#161b22;color:#8b949e;padding:8px 18px;border:1px solid #21262d;border-bottom:none;border-top-left-radius:8px;border-top-right-radius:8px}
QTabBar::tab:selected{background:#0d1117;color:#58a6ff;border-bottom:2px solid #58a6ff}

QToolTip{background:#161b22;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 10px;font-size:12px}

QGroupBox{background:#0d1117;border:1px solid #30363d;border-radius:10px;margin-top:16px;padding:22px;padding-top:34px;font-size:14px;font-weight:700;color:#58a6ff}
QGroupBox::title{subcontrol-origin:margin;left:14px;padding:0 8px}

#logPanel{background:#0d1117;border-top:1px solid #21262d}
#logPanel QTextEdit{background:#0d1117;border:none;color:#8b949e;font-family:'Consolas','Cascadia Code',monospace;font-size:11px}

QKeySequenceEdit{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:6px;padding:6px 8px;font-family:'Consolas',monospace;font-size:12px}
"""


def apply_dark_palette(app: QApplication) -> None:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#0d1117"))
    palette.setColor(QPalette.WindowText, QColor("#c9d1d9"))
    palette.setColor(QPalette.Base, QColor("#0d1117"))
    palette.setColor(QPalette.AlternateBase, QColor("#161b22"))
    palette.setColor(QPalette.ToolTipBase, QColor("#161b22"))
    palette.setColor(QPalette.ToolTipText, QColor("#c9d1d9"))
    palette.setColor(QPalette.Text, QColor("#c9d1d9"))
    palette.setColor(QPalette.Button, QColor("#21262d"))
    palette.setColor(QPalette.ButtonText, QColor("#c9d1d9"))
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Highlight, QColor("#1f6feb"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)
