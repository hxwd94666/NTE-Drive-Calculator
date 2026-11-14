# 共用倒带推荐的角色选择卡：头像、键盘勾选、总分评级和槽位入口使用同一布局。
from __future__ import annotations

import math
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget
from src.app.theme import GRADE_COLORS, current_theme_name, themed_style
from src.domain.equipment.scoring.allocation_rating import loadout_total_grade
from src.ui.common.text_catalog import ElidedTextLabel, resolve_text_catalog


class _ScoreLabel(ElidedTextLabel):
    def set_score_text(self, text, *, numeric):
        self._numeric = numeric
        self.setText(text)

    def _elide(self):
        # 数字是完整结果，不作省略；否则槽位回调还会把省略号带入评分提示。
        if getattr(self, "_numeric", False):
            QLabel.setText(self, self._full_text)
        else:
            super()._elide()

    def minimumSizeHint(self):
        # 数字及评级各自保留自然宽度；缺资料等长补丁提示仍可收缩。
        return self.sizeHint() if getattr(self, "_numeric", False) else super().minimumSizeHint()


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
        self.mark.setObjectName("roleSelectionCheck")
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


class RoleSelectionCard(QFrame):
    score_changed = Signal()

    def __init__(self, parent, name: str, portrait: QPixmap, *, catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.text = self.text_catalog.render
        self.setObjectName("roleSelectionFrame")
        # 固定卡片宽度，避免父级网格在窄窗口中把头像和文字横向拉伸。
        self.setFixedWidth(154)
        self.setFixedHeight(176)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            themed_style(
                "QFrame#roleSelectionFrame{background:#161b22;border:1px solid #30363d;border-radius:9px;}"
                "QFrame#roleSelectionFrame:hover{border-color:#58a6ff;}"
                'QFrame#roleSelectionFrame[roleFocused="true"]{border-color:#58a6ff;}'
                'QFrame#roleSelectionFrame[chosen="true"]{background:#0d1f35;border:2px solid #58a6ff;}'
                "QToolButton#roleSelectionCard{background:transparent;border:none;padding:0;}"
                "QToolButton#roleSelectionCard:focus{border:none;outline:none;}"
                "QLabel{background:transparent;border:none;padding:0;}"
                "QLabel#roleSelectionCheck{border:1px solid #6e7681;border-radius:4px;color:#8b949e;}"
                'QLabel#roleSelectionCheck[chosen="true"]{background:#1f6feb;border-color:#58a6ff;color:#fff;font-weight:800;}'
                "QPushButton#roleSelectionSlot{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;"
                "border-radius:5px;font-size:12px;padding:0 6px;}"
                "QPushButton#roleSelectionSlot:hover,QPushButton#roleSelectionSlot:focus{border-color:#58a6ff;background:#1f6feb33;}"
                "QPushButton#roleSelectionSlot:disabled{color:#6e7681;background:transparent;border-color:#21262d;}"
            )
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        self.selection_button = _SelectionButton(self)
        self.selection_button.setObjectName("roleSelectionCard")
        self.selection_button.setCheckable(True)
        self.selection_button.setFocusPolicy(Qt.StrongFocus)
        self.selection_button.setCursor(Qt.PointingHandCursor)
        self.selection_button.setAccessibleName(name)
        self.selection_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body = QVBoxLayout(self.selection_button)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        avatar = QLabel(self.selection_button)
        avatar.setTextFormat(Qt.PlainText)
        avatar.setFixedSize(64, 64)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setAccessibleName(self.text("selector.portrait", role=name))
        if not portrait.isNull():
            # 统一按逻辑尺寸等比缩放，防止 Windows 缩放下头像裁切或偏移。
            dpr = max(1.0, float(portrait.devicePixelRatioF()))
            pixels = max(64, round(64 * dpr))
            fitted = portrait.scaled(pixels, pixels, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            fitted.setDevicePixelRatio(dpr)
            avatar.setPixmap(fitted)
        else:
            avatar.setText("?")
        body.addWidget(avatar, 0, Qt.AlignHCenter)
        self.name_label = _ElidedLabel(self.selection_button)
        self.name_label.setTextFormat(Qt.PlainText)
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setFixedHeight(20)
        self.name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.name_label.setStyleSheet(themed_style("color:#f0f6fc;font-size:13px;font-weight:700"))
        self.name_label.setText(name)
        body.addWidget(self.name_label)
        score_row = QWidget(self.selection_button)
        score_layout = QHBoxLayout(score_row)
        score_layout.setContentsMargins(0, 0, 0, 0)
        score_layout.setSpacing(5)
        score_layout.setAlignment(Qt.AlignCenter)
        # 缺资料提示也可能被补丁扩长；只省略可见文字，完整内容保留为可访问名称和提示。
        self.score_label = _ScoreLabel("", score_row)
        self.score_label.setTextFormat(Qt.PlainText)
        self.score_label.setObjectName("roleSelectionScore")
        self.grade_label = QLabel(score_row)
        self.grade_label.setTextFormat(Qt.PlainText)
        self.grade_label.setObjectName("roleSelectionGrade")
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
        self.slot_button.setObjectName("roleSelectionSlot")
        self.slot_button.setFixedHeight(30)
        self.slot_button.setCursor(Qt.PointingHandCursor)
        root.addWidget(self.slot_button)
        self.selection_button.toggled.connect(self.sync_checked)
        self.sync_checked()

    def set_score(self, score, *, text=None, grade=None):
        # 已保存/已冻结总分直接展示，缺失与非有限值保持未知，不触发重评分。
        score = (
            float(score)
            if isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score)
            else None
        )
        grade = (loadout_total_grade(score) if score is not None else "") if grade is None else grade
        self.grade_label.setText(grade)
        self.grade_label.setVisible(bool(grade))
        self.selection_button.setProperty("roleScore", score)
        color = GRADE_COLORS.get(grade, "#8b949e")
        if current_theme_name() == "light":
            color = QColor(color).darker(150).name()
        elif QColor(color).lightnessF() < 0.45:
            color = QColor(color).lighter(145).name()
        self.score_label.setStyleSheet(
            f"color:{color};font-size:13px;font-weight:700;"
            if score is not None
            else themed_style("color:#8b949e;font-size:11px;")
        )
        self.grade_label.setStyleSheet(
            f"color:{color};border:1px solid {color};border-radius:7px;font-size:10px;font-weight:700;padding:1px 4px;background:transparent;"
        )
        # 先应用本次字号再设置文本，不用上一种评分状态的字形尺寸计算省略。
        self.score_label.set_score_text(
            text
            if text is not None
            else (f"{score:.2f}".rstrip("0").rstrip(".") if score is not None else self.text("selector.score_unknown")),
            numeric=score is not None,
        )
        self.score_changed.emit()

    def sync_checked(self, *_):
        checked = self.selection_button.isChecked()
        self.setProperty("chosen", checked)
        mark = self.selection_button.mark
        mark.setText("✓" if checked else "")
        mark.setAccessibleName(self.text("selector.checked" if checked else "selector.unchecked"))
        mark.setProperty("chosen", checked)
        for widget in (self, mark):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
