# 提供多角色养成列表中的可展开目标编辑卡片。
"""Inline target editor used by the batch cultivation page."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.ui.image_scaling import asset_pixmap
from src.features.toolbox.cultivation_controls import (
    level_spinbox,
    participation_toggle,
    skill_spinbox,
    stage_combobox,
    style_progress_arrow,
    style_skill_badge,
)
from src.services.cultivation_planner_service import (
    CultivationForkSeed,
    CultivationForkTarget,
    CultivationRequest,
    CultivationSeed,
    CultivationSkillTarget,
)


class CultivationBatchTargetCard(QFrame):
    """Keep one stable line identity while its editable target changes."""

    changed = Signal()
    remove_requested = Signal(str)
    fork_requested = Signal(str)
    expanded_changed = Signal()

    def __init__(
        self,
        line_id: str,
        seed: CultivationSeed,
        *,
        avatar_path: object = None,
        fork_icon_lookup: Callable[[str], str | Path | None] | None = None,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self.line_id = line_id
        self.seed = seed
        self._fork_seed = seed.fork
        self._fork_icon_lookup = fork_icon_lookup
        self._skill_inputs: dict[str, tuple[QSpinBox, QSpinBox]] = {}
        self._skill_rows: list[QFrame] = []
        self._skill_column_count: int | None = None
        self._fork_horizontal: bool | None = None
        self._progress_horizontal: bool | None = None
        self._available_width = 0
        self._progress_layouts: list[QBoxLayout] = []
        self._progress_arrows: list[QLabel] = []
        self.setObjectName("cultivationBatchTargetCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._apply_style(False)
        self._build(avatar_path)
        self._connect_changes()
        self._refresh_summary()
        self._reflow_sections()

    @property
    def character_id(self) -> int:
        return self.seed.character_id

    def request(self) -> CultivationRequest:
        return CultivationRequest(
            character_id=self.seed.character_id,
            current_level=self.current_level.value(),
            current_breakthrough_stage=int(self.current_stage.currentData()),
            target_level=self.target_level.value(),
            target_breakthrough_stage=int(self.target_stage.currentData()),
            skills=tuple(
                CultivationSkillTarget(skill_id, current.value(), target.value())
                for skill_id, (current, target) in self._skill_inputs.items()
            ),
            include_character_progression=self.character_toggle.isChecked(),
            include_skills=self.skills_toggle.isChecked(),
            fork=self._fork_target(),
        )

    def set_fork_seed(self, seed: CultivationForkSeed) -> None:
        self._fork_seed = seed
        self._set_fork_visual()
        self.fork_current_level.setValue(seed.current_level)
        _set_stages(
            self.fork_current_stage,
            seed.current_level,
            seed.current_breakthrough_stage,
        )
        self.fork_target_level.setValue(80)
        _set_stages(self.fork_target_stage, 80, 6)
        self.fork_toggle.setChecked(True)
        self._set_fork_enabled(True)
        self._emit_changed()

    def _apply_style(self, expanded: bool) -> None:
        border = "#58a6ff" if expanded else "#30363d"
        self.setStyleSheet(themed_style(
            f"QFrame#cultivationBatchTargetCard{{background:#161b22;border:1px solid {border};"
            "border-radius:9px;}"
            "QFrame#cultivationBatchEditorBody{background:#0d1117;"
            "border-top:1px solid #30363d;border-radius:7px;}"
            "QFrame#cultivationBatchSection{background:#161b22;"
            "border:1px solid #30363d;border-radius:8px;}"
            "QFrame#cultivationBatchCurrentState{background:#0d1117;"
            "border:1px solid #30363d;border-radius:7px;}"
            "QFrame#cultivationBatchTargetState{background:#0d1117;"
            "border:1px solid #30363d;border-radius:7px;}"
            "QFrame#cultivationBatchSkillRow{background:#0d1117;"
            "border:1px solid #30363d;border-radius:7px;}"
            "QFrame#cultivationBatchForkIdentity{background:#0d1117;"
            "border:1px solid #30363d;border-radius:7px;}"
            "QLabel#cultivationBatchForkIcon{background:#21262d;"
            "border:1px solid #484f58;border-radius:7px;color:#8b949e;}"
        ))

    def _build(self, avatar_path: object) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(11, 8, 11, 10)
        root.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(11)
        avatar = QLabel(self)
        avatar.setFixedSize(46, 46)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = asset_pixmap(avatar_path, 44, avatar.devicePixelRatioF())
        if pixmap.isNull():
            avatar.setText(self.seed.character_name[:1])
        else:
            avatar.setPixmap(pixmap)
        header.addWidget(avatar)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        name = QLabel(self.seed.character_name, self)
        name.setStyleSheet(themed_style("color:#f0f6fc;font-size:15px;font-weight:900"))
        copy.addWidget(name)
        self.summary = QLabel(self)
        self.summary.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        copy.addWidget(self.summary)
        header.addLayout(copy, 1)
        self.edit = QToolButton(self)
        self.edit.setText("编辑")
        self.edit.setCheckable(True)
        self.edit.setAccessibleName(f"编辑{self.seed.character_name}养成目标")
        self.remove = QPushButton("删除", self)
        action_size = self.remove.sizeHint()
        self.edit.setFixedSize(action_size)
        self.remove.setFixedSize(action_size)
        header.addWidget(self.edit)
        header.addWidget(self.remove)
        root.addLayout(header)

        self.body = QFrame(self)
        self.body.setObjectName("cultivationBatchEditorBody")
        self.body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        body = QVBoxLayout(self.body)
        body.setContentsMargins(12, 13, 12, 13)
        body.setSpacing(11)
        body.addWidget(self._character_editor())
        body.addWidget(self._skills_editor())
        body.addWidget(self._fork_editor())
        self.body.hide()
        root.addWidget(self.body)

        self.remove.clicked.connect(lambda: self.remove_requested.emit(self.line_id))
        self.edit.toggled.connect(self._set_expanded)

    def _section(self, title: str, subtitle: str) -> tuple[QFrame, QVBoxLayout, QToolButton]:
        panel = QFrame(self.body)
        panel.setObjectName("cultivationBatchSection")
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 13)
        layout.setSpacing(10)
        header = QHBoxLayout()
        heading = QLabel(title, panel)
        heading.setStyleSheet(themed_style("color:#f0f6fc;font-size:14px;font-weight:900"))
        header.addWidget(heading)
        note = QLabel(subtitle, panel)
        note.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        header.addWidget(note)
        header.addStretch(1)
        label = QLabel("参与计算", panel)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        header.addWidget(label)
        toggle = participation_toggle(panel, title)
        header.addWidget(toggle)
        layout.addLayout(header)
        return panel, layout, toggle

    @staticmethod
    def _prepare_progress_control(control: QSpinBox | QComboBox) -> None:
        control.setMinimumWidth(112 if isinstance(control, QSpinBox) else 145)
        control.setMaximumWidth(16_777_215)
        control.setMinimumHeight(38)
        control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _progress_state(
        self, parent: QWidget, label: str, level: QSpinBox, stage: QComboBox,
        *, target: bool,
    ) -> QFrame:
        panel = QFrame(parent)
        panel.setObjectName(
            "cultivationBatchTargetState" if target else "cultivationBatchCurrentState"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(11, 8, 11, 9)
        layout.setSpacing(6)
        caption = QLabel(label, panel)
        caption.setStyleSheet(themed_style("color:#8b949e;font-size:11px;font-weight:700"))
        layout.addWidget(caption)
        fields = QHBoxLayout()
        fields.setSpacing(8)
        for control in (level, stage):
            self._prepare_progress_control(control)
            fields.addWidget(control, 1)
        layout.addLayout(fields)
        return panel

    def _progress_pair(
        self, parent: QWidget,
        current_level: QSpinBox, current_stage: QComboBox,
        target_level: QSpinBox, target_stage: QComboBox,
    ) -> QWidget:
        wrapper = QWidget(parent)
        layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self._progress_state(
            wrapper, "当前状态", current_level, current_stage, target=False,
        ), 1)
        arrow = QLabel("→", wrapper)
        style_progress_arrow(arrow)
        layout.addWidget(arrow)
        self._progress_arrows.append(arrow)
        layout.addWidget(self._progress_state(
            wrapper, "目标状态", target_level, target_stage, target=True,
        ), 1)
        self._progress_layouts.append(layout)
        return wrapper

    def _character_editor(self) -> QFrame:
        panel, layout, self.character_toggle = self._section(
            "角色等级与突破", "人物养成目标",
        )
        self.current_level = level_spinbox(panel)
        self.target_level = level_spinbox(panel)
        self.current_stage = stage_combobox(panel)
        self.target_stage = stage_combobox(panel)
        self.current_level.setValue(self.seed.current_level)
        _set_stages(
            self.current_stage, self.seed.current_level,
            self.seed.current_breakthrough_stage,
        )
        self.target_level.setValue(80)
        _set_stages(self.target_stage, 80, 6)
        layout.addWidget(self._progress_pair(
            panel, self.current_level, self.current_stage,
            self.target_level, self.target_stage,
        ))
        return panel

    def _skills_editor(self) -> QFrame:
        panel, layout, self.skills_toggle = self._section(
            "技能等级", "当前等级 → 目标等级",
        )
        self._skills_grid = QGridLayout()
        self._skills_grid.setContentsMargins(0, 0, 0, 0)
        self._skills_grid.setHorizontalSpacing(10)
        self._skills_grid.setVerticalSpacing(9)
        for skill in self.seed.skills:
            row = QFrame(panel)
            row.setObjectName("cultivationBatchSkillRow")
            line = QHBoxLayout(row)
            line.setContentsMargins(10, 7, 10, 7)
            line.setSpacing(8)
            category = QLabel(skill.category, row)
            style_skill_badge(category)
            line.addWidget(category)
            name = QLabel(skill.name, row)
            name.setToolTip(skill.name)
            name.setWordWrap(True)
            name.setMinimumWidth(0)
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            line.addWidget(name, 1)
            current = skill_spinbox(row)
            target = skill_spinbox(row)
            for control in (current, target):
                control.setFixedWidth(88)
                control.setMinimumHeight(40)
                control.setRange(1, skill.maximum_level)
            current.setValue(skill.current_level)
            target.setValue(skill.maximum_level)
            line.addWidget(current)
            arrow = QLabel("→", row)
            style_progress_arrow(arrow, compact=True)
            line.addWidget(arrow)
            line.addWidget(target)
            self._skill_inputs[skill.skill_id] = (current, target)
            self._skill_rows.append(row)
        layout.addLayout(self._skills_grid)
        return panel

    def _fork_editor(self) -> QFrame:
        panel, layout, self.fork_toggle = self._section(
            "弧盘养成", "选择弧盘并设置目标",
        )
        self.fork_toggle.setChecked(self._fork_seed is not None)
        self._fork_content = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._fork_content.setSpacing(12)
        self._fork_identity = QFrame(panel)
        self._fork_identity.setObjectName("cultivationBatchForkIdentity")
        identity = QHBoxLayout(self._fork_identity)
        identity.setContentsMargins(10, 8, 10, 8)
        identity.setSpacing(11)
        self.fork_icon = QLabel(self._fork_identity)
        self.fork_icon.setObjectName("cultivationBatchForkIcon")
        self.fork_icon.setFixedSize(72, 72)
        self.fork_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity.addWidget(self.fork_icon)
        details = QVBoxLayout()
        self.fork_name = QLabel(self._fork_identity)
        self.fork_name.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:13px;font-weight:800"
        ))
        details.addWidget(self.fork_name)
        self.fork_note = QLabel(self._fork_identity)
        self.fork_note.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        details.addWidget(self.fork_note)
        identity.addLayout(details, 1)
        self.fork_button = QPushButton(self._fork_identity)
        self.fork_button.clicked.connect(lambda: self.fork_requested.emit(self.line_id))
        identity.addWidget(self.fork_button)
        self._fork_content.addWidget(self._fork_identity)
        self.fork_current_level = level_spinbox(panel)
        self.fork_target_level = level_spinbox(panel)
        self.fork_current_stage = stage_combobox(panel)
        self.fork_target_stage = stage_combobox(panel)
        if self._fork_seed is not None:
            self.fork_current_level.setValue(self._fork_seed.current_level)
            _set_stages(
                self.fork_current_stage, self._fork_seed.current_level,
                self._fork_seed.current_breakthrough_stage,
            )
        else:
            self.fork_current_level.setValue(1)
            _set_stages(self.fork_current_stage, 1, 0)
        self.fork_target_level.setValue(80)
        _set_stages(self.fork_target_stage, 80, 6)
        self._fork_progress = self._progress_pair(
            panel, self.fork_current_level, self.fork_current_stage,
            self.fork_target_level, self.fork_target_stage,
        )
        self._fork_content.addWidget(self._fork_progress, 1)
        layout.addLayout(self._fork_content)
        self._set_fork_visual()
        self._set_fork_enabled(self._fork_seed is not None)
        return panel

    def _set_fork_visual(self) -> None:
        seed = self._fork_seed
        self.fork_name.setText(seed.fork_name if seed is not None else "尚未选择弧盘")
        self.fork_note.setText(
            "当前角色使用的弧盘" if seed is not None else "选择弧盘后可设置等级与突破"
        )
        self.fork_button.setText("更换弧盘" if seed is not None else "选择弧盘")
        path = self._fork_icon_lookup(seed.fork_id) if seed and self._fork_icon_lookup else None
        pixmap = asset_pixmap(path, 68, self.fork_icon.devicePixelRatioF())
        self.fork_icon.clear()
        if pixmap.isNull():
            self.fork_icon.setText("弧")
        else:
            self.fork_icon.setPixmap(pixmap)

    def _reflow_sections(self) -> None:
        width = self._available_width or self.width()
        progress_horizontal = width >= 720
        if progress_horizontal != self._progress_horizontal:
            direction = (
                QBoxLayout.Direction.LeftToRight if progress_horizontal
                else QBoxLayout.Direction.TopToBottom
            )
            for layout in self._progress_layouts:
                layout.setDirection(direction)
            for arrow in self._progress_arrows:
                arrow.setText("→" if progress_horizontal else "↓")
            self._progress_horizontal = progress_horizontal
        columns = 4 if width >= 1440 else 2 if width >= 720 else 1
        if columns != self._skill_column_count:
            while self._skills_grid.count():
                self._skills_grid.takeAt(0)
            for index, row in enumerate(self._skill_rows):
                self._skills_grid.addWidget(row, index // columns, index % columns)
            self._skill_column_count = columns
        horizontal = width >= 1000
        if horizontal != self._fork_horizontal:
            direction = (
                QBoxLayout.Direction.LeftToRight if horizontal
                else QBoxLayout.Direction.TopToBottom
            )
            self._fork_content.setDirection(direction)
            self._fork_identity.setMinimumWidth(280 if horizontal else 0)
            self._fork_identity.setMaximumWidth(320 if horizontal else 16_777_215)
            self._fork_horizontal = horizontal

    def update_available_width(self, width: int) -> None:
        self._available_width = max(0, width)
        self._reflow_sections()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._reflow_sections()

    def _connect_changes(self) -> None:
        for spin in (
            self.current_level,
            self.target_level,
            self.fork_current_level,
            self.fork_target_level,
        ):
            spin.valueChanged.connect(self._emit_changed)
        for combo in (
            self.current_stage,
            self.target_stage,
            self.fork_current_stage,
            self.fork_target_stage,
        ):
            combo.currentIndexChanged.connect(self._emit_changed)
        for toggle in (self.character_toggle, self.skills_toggle, self.fork_toggle):
            toggle.toggled.connect(self._emit_changed)
        self.character_toggle.toggled.connect(self._set_character_enabled)
        self.skills_toggle.toggled.connect(self._set_skills_enabled)
        self.fork_toggle.toggled.connect(self._set_fork_enabled)
        self.current_level.valueChanged.connect(
            lambda: _set_stages(self.current_stage, self.current_level.value(), self.current_stage.currentData())
        )
        self.target_level.valueChanged.connect(
            lambda: _set_stages(self.target_stage, self.target_level.value(), self.target_stage.currentData())
        )
        self.fork_current_level.valueChanged.connect(
            lambda: _set_stages(self.fork_current_stage, self.fork_current_level.value(), self.fork_current_stage.currentData())
        )
        self.fork_target_level.valueChanged.connect(
            lambda: _set_stages(self.fork_target_stage, self.fork_target_level.value(), self.fork_target_stage.currentData())
        )
        for current, target in self._skill_inputs.values():
            current.valueChanged.connect(self._emit_changed)
            target.valueChanged.connect(self._emit_changed)

    def _fork_target(self) -> CultivationForkTarget | None:
        if self._fork_seed is None or not self.fork_toggle.isChecked():
            return None
        return CultivationForkTarget(
            self._fork_seed.fork_id,
            self.fork_current_level.value(),
            int(self.fork_current_stage.currentData()),
            self.fork_target_level.value(),
            int(self.fork_target_stage.currentData()),
        )

    def _emit_changed(self, *_args: object) -> None:
        self._refresh_summary()
        self.changed.emit()

    def _refresh_summary(self) -> None:
        parts = [f"角色 {self.current_level.value()}→{self.target_level.value()}"]
        if self.skills_toggle.isChecked():
            parts.append(f"技能 {len(self._skill_inputs)} 项")
        if self._fork_seed is not None and self.fork_toggle.isChecked():
            parts.append(f"弧盘 {self._fork_seed.fork_name}")
        self.summary.setText(" · ".join(parts))

    def _set_expanded(self, expanded: bool) -> None:
        self.edit.setText("收起" if expanded else "编辑")
        self._apply_style(expanded)
        self.body.setVisible(expanded)
        self._reflow_sections()
        self.expanded_changed.emit()

    def _set_character_enabled(self, enabled: bool) -> None:
        for control in (
            self.current_level,
            self.current_stage,
            self.target_level,
            self.target_stage,
        ):
            control.setEnabled(enabled)

    def _set_skills_enabled(self, enabled: bool) -> None:
        for current, target in self._skill_inputs.values():
            current.setEnabled(enabled)
            target.setEnabled(enabled)

    def _set_fork_enabled(self, enabled: bool) -> None:
        active = bool(enabled and self._fork_seed is not None)
        for control in (
            self.fork_current_level,
            self.fork_current_stage,
            self.fork_target_level,
            self.fork_target_stage,
        ):
            control.setEnabled(active)


def _stages_for_level(level: int) -> tuple[int, ...]:
    return tuple(
        stage for stage in range(7)
        if (1 if stage == 0 else (stage + 1) * 10) <= level <= (stage + 2) * 10
    )


def _set_stages(combo: QComboBox, level: int, preferred: object) -> None:
    options = _stages_for_level(level)
    previous = int(preferred) if preferred is not None else None
    combo.blockSignals(True)
    combo.clear()
    for stage in options:
        combo.addItem(f"突破 {stage}", stage)
    selected = previous if previous in options else options[0]
    combo.setCurrentIndex(options.index(selected))
    combo.blockSignals(False)


__all__ = ["CultivationBatchTargetCard"]
