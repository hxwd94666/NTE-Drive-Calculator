# 构建单角色养成目标的响应式编辑卡片与高清角色图像。
"""Presentation-only controls for the single-character cultivation planner."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from src.app.theme import themed_style
from src.features.toolbox.cultivation_controls import (
    level_spinbox, participation_toggle, skill_spinbox, stage_combobox,
    style_progress_arrow, style_skill_badge,
)
from src.services.cultivation_planner_service import CultivationSeed
from src.ui.image_scaling import asset_pixmap


class CultivationSingleEditor(QWidget):
    """Keep the editable fields and imagery together without owning calculation."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.skill_inputs: dict[str, tuple[QSpinBox, QSpinBox]] = {}
        self._skill_cards: list[QFrame] = []
        self._skill_columns = 0
        self._progress_pairs: list[QBoxLayout] = []
        self._progress_arrows: list[QLabel] = []
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(11)
        heading = QLabel("养成目标", self)
        heading.setStyleSheet(themed_style("font-size:14px;font-weight:800;color:#c9d1d9"))
        root.addWidget(heading)

        role, role_body = self._section("", "")
        role_header = QHBoxLayout()
        self.role_icon = self._icon(role, 58)
        role_header.addWidget(self.role_icon)
        role_copy = QVBoxLayout()
        self.role_name = QLabel("尚未选择角色", role)
        self.role_name.setStyleSheet(themed_style("color:#f0f6fc;font-size:16px;font-weight:800"))
        role_copy.addWidget(self.role_name)
        note = QLabel("角色等级与突破 · 已按角色档案预填", role)
        note.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        role_copy.addWidget(note)
        role_header.addLayout(role_copy, 1)
        self.role_button = QPushButton("更换角色", role)
        self.role_button.setObjectName("cultivationCalculatorRoleSelector")
        role_header.addWidget(self.role_button)
        role_header.addWidget(QLabel("参与计算", role))
        self.character_toggle = participation_toggle(role, "角色养成")
        role_header.addWidget(self.character_toggle)
        role_body.addLayout(role_header)
        self.current_level, self.current_stage = self._level_fields(role)
        self.target_level, self.target_stage = self._level_fields(role)
        role_body.addWidget(self._progress_pair(
            role, self.current_level, self.current_stage,
            self.target_level, self.target_stage,
        ))
        root.addWidget(role)

        skills, skills_body = self._section("技能等级", "当前等级 → 目标等级")
        skill_header = QHBoxLayout()
        skill_header.addStretch(1)
        skill_header.addWidget(QLabel("参与计算", skills))
        self.skills_toggle = participation_toggle(skills, "技能目标")
        skill_header.addWidget(self.skills_toggle)
        skills_body.addLayout(skill_header)
        self.skills_grid = QGridLayout()
        self.skills_grid.setContentsMargins(0, 0, 0, 0)
        self.skills_grid.setHorizontalSpacing(10)
        self.skills_grid.setVerticalSpacing(9)
        skills_body.addLayout(self.skills_grid)
        fork, fork_body = self._section("", "")
        identity = QWidget(fork)
        identity_row = QHBoxLayout(identity)
        identity_row.setContentsMargins(0, 0, 0, 0)
        self.fork_icon = self._icon(identity, 72)
        identity_row.addWidget(self.fork_icon)
        fork_copy = QVBoxLayout()
        self.fork_name = QLabel("尚未选择弧盘", identity)
        self.fork_name.setStyleSheet(themed_style("color:#f0f6fc;font-size:14px;font-weight:800"))
        fork_copy.addWidget(self.fork_name)
        fork_note = QLabel("弧盘等级与突破 · 当前角色使用", identity)
        fork_note.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        fork_copy.addWidget(fork_note)
        identity_row.addLayout(fork_copy, 1)
        self.fork_button = QPushButton("更换弧盘", identity)
        self.fork_button.setObjectName("cultivationCalculatorForkSelector")
        identity_row.addWidget(self.fork_button)
        identity_row.addWidget(QLabel("参与计算", identity))
        self.fork_toggle = participation_toggle(fork, "弧盘养成")
        identity_row.addWidget(self.fork_toggle)
        fork_body.addWidget(identity)
        self.fork_current_level, self.fork_current_stage = self._level_fields(fork)
        self.fork_target_level, self.fork_target_stage = self._level_fields(fork)
        fork_body.addWidget(self._progress_pair(
            fork, self.fork_current_level, self.fork_current_stage,
            self.fork_target_level, self.fork_target_stage,
        ))
        root.addWidget(fork)
        root.addWidget(skills)
        self._reflow()

    @staticmethod
    def _icon(parent: QWidget, side: int) -> QLabel:
        label = QLabel(parent)
        label.setFixedSize(side, side)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(themed_style(
            "background:#21262d;border:1px solid #484f58;border-radius:7px"
        ))
        return label

    @staticmethod
    def _section(title: str, note: str) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setObjectName("cultivationSingleSection")
        panel.setStyleSheet(themed_style(
            "QFrame#cultivationSingleSection{background:#161b22;border:1px solid #30363d;"
            "border-radius:9px;}"
            "QFrame#cultivationSingleState,QFrame#cultivationSingleSkillCard{background:#0d1117;"
            "border:1px solid #30363d;border-radius:7px;}"
        ))
        body = QVBoxLayout(panel)
        body.setContentsMargins(14, 12, 14, 13)
        body.setSpacing(10)
        if title:
            caption = QHBoxLayout()
            label = QLabel(title, panel)
            label.setStyleSheet(themed_style("color:#f0f6fc;font-size:14px;font-weight:800"))
            caption.addWidget(label)
            subtitle = QLabel(note, panel)
            subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
            caption.addWidget(subtitle)
            caption.addStretch(1)
            body.addLayout(caption)
        return panel, body

    @staticmethod
    def _level_fields(parent: QWidget) -> tuple[QSpinBox, QComboBox]:
        level = level_spinbox(parent)
        stage = stage_combobox(parent)
        for control in (level, stage):
            control.setMinimumHeight(38)
            control.setMinimumWidth(110 if isinstance(control, QSpinBox) else 145)
            control.setMaximumWidth(16_777_215)
            control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return level, stage

    @staticmethod
    def _state(parent: QWidget, title: str, level: QSpinBox, stage: QComboBox) -> QFrame:
        state = QFrame(parent)
        state.setObjectName("cultivationSingleState")
        body = QVBoxLayout(state)
        body.setContentsMargins(11, 8, 11, 9)
        body.setSpacing(6)
        label = QLabel(title, state)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:11px;font-weight:700"))
        body.addWidget(label)
        fields = QHBoxLayout()
        fields.setSpacing(8)
        fields.addWidget(level, 1)
        fields.addWidget(stage, 1)
        body.addLayout(fields)
        return state

    def _progress_pair(
        self, parent: QWidget, current: QSpinBox, current_stage: QComboBox,
        target: QSpinBox, target_stage: QComboBox,
    ) -> QWidget:
        wrapper = QWidget(parent)
        pair = QBoxLayout(QBoxLayout.Direction.LeftToRight, wrapper)
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setSpacing(10)
        pair.addWidget(self._state(wrapper, "当前状态", current, current_stage), 1)
        arrow = QLabel("→", wrapper)
        style_progress_arrow(arrow)
        pair.addWidget(arrow)
        pair.addWidget(self._state(wrapper, "目标状态", target, target_stage), 1)
        self._progress_pairs.append(pair)
        self._progress_arrows.append(arrow)
        return wrapper

    @staticmethod
    def _set_image(label: QLabel, path: str | Path | None, fallback: str) -> None:
        pixmap = asset_pixmap(path, label.width() - 4, label.devicePixelRatioF())
        label.clear()
        if pixmap.isNull():
            label.setText(fallback)
        else:
            label.setPixmap(pixmap)

    def set_role(self, name: str, image: str | Path | None) -> None:
        self.role_name.setText(name)
        self._set_image(self.role_icon, image, name[:1] or "?")

    def set_fork(self, name: str | None, image: str | Path | None) -> None:
        self.fork_name.setText(name or "尚未选择弧盘")
        self.fork_button.setText("更换弧盘" if name else "选择弧盘")
        self._set_image(self.fork_icon, image, "弧")

    def set_skills(self, seed: CultivationSeed) -> None:
        while self.skills_grid.count():
            item = self.skills_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._skill_cards.clear()
        self.skill_inputs.clear()
        if not seed.skills:
            self.skills_grid.addWidget(QLabel("当前静态库未提供可升级技能。", self), 0, 0)
            return
        for skill in seed.skills:
            card = QFrame(self)
            card.setObjectName("cultivationSingleSkillCard")
            line = QHBoxLayout(card)
            line.setContentsMargins(10, 7, 10, 7)
            line.setSpacing(8)
            badge = QLabel(skill.category, card)
            style_skill_badge(badge)
            line.addWidget(badge)
            name = QLabel(skill.name, card)
            name.setToolTip(skill.name)
            name.setMinimumWidth(0)
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            line.addWidget(name, 1)
            current, target = skill_spinbox(card), skill_spinbox(card)
            for control in (current, target):
                control.setFixedWidth(88)
                control.setMinimumHeight(40)
                control.setRange(1, skill.maximum_level)
            current.setValue(skill.current_level)
            target.setValue(skill.maximum_level)
            line.addWidget(current)
            arrow = QLabel("→", card)
            style_progress_arrow(arrow, compact=True)
            line.addWidget(arrow)
            line.addWidget(target)
            self._skill_cards.append(card)
            self.skill_inputs[skill.skill_id] = (current, target)
        self._skill_columns = 0
        self._reflow()

    def _reflow(self) -> None:
        width = self.width()
        horizontal = width >= 760
        for pair, arrow in zip(self._progress_pairs, self._progress_arrows):
            pair.setDirection(
                QBoxLayout.Direction.LeftToRight if horizontal
                else QBoxLayout.Direction.TopToBottom
            )
            arrow.setText("→" if horizontal else "↓")
        columns = 4 if width >= 1440 else 2 if width >= 720 else 1
        if columns != self._skill_columns:
            while self.skills_grid.count():
                self.skills_grid.takeAt(0)
            for index, card in enumerate(self._skill_cards):
                self.skills_grid.addWidget(card, index // columns, index % columns)
            self._skill_columns = columns

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._reflow()


__all__ = ["CultivationSingleEditor"]
