# 提供养成计算器复用的等级、突破与参与开关控件。
"""Reusable compact controls for cultivation targets."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
    QToolButton,
    QWidget,
)

from src.app.theme import theme_color, themed_style


class _CultivationSpinBox(QSpinBox):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        disable_numeric_input_method(self)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(theme_color("#8b949e")))
        pen.setWidth(2)
        painter.setPen(pen)
        for control, upward in (
            (QStyle.SubControl.SC_SpinBoxUp, True),
            (QStyle.SubControl.SC_SpinBoxDown, False),
        ):
            rect = self.style().subControlRect(
                QStyle.ComplexControl.CC_SpinBox,
                option,
                control,
                self,
            )
            center_x = rect.center().x()
            center_y = rect.center().y()
            tip_offset = -2 if upward else 2
            painter.drawLine(center_x - 3, center_y - tip_offset, center_x, center_y + tip_offset)
            painter.drawLine(center_x, center_y + tip_offset, center_x + 3, center_y - tip_offset)
        painter.end()


def disable_numeric_input_method(control: QSpinBox) -> None:
    """Keep numeric cultivation fields out of third-party text IME contexts."""

    hints = Qt.InputMethodHint.ImhDigitsOnly | Qt.InputMethodHint.ImhNoPredictiveText
    control.setInputMethodHints(hints)
    control.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)
    editor = control.findChild(QLineEdit)
    if editor is not None:
        editor.setInputMethodHints(hints)
        editor.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, False)


class _ParticipationToggle(QToolButton):
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(self.rect()).adjusted(1, 5, -1, -5)
        checked = self.isChecked()
        painter.setPen(QPen(QColor(theme_color(
            "#58a6ff" if self.underMouse() else "#30363d"
        )), 1.2))
        painter.setBrush(QColor(theme_color("#238636" if checked else "#30363d")))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        knob_size = track.height() - 6
        knob_x = track.right() - knob_size - 3 if checked else track.left() + 3
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff" if checked else theme_color("#8b949e")))
        painter.drawEllipse(QRectF(knob_x, track.top() + 3, knob_size, knob_size))
        painter.end()


def participation_toggle(parent: QWidget, label: str) -> QToolButton:
    toggle = _ParticipationToggle(parent)
    toggle.setObjectName("cultivationParticipationToggle")
    toggle.setCheckable(True)
    toggle.setChecked(True)
    toggle.setToolTip(f"{label}：参与计算（点击关闭）")
    toggle.toggled.connect(lambda enabled: toggle.setToolTip(
        f"{label}：{'参与计算（点击关闭）' if enabled else '不参与计算（点击开启）'}"
    ))
    toggle.setCursor(Qt.CursorShape.PointingHandCursor)
    toggle.setFixedSize(36, 26)
    return toggle


def level_spinbox(parent: QWidget) -> QSpinBox:
    control = _CultivationSpinBox(parent)
    control.setRange(1, 80)
    control.setSuffix(" 级")
    control.setFixedWidth(92)
    return control


def stage_combobox(parent: QWidget) -> QComboBox:
    control = QComboBox(parent)
    control.setMinimumWidth(150)
    return control


def skill_spinbox(parent: QWidget) -> QSpinBox:
    control = _CultivationSpinBox(parent)
    control.setFixedWidth(68)
    return control


def style_progress_arrow(label: QLabel, *, compact: bool = False) -> None:
    """Give both themes a legible, restrained direction marker."""

    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setFixedSize(28 if compact else 38, 28 if compact else 38)
    label.setStyleSheet(themed_style(
        "color:#58a6ff;background:#0d1f35;border:1px solid #58a6ff;"
        "border-radius:7px;font-size:16px;font-weight:900;"
    ))


def style_skill_badge(label: QLabel) -> None:
    """Keep A/E/Q/QTE readable against the light and dark card surfaces."""

    label.setFixedSize(44, 36)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setStyleSheet(themed_style(
        "color:#58a6ff;background:#0d1f35;border:1px solid #58a6ff;"
        "border-radius:6px;font-size:12px;font-weight:900;"
    ))


__all__ = [
    "disable_numeric_input_method",
    "level_spinbox",
    "participation_toggle",
    "skill_spinbox",
    "style_progress_arrow",
    "style_skill_badge",
    "stage_combobox",
]
