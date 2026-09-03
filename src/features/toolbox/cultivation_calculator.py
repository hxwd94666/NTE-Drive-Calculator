# 实现工具页养成计算器的交互界面。
"""Toolbox dialog for calculating a character's formal cultivation materials."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import theme_color, themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.toolbox.cultivation_selectors import select_cultivation_item
from src.services.character_progression_requirements import (
    MaterialSummaryStatus,
)
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.cultivation_planner_service import (
    CultivationFork,
    CultivationForkSeed,
    CultivationForkTarget,
    CultivationPlan,
    CultivationPlannerService,
    CultivationRequest,
    CultivationRole,
    CultivationSeed,
    CultivationSkillTarget,
)


class _CultivationSpinBox(QSpinBox):
    """Draw stable step chevrons independent of the active Qt platform theme."""

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
            wing_y = center_y - tip_offset
            tip_y = center_y + tip_offset
            painter.drawLine(center_x - 3, wing_y, center_x, tip_y)
            painter.drawLine(center_x, tip_y, center_x + 3, wing_y)
        painter.end()


class _ParticipationToggle(QToolButton):
    """A text-free on/off switch that remains recognizable in every theme."""

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(self.rect()).adjusted(1, 5, -1, -5)
        checked = self.isChecked()
        track_color = theme_color("#238636" if checked else "#30363d")
        border_color = theme_color("#58a6ff" if self.underMouse() else "#30363d")
        painter.setPen(QPen(QColor(border_color), 1.2))
        painter.setBrush(QColor(track_color))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        knob_size = track.height() - 6
        knob_x = (
            track.right() - knob_size - 3
            if checked else track.left() + 3
        )
        knob = QRectF(knob_x, track.top() + 3, knob_size, knob_size)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff" if checked else theme_color("#8b949e")))
        painter.drawEllipse(knob)
        painter.end()


class CultivationCalculatorDialog(QDialog):
    """Editable planning draft; calculation never writes the account or inventory."""

    def __init__(self, service: CultivationPlannerService, parent: QWidget) -> None:
        super().__init__(parent)
        self._service = service
        self._roles: tuple[CultivationRole, ...] = ()
        self._forks: tuple[CultivationFork, ...] = ()
        self._seed: CultivationSeed | None = None
        self._fork_seed: CultivationForkSeed | None = None
        self._skill_inputs: dict[str, tuple[QSpinBox, QSpinBox]] = {}
        self._last_plan: CultivationPlan | None = None
        self._asset_catalog = GameUiAssetCatalog(bundled_game_ui_asset_root())
        self.setWindowTitle("养成计算器")
        self.setObjectName("cultivationCalculatorDialog")
        self.setModal(True)
        self._build()
        fit_dialog_to_available_screen(self, QSize(1180, 740))
        QTimer.singleShot(0, self._load_roles)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        note = QLabel(
            "按角色页已保存的养成状态预填；仅汇总正式材料，不扣除背包库存，也不估算体力。",
            self,
        )
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(note)

        content = QHBoxLayout()
        content.setSpacing(14)
        input_scroll = QScrollArea(self)
        input_scroll.setObjectName("cultivationCalculatorInputScroll")
        input_scroll.setWidgetResizable(True)
        input_scroll.setFrameShape(QFrame.NoFrame)
        input_scroll.setMinimumWidth(410)
        input_body = QWidget(input_scroll)
        input_layout = QVBoxLayout(input_body)
        input_layout.setContentsMargins(2, 2, 2, 2)
        input_layout.setSpacing(10)

        configuration_caption = QLabel("养成目标", input_body)
        configuration_caption.setStyleSheet(themed_style(
            "font-size:14px;font-weight:800;color:#c9d1d9"
        ))
        input_layout.addWidget(configuration_caption)

        controls = QFrame(input_body)
        controls.setObjectName("cultivationCalculatorControls")
        controls.setStyleSheet(themed_style(
            "QFrame#cultivationCalculatorControls{background:#161b22;border:1px solid #30363d;"
            "border-radius:9px;}"
        ))
        grid = QGridLayout(controls)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self._role = QPushButton("选择角色", controls)
        self._role.setObjectName("cultivationCalculatorRoleSelector")
        self._role.setMinimumWidth(210)
        self._role.clicked.connect(self._select_role)
        grid.addWidget(QLabel("角色", controls), 0, 0)
        grid.addWidget(self._role, 0, 1, 1, 2)
        self._character_toggle = _participation_toggle(controls, "角色养成")
        self._character_toggle.toggled.connect(self._set_character_progression_enabled)
        grid.addWidget(self._character_toggle, 0, 3)
        self._current_level = _level_spinbox(controls)
        self._target_level = _level_spinbox(controls)
        self._current_stage = QComboBox(controls)
        self._target_stage = QComboBox(controls)
        self._current_level.valueChanged.connect(self._refresh_current_stages)
        self._target_level.valueChanged.connect(self._refresh_target_stages)
        grid.addWidget(QLabel("当前等级", controls), 1, 0)
        grid.addWidget(self._current_level, 1, 1)
        grid.addWidget(self._current_stage, 1, 2)
        grid.addWidget(QLabel("目标等级", controls), 2, 0)
        grid.addWidget(self._target_level, 2, 1)
        grid.addWidget(self._target_stage, 2, 2)
        input_layout.addWidget(controls)

        self._fork_controls = QFrame(input_body)
        self._fork_controls.setObjectName("cultivationCalculatorForkControls")
        self._fork_controls.setStyleSheet(themed_style(
            "QFrame#cultivationCalculatorForkControls{background:#161b22;border:1px solid #30363d;"
            "border-radius:9px;}"
        ))
        fork_grid = QGridLayout(self._fork_controls)
        fork_grid.setContentsMargins(14, 12, 14, 12)
        fork_grid.setHorizontalSpacing(10)
        fork_grid.setVerticalSpacing(8)
        self._fork = QPushButton("选择弧盘", self._fork_controls)
        self._fork.setObjectName("cultivationCalculatorForkSelector")
        self._fork.setMinimumWidth(210)
        self._fork.clicked.connect(self._select_fork)
        fork_grid.addWidget(QLabel("弧盘", self._fork_controls), 0, 0)
        fork_grid.addWidget(self._fork, 0, 1, 1, 2)
        self._fork_toggle = _participation_toggle(self._fork_controls, "弧盘养成")
        self._fork_toggle.toggled.connect(self._refresh_fork_participation)
        fork_grid.addWidget(self._fork_toggle, 0, 3)
        self._fork_current_level = _level_spinbox(self._fork_controls)
        self._fork_target_level = _level_spinbox(self._fork_controls)
        self._fork_current_stage = QComboBox(self._fork_controls)
        self._fork_target_stage = QComboBox(self._fork_controls)
        self._fork_current_level.valueChanged.connect(self._refresh_fork_current_stages)
        self._fork_target_level.valueChanged.connect(self._refresh_fork_target_stages)
        fork_grid.addWidget(QLabel("当前等级", self._fork_controls), 1, 0)
        fork_grid.addWidget(self._fork_current_level, 1, 1)
        fork_grid.addWidget(self._fork_current_stage, 1, 2)
        fork_grid.addWidget(QLabel("目标等级", self._fork_controls), 2, 0)
        fork_grid.addWidget(self._fork_target_level, 2, 1)
        fork_grid.addWidget(self._fork_target_stage, 2, 2)
        input_layout.addWidget(self._fork_controls)
        self._set_fork_controls_enabled(False)

        skill_header = QHBoxLayout()
        skill_header.setContentsMargins(0, 0, 22, 0)
        skill_caption = QLabel("技能目标", input_body)
        skill_caption.setStyleSheet(themed_style("font-size:14px;font-weight:800;color:#c9d1d9"))
        skill_header.addWidget(skill_caption)
        skill_header.addStretch(1)
        self._skills_toggle = _participation_toggle(input_body, "技能目标")
        self._skills_toggle.toggled.connect(self._set_skills_enabled)
        skill_header.addWidget(self._skills_toggle)
        input_layout.addLayout(skill_header)
        self._skills_box = QWidget(input_body)
        self._skills_layout = QFormLayout(self._skills_box)
        self._skills_layout.setContentsMargins(8, 2, 8, 2)
        self._skills_layout.setHorizontalSpacing(14)
        self._skills_layout.setVerticalSpacing(7)
        input_layout.addWidget(self._skills_box)

        action_row = QHBoxLayout()
        calculate = QPushButton("计算所需材料", input_body)
        calculate.setObjectName("cultivationCalculatorCalculate")
        calculate.setMinimumHeight(44)
        calculate.setStyleSheet(themed_style(
            "QPushButton#cultivationCalculatorCalculate{background:#1f6feb;color:#fff;"
            "border:1px solid #58a6ff;border-radius:7px;font-size:14px;font-weight:800;}"
            "QPushButton#cultivationCalculatorCalculate:hover{background:#388bfd;}"
            "QPushButton#cultivationCalculatorCalculate:pressed{background:#1f6feb;}"
        ))
        calculate.clicked.connect(self._calculate)
        action_row.addWidget(calculate)
        input_layout.addLayout(action_row)
        input_layout.addStretch()
        input_scroll.setWidget(input_body)
        content.addWidget(input_scroll, 4)

        result_panel = QFrame(self)
        result_panel.setObjectName("cultivationCalculatorResultPanel")
        result_panel.setStyleSheet(themed_style(
            "QFrame#cultivationCalculatorResultPanel{background:#161b22;border:1px solid #30363d;"
            "border-radius:9px;}"
        ))
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(14, 12, 14, 12)
        result_layout.setSpacing(8)
        result_caption = QLabel("材料清单", result_panel)
        result_caption.setStyleSheet(themed_style("font-size:15px;font-weight:900;color:#58a6ff"))
        result_layout.addWidget(result_caption)
        self._result = QScrollArea(result_panel)
        self._result.setWidgetResizable(True)
        self._result.setFrameShape(QFrame.StyledPanel)
        self._result_body = QWidget(self._result)
        self._result_layout = QVBoxLayout(self._result_body)
        self._result_layout.setContentsMargins(12, 10, 12, 10)
        self._result_layout.setSpacing(8)
        self._result.setWidget(self._result_body)
        result_layout.addWidget(self._result, 1)
        content.addWidget(result_panel, 6)
        layout.addLayout(content, 1)
        self._set_result_message("选择角色后填写目标等级和技能目标，再计算所需材料。")

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        self._copy_button = buttons.addButton("复制清单", QDialogButtonBox.ActionRole)
        self._copy_button.setEnabled(False)
        self._copy_button.clicked.connect(self._copy_plan)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_roles(self) -> None:
        try:
            self._roles = self._service.list_roles()
        except Exception as exc:
            self._set_result_message(f"读取角色列表失败：{exc}", error=True)
            return
        if self._roles:
            self._load_selected_seed(self._roles[0].character_id)
        else:
            self._set_result_message("当前静态库没有可用于养成计算的角色。", error=True)

    def _select_role(self) -> None:
        selected = select_cultivation_item(
            self,
            title="选择角色",
            description="选择要计算养成材料的角色。角色页已保存的养成状态会自动预填。",
            options=tuple(
                (
                    str(role.character_id),
                    role.name,
                    _asset_path(self._asset_catalog.character_icon(role.character_id)),
                )
                for role in self._roles
            ),
            selected_id=str(self._seed.character_id) if self._seed else None,
        )
        if selected is not None:
            self._load_selected_seed(int(selected))

    def _load_selected_seed(self, character_id: int) -> None:
        try:
            seed = self._service.load_seed(int(character_id))
        except Exception as exc:
            self._set_result_message(f"读取角色养成状态失败：{exc}", error=True)
            return
        self._seed = seed
        self._role.setText(seed.character_name)
        self._current_level.setValue(seed.current_level)
        self._set_stages(self._current_stage, seed.current_level, seed.current_breakthrough_stage)
        self._target_level.setValue(80)
        self._set_stages(self._target_stage, 80, 6)
        self._rebuild_skills(seed)
        self._apply_fork_seed(seed.fork)
        self._last_plan = None
        self._copy_button.setEnabled(False)
        self._set_result_message("已按角色页保存的等级、突破和技能等级预填。")

    def _select_fork(self) -> None:
        try:
            if not self._forks:
                self._forks = self._service.list_forks()
        except Exception as exc:
            QMessageBox.warning(self, "养成计算器", f"读取弧盘列表失败：{exc}")
            return
        selected = select_cultivation_item(
            self,
            title="选择弧盘",
            description="选择要计算养成材料的弧盘。选择后可填写等级和突破前后状态。",
            options=tuple(
                (
                    item.fork_id,
                    item.name,
                    _asset_path(self._asset_catalog.fork_icon(item.fork_id)),
                )
                for item in self._forks
            ),
            selected_id=self._fork_seed.fork_id if self._fork_seed else None,
        )
        if selected is None:
            return
        try:
            self._apply_fork_seed(self._service.load_fork_seed(
                selected,
                character_id=self._seed.character_id if self._seed else None,
            ))
        except Exception as exc:
            QMessageBox.warning(self, "养成计算器", f"读取弧盘养成状态失败：{exc}")

    def _apply_fork_seed(self, seed: CultivationForkSeed | None) -> None:
        self._fork_seed = seed
        self._set_fork_controls_enabled(seed is not None)
        if seed is None:
            self._fork.setText("选择弧盘")
            return
        self._fork.setText(seed.fork_name)
        self._fork_current_level.setValue(seed.current_level)
        self._set_stages(
            self._fork_current_stage,
            seed.current_level,
            seed.current_breakthrough_stage,
        )
        self._fork_target_level.setValue(80)
        self._set_stages(self._fork_target_stage, 80, 6)

    def _set_fork_controls_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled and self._fork_toggle.isChecked())
        for control in (
            self._fork_current_level,
            self._fork_target_level,
            self._fork_current_stage,
            self._fork_target_stage,
        ):
            control.setEnabled(enabled)

    def _set_character_progression_enabled(self, enabled: bool) -> None:
        for control in (
            self._current_level,
            self._target_level,
            self._current_stage,
            self._target_stage,
        ):
            control.setEnabled(enabled)

    def _refresh_fork_participation(self, _enabled: bool) -> None:
        self._set_fork_controls_enabled(self._fork_seed is not None)

    def _set_skills_enabled(self, enabled: bool) -> None:
        for current, target in self._skill_inputs.values():
            current.setEnabled(enabled)
            target.setEnabled(enabled)

    def _rebuild_skills(self, seed: CultivationSeed) -> None:
        while self._skills_layout.rowCount():
            self._skills_layout.removeRow(0)
        self._skill_inputs.clear()
        if not seed.skills:
            self._skills_layout.addRow(QLabel("当前正式静态库未提供可升级技能。", self._skills_box))
            return
        for skill in seed.skills:
            current = _CultivationSpinBox(self._skills_box)
            current.setRange(1, skill.maximum_level)
            current.setValue(skill.current_level)
            target = _CultivationSpinBox(self._skills_box)
            target.setRange(1, skill.maximum_level)
            target.setValue(skill.maximum_level)
            row = QWidget(self._skills_box)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_layout.addWidget(QLabel("当前", row))
            row_layout.addWidget(current)
            row_layout.addWidget(QLabel("目标", row))
            row_layout.addWidget(target)
            row_layout.addStretch()
            self._skills_layout.addRow(f"{skill.category} · {skill.name}", row)
            self._skill_inputs[skill.skill_id] = (current, target)
        self._set_skills_enabled(self._skills_toggle.isChecked())

    def _refresh_current_stages(self) -> None:
        self._set_stages(self._current_stage, self._current_level.value(), self._current_stage.currentData())

    def _refresh_target_stages(self) -> None:
        self._set_stages(self._target_stage, self._target_level.value(), self._target_stage.currentData())

    def _refresh_fork_current_stages(self) -> None:
        self._set_stages(
            self._fork_current_stage,
            self._fork_current_level.value(),
            self._fork_current_stage.currentData(),
        )

    def _refresh_fork_target_stages(self) -> None:
        self._set_stages(
            self._fork_target_stage,
            self._fork_target_level.value(),
            self._fork_target_stage.currentData(),
        )

    @staticmethod
    def _set_stages(combo: QComboBox, level: int, preferred: object) -> None:
        previous = int(preferred) if preferred is not None else None
        options = _stages_for_level(level)
        combo.blockSignals(True)
        combo.clear()
        for stage in options:
            combo.addItem(_stage_label(level, stage), stage)
        selected = previous if previous in options else options[0]
        combo.setCurrentIndex(options.index(selected))
        combo.blockSignals(False)

    def _calculate(self) -> None:
        if self._seed is None:
            return
        request = CultivationRequest(
            character_id=self._seed.character_id,
            current_level=self._current_level.value(),
            current_breakthrough_stage=int(self._current_stage.currentData()),
            target_level=self._target_level.value(),
            target_breakthrough_stage=int(self._target_stage.currentData()),
            skills=tuple(
                CultivationSkillTarget(skill_id, current.value(), target.value())
                for skill_id, (current, target) in self._skill_inputs.items()
            ),
            include_character_progression=self._character_toggle.isChecked(),
            include_skills=self._skills_toggle.isChecked(),
            fork=(
                CultivationForkTarget(
                    fork_id=self._fork_seed.fork_id,
                    current_level=self._fork_current_level.value(),
                    current_breakthrough_stage=int(self._fork_current_stage.currentData()),
                    target_level=self._fork_target_level.value(),
                    target_breakthrough_stage=int(self._fork_target_stage.currentData()),
                )
                if self._fork_seed is not None and self._fork_toggle.isChecked() else None
            ),
        )
        try:
            plan = self._service.calculate(request)
        except ValueError as exc:
            QMessageBox.warning(self, "养成计算器", str(exc))
            return
        except Exception as exc:
            QMessageBox.warning(self, "养成计算器", f"计算材料失败：{exc}")
            return
        self._last_plan = plan
        self._copy_button.setEnabled(True)
        self._render_plan(plan)

    def _render_plan(self, plan: CultivationPlan) -> None:
        self._clear_result()
        complete = plan.status == MaterialSummaryStatus.COMPLETE
        summary = QLabel(
            "材料数据完整" if complete else "材料数据不完整，以下为已识别的材料",
            self._result_body,
        )
        summary.setStyleSheet(themed_style(
            "color:#3fb950;font-weight:800" if complete else "color:#d29922;font-weight:800"
        ))
        self._result_layout.addWidget(summary)
        if plan.required_experience:
            overflow = f"，经验书最小溢出 {plan.experience_overflow:,}" if plan.experience_overflow else ""
            self._result_layout.addWidget(QLabel(
                f"角色升级经验 {plan.required_experience:,}{overflow}", self._result_body
            ))
        for section in plan.sections:
            card = QFrame(self._result_body)
            card.setObjectName("cultivationCalculatorResultSection")
            card.setStyleSheet(themed_style(
                "QFrame#cultivationCalculatorResultSection{background:#161b22;border:1px solid #30363d;"
                "border-radius:8px;}"
            ))
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            heading = QLabel(section.label, card)
            heading.setStyleSheet(themed_style("color:#58a6ff;font-weight:800"))
            card_layout.addWidget(heading)
            material_text = " · ".join(
                f"{material.name} × {material.quantity:,}" for material in section.materials
            ) or "无额外材料"
            values = QLabel(material_text, card)
            values.setWordWrap(True)
            values.setStyleSheet(themed_style("color:#c9d1d9"))
            card_layout.addWidget(values)
            if section.description:
                description = QLabel(section.description, card)
                description.setWordWrap(True)
                description.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
                card_layout.addWidget(description)
            self._result_layout.addWidget(card)
        total = QFrame(self._result_body)
        total.setObjectName("cultivationCalculatorTotals")
        total.setStyleSheet(themed_style(
            "QFrame#cultivationCalculatorTotals{background:#0d1117;border:1px solid #58a6ff;border-radius:8px;}"
        ))
        total_layout = QVBoxLayout(total)
        total_layout.setContentsMargins(10, 8, 10, 8)
        total_heading = QLabel("合计", total)
        total_heading.setStyleSheet(themed_style("color:#58a6ff;font-size:14px;font-weight:900"))
        total_layout.addWidget(total_heading)
        total_text = QLabel(
            " · ".join(f"{item.name} × {item.quantity:,}" for item in plan.totals) or "本次目标没有新增材料",
            total,
        )
        total_text.setWordWrap(True)
        total_layout.addWidget(total_text)
        self._result_layout.addWidget(total)
        if plan.gaps:
            self._result_layout.addWidget(QLabel(
                "部分正式材料数量尚未提供，合计只包含已识别条目。", self._result_body
            ))
        self._result_layout.addStretch()

    def _copy_plan(self) -> None:
        if self._last_plan is None:
            return
        lines = [f"{self._last_plan.character_name} · 养成材料"]
        if self._last_plan.fork_required_experience:
            lines.append(f"弧盘升级经验 × {self._last_plan.fork_required_experience:,}")
        for material in self._last_plan.totals:
            lines.append(f"{material.name} × {material.quantity:,}")
        QApplication.clipboard().setText("\n".join(lines))

    def _set_result_message(self, text: str, *, error: bool = False) -> None:
        self._clear_result()
        message = QLabel(text, self._result_body)
        message.setWordWrap(True)
        message.setStyleSheet(themed_style(
            "color:#f85149" if error else "color:#8b949e"
        ))
        self._result_layout.addWidget(message)
        self._result_layout.addStretch()

    def _clear_result(self) -> None:
        while self._result_layout.count():
            item = self._result_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


def _participation_toggle(parent: QWidget, label: str) -> QToolButton:
    """Return a compact, text-free power toggle with an accessible explanation."""

    toggle = _ParticipationToggle(parent)
    toggle.setObjectName("cultivationParticipationToggle")
    toggle.setCheckable(True)
    toggle.setChecked(True)
    toggle.setToolTip(f"{label}：参与计算（点击关闭）")
    toggle.toggled.connect(lambda enabled: toggle.setToolTip(
        f"{label}：{'参与计算（点击关闭）' if enabled else '不参与计算（点击开启）'}"
    ))
    toggle.setCursor(Qt.PointingHandCursor)
    toggle.setFixedSize(36, 26)
    return toggle


def _level_spinbox(parent: QWidget) -> QSpinBox:
    control = _CultivationSpinBox(parent)
    control.setRange(1, 80)
    control.setSuffix(" 级")
    return control


def _stages_for_level(level: int) -> tuple[int, ...]:
    return tuple(
        stage for stage in range(7)
        if (1 if stage == 0 else (stage + 1) * 10) <= level <= (stage + 2) * 10
    )


def _stage_label(level: int, stage: int) -> str:
    alternatives = _stages_for_level(level)
    if len(alternatives) == 2:
        return f"突破 {stage}（{'突破前' if stage == alternatives[0] else '突破后'}）"
    return f"突破 {stage}"


def _asset_path(value: object) -> str | None:
    return str(value) if value is not None else None


__all__ = ["CultivationCalculatorDialog"]
