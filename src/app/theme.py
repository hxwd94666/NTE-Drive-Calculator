# 定义深色和浅色主题样式与调色板。
"""Shared visual theme constants."""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QMessageBox

GRADE_COLORS = {"ACE": "#ffa726", "SSS": "#ffa726", "SS": "#f0883e", "S": "#f0883e", "A": "#7ec8e3", "B": "#5b9bd5", "C": "#4a7fb5", "D": "#3d5a80"}
GRADE_BGS = {"ACE": "#ffa72630", "SSS": "#ffa72620", "SS": "#f0883e18", "S": "#f0883e18", "A": "#7ec8e318", "B": "#5b9bd515", "C": "#4a7fb512", "D": "#3d5a8010"}

DARK_STYLE = """
QMainWindow{background:#0d1117;border:1px solid #21262d;border-radius:10px}
QDialog{background:#0d1117;border:1px solid #21262d;border-radius:8px}
QWidget{color:#c9d1d9;font-family:"Microsoft YaHei UI","Segoe UI",sans-serif;font-size:13px}

#card{background:#161b22;border:1px solid #21262d;border-radius:10px}
#cardTitle{font-size:14px;font-weight:600;color:#58a6ff}

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


LIGHT_COLOR_MAP = {
    "#0d1117": "#ffffff",
    "#161b22": "#f6f8fa",
    "#1c2128": "#eef2f6",
    "#21262d": "#d0d7de",
    "#30363d": "#d0d7de",
    "#484f58": "#afb8c1",
    "#c9d1d9": "#24292f",
    "#f0f6fc": "#24292f",
    "#8b949e": "#57606a",
    "#6e7681": "#6e7781",
    "#1f6feb33": "#0969da1c",
    "#1f6feb66": "#0969da33",
    "#1b3a24": "#d8f5df",
    "#0d1f35": "#ddf4ff",
    "#10243f": "#ddf4ff",
    "#23863622": "#1a7f3718",
    # Warehouse card action backgrounds: keep lock/discard/inspect controls
    # neutral on white theme instead of carrying the dark navy/brown fills.
    "#172a45": "#d0d7de",
    "#3a2f13": "#d0d7de",
    "#5b2026": "#d0d7de",
    "#4dd0e122": "#0969da18",
    "#4dd0e1": "#0969da",
    "#2f81f7": "#0969da",
    "#388bfd": "#218bff",
    "#79c0ff": "#54aeff",
    "#56d364": "#1a7f37",
    "#7ee787": "#1a7f37",
    "#2d1117": "#ffebe9",
    "#3c151c": "#ffebe9",
    "#ff7b72": "#cf222e",
    "#58a6ff": "#0969da",
    "#1f6feb": "#0969da",
    "#238636": "#1a7f37",
    "#2ea043": "#2da44e",
    "#da3633": "#cf222e",
    "#f85149": "#cf222e",
    # Warehouse lock: use a saturated orange on white rather than the muted
    # brown used by the old light mapping, so the tiny glyph stays obvious.
    "#d2991d": "#e8590c",
    "#d29922": "#e8590c",
    "#e3b341": "#e8590c",
    "#3fb950": "#1a7f37",
}
_DARK_COLOR_MAP = {value: key for key, value in LIGHT_COLOR_MAP.items()}

BLACK_COLOR_MAP = {
    "#0d1117": "#000000",
    "#161b22": "#080a0d",
    "#1c2128": "#111418",
    "#21262d": "#171b21",
    "#30363d": "#242a32",
    "#484f58": "#3b434d",
    "#1b3a24": "#102416",
    "#0d1f35": "#071526",
    "#10243f": "#08182c",
    "#2d1117": "#1d090d",
    "#3c151c": "#2a0d13",
}
_DARK_FROM_BLACK_COLOR_MAP = {value: key for key, value in BLACK_COLOR_MAP.items()}


def _map_light_colors(style: str) -> str:
    for src, dst in LIGHT_COLOR_MAP.items():
        style = style.replace(src, dst)
    return style


def _map_dark_colors(style: str) -> str:
    for src, dst in _DARK_COLOR_MAP.items():
        style = style.replace(src, dst)
    for src, dst in _DARK_FROM_BLACK_COLOR_MAP.items():
        style = style.replace(src, dst)
    return style


def _map_black_colors(style: str) -> str:
    for src, dst in BLACK_COLOR_MAP.items():
        style = style.replace(src, dst)
    return style


def _build_light_style() -> str:
    style = DARK_STYLE
    return _map_light_colors(style)


LIGHT_STYLE = _build_light_style()
BLACK_STYLE = _map_black_colors(DARK_STYLE)
STYLE = DARK_STYLE


THEME_PREFERENCES = {"dark", "black", "light"}
THEME_LABELS = {
    "dark": "原主题",
    "black": "黑色主题",
    "light": "白色主题",
}


def theme_preference(value: str | None) -> str:
    return value if value in THEME_PREFERENCES else "dark"


def theme_name(value: str | None) -> str:
    if value == "black":
        return "black"
    return "light" if value == "light" else "dark"


def theme_style(theme: str | None) -> str:
    name = theme_name(theme)
    if name == "light":
        return LIGHT_STYLE
    if name == "black":
        return BLACK_STYLE
    return DARK_STYLE


def current_theme_name(app: QApplication | None = None) -> str:
    app = app or QApplication.instance()
    if app is None:
        return "dark"
    return theme_name(str(app.property("nte_effective_theme") or "dark"))


def current_theme_preference(app: QApplication | None = None) -> str:
    app = app or QApplication.instance()
    if app is None:
        return "dark"
    return theme_preference(str(app.property("nte_theme_preference") or "dark"))


def current_style_sheet(app: QApplication | None = None) -> str:
    return theme_style(current_theme_name(app))


def themed_style(style: str, app: QApplication | None = None) -> str:
    theme = current_theme_name(app)
    if theme == "light":
        return _map_light_colors(style)
    if theme == "black":
        return _map_black_colors(style)
    return style


def theme_color(dark_color: str, app: QApplication | None = None) -> str:
    theme = current_theme_name(app)
    if theme == "light":
        return LIGHT_COLOR_MAP.get(dark_color, dark_color)
    if theme == "black":
        return BLACK_COLOR_MAP.get(dark_color, dark_color)
    return dark_color


def theme_rgba(dark_color: str, alpha: float, app: QApplication | None = None) -> str:
    color = QColor(theme_color(dark_color, app))
    return f"rgba({color.red()},{color.green()},{color.blue()},{max(0.0, min(1.0, alpha)):.3f})"


def refresh_inline_theme_styles(root, app: QApplication | None = None) -> None:
    """Re-map existing inline widget styles after the app theme changes."""
    theme = current_theme_name(app)
    widgets = [root, *root.findChildren(QObject)] if root is not None else []
    for widget in widgets:
        if not hasattr(widget, "styleSheet") or not hasattr(widget, "setStyleSheet"):
            continue
        style = widget.styleSheet()
        if not style:
            continue
        base = widget.property("_nte_base_style")
        if not isinstance(base, str) or not base:
            base = _map_dark_colors(style) if theme == "dark" else style
            widget.setProperty("_nte_base_style", base)
        if theme == "light":
            widget.setStyleSheet(_map_light_colors(base))
        elif theme == "black":
            widget.setStyleSheet(_map_black_colors(base))
        else:
            widget.setStyleSheet(base)


def apply_theme_palette(app: QApplication, theme: str | None = "dark") -> None:
    name = theme_name(theme)
    if name == "light":
        apply_light_palette(app)
    elif name == "black":
        apply_black_palette(app)
    else:
        apply_dark_palette(app)


def apply_app_theme(app: QApplication, theme: str | None = "dark") -> None:
    preference = theme_preference(theme)
    effective = theme_name(preference)
    app.setProperty("nte_theme_preference", preference)
    app.setProperty("nte_effective_theme", effective)
    app.setStyleSheet(theme_style(effective))
    apply_theme_palette(app, effective)


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


def apply_light_palette(app: QApplication) -> None:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#ffffff"))
    palette.setColor(QPalette.WindowText, QColor("#24292f"))
    palette.setColor(QPalette.Base, QColor("#ffffff"))
    palette.setColor(QPalette.AlternateBase, QColor("#f6f8fa"))
    palette.setColor(QPalette.ToolTipBase, QColor("#f6f8fa"))
    palette.setColor(QPalette.ToolTipText, QColor("#24292f"))
    palette.setColor(QPalette.Text, QColor("#24292f"))
    palette.setColor(QPalette.Button, QColor("#f6f8fa"))
    palette.setColor(QPalette.ButtonText, QColor("#24292f"))
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Highlight, QColor("#0969da"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)


def apply_black_palette(app: QApplication) -> None:
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor("#000000"))
    palette.setColor(QPalette.WindowText, QColor("#c9d1d9"))
    palette.setColor(QPalette.Base, QColor("#000000"))
    palette.setColor(QPalette.AlternateBase, QColor("#080a0d"))
    palette.setColor(QPalette.ToolTipBase, QColor("#080a0d"))
    palette.setColor(QPalette.ToolTipText, QColor("#c9d1d9"))
    palette.setColor(QPalette.Text, QColor("#c9d1d9"))
    palette.setColor(QPalette.Button, QColor("#171b21"))
    palette.setColor(QPalette.ButtonText, QColor("#c9d1d9"))
    palette.setColor(QPalette.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.Highlight, QColor("#1f6feb"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)


def _standard_button_value(button) -> int:
    return int(getattr(button, "value", button))


STANDARD_BUTTON_TEXT = {
    _standard_button_value(QDialogButtonBox.Ok): "确定",
    _standard_button_value(QDialogButtonBox.Cancel): "取消",
    _standard_button_value(QDialogButtonBox.Save): "保存",
    _standard_button_value(QDialogButtonBox.Close): "关闭",
    _standard_button_value(QDialogButtonBox.Yes): "是",
    _standard_button_value(QDialogButtonBox.No): "否",
    _standard_button_value(QDialogButtonBox.Discard): "放弃",
    _standard_button_value(QDialogButtonBox.Apply): "应用",
    _standard_button_value(QDialogButtonBox.Reset): "重置",
    _standard_button_value(QDialogButtonBox.Open): "打开",
}


def apply_title_bar_theme(widget, theme: str | None = None) -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        value = ctypes.c_int(0 if theme_name(theme or current_theme_name()) == "light" else 1)
        dwm = ctypes.windll.dwmapi
        # Windows 11 uses 20; older Windows 10 builds use 19.
        for attribute in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        return


def apply_dark_title_bar(widget) -> None:
    apply_title_bar_theme(widget, "dark")


def localize_standard_buttons(widget) -> None:
    for box in widget.findChildren(QDialogButtonBox):
        for button in box.buttons():
            text = STANDARD_BUTTON_TEXT.get(_standard_button_value(box.standardButton(button)))
            if text:
                button.setText(text)
    if isinstance(widget, QMessageBox):
        for button in widget.buttons():
            try:
                standard = widget.standardButton(button)
            except Exception:
                continue
            text = STANDARD_BUTTON_TEXT.get(_standard_button_value(standard))
            if text:
                button.setText(text)


class _DialogPolishFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QDialog):
            theme = current_theme_name()
            if obj.property("_nte_dialog_theme") != theme:
                obj.setProperty("_nte_dialog_theme", theme)
                obj.setStyleSheet(current_style_sheet() + "\n" + obj.styleSheet())
            localize_standard_buttons(obj)
            apply_title_bar_theme(obj, theme)
        return False


def install_dialog_defaults(app: QApplication) -> None:
    if getattr(app, "_nte_dialog_defaults_installed", False):
        return
    app._nte_dialog_defaults_installed = True
    app._nte_dialog_polish_filter = _DialogPolishFilter(app)
    app.installEventFilter(app._nte_dialog_polish_filter)
