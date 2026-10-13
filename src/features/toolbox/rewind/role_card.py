# 组合倒带角色卡的头像、勾选状态、评分与卡内槽位入口。
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from src.app.theme import GRADE_COLORS, current_theme_name, themed_style
from src.features.toolbox.rewind.role_selection import rewind_role_card_presentation


class _ElidedLabel(QLabel):
    def setText(self, text):
        self._full_text = str(text)
        self.setAccessibleName(self._full_text)
        self._refresh_text()

    def _refresh_text(self):
        super().setText(self.fontMetrics().elidedText(getattr(self, "_full_text", ""), Qt.ElideRight, self.width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_text()


class _SlotButton(QPushButton):
    def set_caption(self, caption, *, available):
        self._caption, self._available = caption, available
        self.setEnabled(available)
        self._refresh_text()

    def _refresh_text(self):
        suffix = "  ▾" if getattr(self, "_available", False) else ""
        caption = self.fontMetrics().elidedText(getattr(self, "_caption", ""), Qt.ElideRight, max(0, self.width() - 36))
        self.setText(caption + suffix)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_text()


class _SelectionButton(QToolButton):
    def __init__(self, parent):
        super().__init__(parent)
        self.mark = QLabel(self)
        self.mark.setObjectName("rewindRoleCheck")
        self.mark.setAlignment(Qt.AlignCenter)
        self.mark.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.mark.resize(18, 18)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._sync_outer_focus(True)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._sync_outer_focus(False)

    def _sync_outer_focus(self, focused):
        wrapper = self.parentWidget()
        if wrapper is not None:
            wrapper.setProperty("roleFocused", focused)
            wrapper.style().unpolish(wrapper)
            wrapper.style().polish(wrapper)
            wrapper.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.mark.move(max(0, self.width() - 20), 2)
        self.mark.raise_()


class RewindRoleCard(QFrame):
    def __init__(self, parent, role, selected_reference, portrait: QPixmap):
        super().__init__(parent)
        self.setObjectName("rewindRoleCard")
        self.setMinimumWidth(136)
        self.setFixedHeight(176)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(themed_style(
            "QFrame#rewindRoleCard{background:#161b22;border:1px solid #30363d;border-radius:9px;}"
            "QFrame#rewindRoleCard:hover{border-color:#58a6ff;}"
            "QFrame#rewindRoleCard[roleFocused=\"true\"]{border-color:#58a6ff;}"
            "QFrame#rewindRoleCard[chosen=\"true\"]{background:#0d1f35;border:2px solid #58a6ff;}"
            "QToolButton#rewindRoleSelectionCard{background:transparent;border:none;padding:0;}"
            "QToolButton#rewindRoleSelectionCard:focus{border:none;outline:none;}"
            "QLabel{background:transparent;border:none;padding:0;}"
            "QLabel#rewindRoleCheck{border:1px solid #6e7681;border-radius:4px;color:#8b949e;}"
            "QLabel#rewindRoleCheck[chosen=\"true\"]{background:#1f6feb;border-color:#58a6ff;color:#fff;font-weight:800;}"
            "QPushButton#rewindSwitchSlot{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;"
            "border-radius:5px;font-size:12px;padding:0 6px;}"
            "QPushButton#rewindSwitchSlot:hover,QPushButton#rewindSwitchSlot:focus{border-color:#58a6ff;background:#1f6feb33;}"
            "QPushButton#rewindSwitchSlot:disabled{color:#6e7681;background:transparent;border-color:#21262d;}"
        ))
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self.selection_button = _SelectionButton(self)
        self.selection_button.setObjectName("rewindRoleSelectionCard")
        self.selection_button.setCheckable(True)
        self.selection_button.setFocusPolicy(Qt.StrongFocus)
        self.selection_button.setCursor(Qt.PointingHandCursor)
        self.selection_button.setAccessibleName(role.name)
        self.selection_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body = QVBoxLayout(self.selection_button)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        avatar = QLabel(self.selection_button)
        avatar.setFixedSize(64, 64)
        avatar.setAlignment(Qt.AlignCenter)
        if not portrait.isNull():
            avatar.setPixmap(portrait)
        else:
            avatar.setText("?")
        body.addWidget(avatar, 0, Qt.AlignHCenter)
        self.name_label = _ElidedLabel(self.selection_button)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setFixedHeight(20)
        self.name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.name_label.setStyleSheet(themed_style("color:#f0f6fc;font-size:13px;font-weight:700"))
        self.name_label.setText(role.name)
        body.addWidget(self.name_label)
        score_row = QWidget(self.selection_button)
        score_layout = QHBoxLayout(score_row)
        score_layout.setContentsMargins(0, 0, 0, 0)
        score_layout.setSpacing(5)
        score_layout.setAlignment(Qt.AlignCenter)
        self.score_label = QLabel(score_row)
        self.score_label.setObjectName("rewindRoleCalculationScore")
        self.grade_label = QLabel(score_row)
        self.grade_label.setObjectName("rewindRoleGrade")
        score_layout.addWidget(self.score_label)
        score_layout.addWidget(self.grade_label)
        score_row.setFixedHeight(22)
        body.addWidget(score_row)
        # The body is one accessible checkable button; decorative children do
        # not swallow mouse clicks. The footer is a separate keyboard action.
        for child in (avatar, self.name_label, score_row):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        root.addWidget(self.selection_button, 1)
        self.slot_button = _SlotButton(self)
        self.slot_button.setObjectName("rewindSwitchSlot")
        self.slot_button.setFixedHeight(30)
        self.slot_button.setCursor(Qt.PointingHandCursor)
        root.addWidget(self.slot_button)
        self.selection_button.toggled.connect(self.sync_checked)
        self.set_reference(role, selected_reference)
        self.sync_checked()

    def set_reference(self, role, selected_reference):
        display = rewind_role_card_presentation(role, selected_reference)
        self.selection_button.setProperty("rewindCalculationScore", display.score)
        self.selection_button.setToolTip(display.detail)
        self.selection_button.setAccessibleDescription(display.detail)
        self.score_label.setText(display.score_text)
        self.grade_label.setText(display.grade)
        self.grade_label.setVisible(bool(display.grade))
        color = GRADE_COLORS.get(display.grade, "#8b949e")
        if current_theme_name() == "light":
            color = QColor(color).darker(150).name()
        elif QColor(color).lightnessF() < 0.45:
            color = QColor(color).lighter(145).name()
        self.score_label.setStyleSheet(
            f"color:{color};font-size:13px;font-weight:700;" if display.score is not None
            else themed_style("color:#8b949e;font-size:11px;")
        )
        self.grade_label.setStyleSheet(
            f"color:{color};border:1px solid {color};border-radius:7px;"
            "font-size:10px;font-weight:700;padding:1px 4px;background:transparent;"
        )
        self.slot_button.set_caption(display.slot_text, available=display.can_switch)
        self.slot_button.setToolTip(display.slot_detail)
        self.slot_button.setAccessibleName(display.slot_detail)

    def sync_checked(self, *_):
        checked = self.selection_button.isChecked()
        self.setProperty("chosen", checked)
        mark = self.selection_button.mark
        mark.setText("✓" if checked else "")
        mark.setAccessibleName("已选" if checked else "未选")
        mark.setProperty("chosen", checked)
        for widget in (self, mark):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
