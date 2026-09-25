# 实现工具页养成计算器的交互界面。
"""Toolbox page content for calculating a character's formal cultivation materials."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.toolbox.cultivation_owned_materials import (
    CultivationOwnedMaterials,
    build_material_grid,
    remaining_materials,
    visible_materials,
    visible_owned_inputs,
)
from src.features.toolbox.cultivation_selectors import select_cultivation_item
from src.features.toolbox.cultivation_single_editor import CultivationSingleEditor
from src.features.toolbox.cultivation_stamina_ui import (
    CultivationStaminaControls,
    stamina_runs_text,
    stamina_summary_text,
    style_stamina_badge,
)
from src.services.character_progression_requirements import (
    MaterialSummaryStatus,
)
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.cultivation_planner_service import (
    CultivationFork,
    CultivationForkSeed,
    CultivationForkTarget,
    CultivationMaterial,
    CultivationPlan,
    CultivationPlannerService,
    CultivationRequest,
    CultivationRole,
    CultivationSeed,
    CultivationStaminaPlan,
    CultivationSkillTarget,
)


class CultivationCalculatorContent(QWidget):
    """Editable planning draft; calculation never writes the account or inventory."""

    plan_available = Signal(bool)
    layout_changed = Signal()
    calculation_completed = Signal()

    def __init__(
        self,
        service: CultivationPlannerService,
        parent: QWidget,
        *,
        asset_root: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._roles: tuple[CultivationRole, ...] = ()
        self._forks: tuple[CultivationFork, ...] = ()
        self._seed: CultivationSeed | None = None
        self._fork_seed: CultivationForkSeed | None = None
        self._skill_inputs: dict[str, tuple[QSpinBox, QSpinBox]] = {}
        self._last_plan: CultivationPlan | None = None
        self._last_stamina_plan: CultivationStaminaPlan | None = None
        self._materials_dirty = False
        self._material_scope = "stamina"
        self._details_expanded = False
        self._asset_catalog = GameUiAssetCatalog(
            asset_root if asset_root is not None else bundled_game_ui_asset_root()
        )
        self.setObjectName("cultivationCalculatorContent")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        self._build()
        self._load_roles()

    @property
    def owned_materials(self) -> CultivationOwnedMaterials:
        return self._owned_materials

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        input_body = QWidget(self)
        input_layout = QVBoxLayout(input_body)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(10)

        self._editor = CultivationSingleEditor(input_body)
        input_layout.addWidget(self._editor)
        self._role = self._editor.role_button
        self._fork = self._editor.fork_button
        self._character_toggle = self._editor.character_toggle
        self._skills_toggle = self._editor.skills_toggle
        self._fork_toggle = self._editor.fork_toggle
        self._current_level = self._editor.current_level
        self._current_stage = self._editor.current_stage
        self._target_level = self._editor.target_level
        self._target_stage = self._editor.target_stage
        self._fork_current_level = self._editor.fork_current_level
        self._fork_current_stage = self._editor.fork_current_stage
        self._fork_target_level = self._editor.fork_target_level
        self._fork_target_stage = self._editor.fork_target_stage
        self._skill_inputs = self._editor.skill_inputs
        self._role.clicked.connect(self._select_role)
        self._fork.clicked.connect(self._select_fork)
        self._character_toggle.toggled.connect(self._set_character_progression_enabled)
        self._skills_toggle.toggled.connect(self._set_skills_enabled)
        self._fork_toggle.toggled.connect(self._refresh_fork_participation)
        self._current_level.valueChanged.connect(self._refresh_current_stages)
        self._target_level.valueChanged.connect(self._refresh_target_stages)
        self._fork_current_level.valueChanged.connect(self._refresh_fork_current_stages)
        self._fork_target_level.valueChanged.connect(self._refresh_fork_target_stages)
        self._set_fork_controls_enabled(False)

        self._stamina_controls = CultivationStaminaControls(input_body)
        self._stamina_controls.values_changed.connect(self._stamina_inputs_changed)
        input_layout.addWidget(self._stamina_controls)

        self._owned_materials = CultivationOwnedMaterials(
            self._asset_catalog.progression_item_icon,
            input_body,
        )
        self._owned_materials.quantities_changed.connect(
            self._owned_quantities_changed
        )
        self._owned_materials.layout_changed.connect(self.layout_changed)
        input_layout.addWidget(self._owned_materials)

        action_row = QHBoxLayout()
        self._calculate_button = QPushButton("计算所需材料与体力", input_body)
        self._calculate_button.setObjectName("cultivationCalculatorCalculate")
        self._calculate_button.setMinimumHeight(44)
        self._calculate_button.setStyleSheet(themed_style(
            "QPushButton#cultivationCalculatorCalculate{background:#1f6feb;color:#fff;"
            "border:1px solid #58a6ff;border-radius:7px;font-size:14px;font-weight:800;}"
            "QPushButton#cultivationCalculatorCalculate:hover{background:#388bfd;}"
            "QPushButton#cultivationCalculatorCalculate:pressed{background:#1f6feb;}"
        ))
        self._calculate_button.clicked.connect(self._calculate)
        action_row.addWidget(self._calculate_button)
        input_layout.addLayout(action_row)
        layout.addWidget(input_body)

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
        self._result_body = QWidget(result_panel)
        self._result_layout = QVBoxLayout(self._result_body)
        self._result_layout.setContentsMargins(12, 10, 12, 10)
        self._result_layout.setSpacing(8)
        result_layout.addWidget(self._result_body)
        layout.addWidget(result_panel)
        layout.addStretch(1)
        self._set_result_message("选择角色后填写目标等级和技能目标，再计算所需材料。")

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
        self._editor.set_role(
            seed.character_name, self._asset_catalog.character_icon(seed.character_id),
        )
        self._current_level.setValue(seed.current_level)
        self._set_stages(self._current_stage, seed.current_level, seed.current_breakthrough_stage)
        self._target_level.setValue(80)
        self._set_stages(self._target_stage, 80, 6)
        self._rebuild_skills(seed)
        self._apply_fork_seed(seed.fork)
        self._owned_materials.clear_materials()
        self._last_plan = None
        self._last_stamina_plan = None
        self._materials_dirty = False
        self._calculate_button.setText("计算所需材料与体力")
        self.plan_available.emit(False)
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
            self._editor.set_fork(None, None)
            return
        self._editor.set_fork(
            seed.fork_name, self._asset_catalog.fork_icon(seed.fork_id),
        )
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
        self._editor.set_skills(seed)
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
        self._materials_dirty = False
        self._calculate_button.setText("计算所需材料与体力")
        self.plan_available.emit(True)
        self._render_plan(plan)
        self.calculation_completed.emit()

    def _owned_quantities_changed(self) -> None:
        if self._last_plan is not None and not self._materials_dirty:
            self._materials_dirty = True
            self._calculate_button.setText("重新计算所需材料与体力")
            self.plan_available.emit(False)
            self._set_result_message("已有材料已修改，请点击重新计算更新材料与体力。")

    def _stamina_inputs_changed(self) -> None:
        if self._last_plan is not None and not self._materials_dirty:
            self._render_plan(self._last_plan)

    def set_material_scope(self, scope: str) -> None:
        if scope not in {"all", "stamina"} or scope == self._material_scope:
            return
        self._material_scope = scope
        if self._last_plan is None:
            return
        if self._materials_dirty:
            self._owned_materials.set_materials(self._input_options(self._last_plan))
        else:
            self._render_plan(self._last_plan, refresh_stamina=False)

    def _visible(
        self, materials: tuple[CultivationMaterial, ...]
    ) -> tuple[CultivationMaterial, ...]:
        item_ids = (
            getattr(self._last_stamina_plan, "stamina_item_ids", frozenset())
            if self._last_stamina_plan is not None else frozenset()
        )
        return visible_materials(materials, self._material_scope, item_ids)

    def _calculate_stamina(self, plan: CultivationPlan) -> CultivationStaminaPlan | None:
        calculate = getattr(self._service, "calculate_stamina", None)
        if not callable(calculate):
            return None
        hunter_level, identification_level = self._stamina_controls.values()
        try:
            return calculate(
                plan,
                owned_quantities=self._owned_materials.quantities(),
                hunter_level=hunter_level,
                effective_identification_level=identification_level,
            )
        except (TypeError, ValueError):
            return None

    def _input_options(self, plan: CultivationPlan) -> tuple[CultivationMaterial, ...]:
        item_ids = (
            self._last_stamina_plan.stamina_item_ids
            if self._last_stamina_plan is not None else frozenset()
        )
        return visible_owned_inputs(
            plan.owned_inputs or plan.totals, plan.totals,
            self._material_scope, item_ids,
        )

    def _render_plan(
        self, plan: CultivationPlan, *, refresh_stamina: bool = True
    ) -> None:
        self._clear_result()
        if refresh_stamina:
            self._last_stamina_plan = self._calculate_stamina(plan)
        self._owned_materials.set_materials(self._input_options(plan))
        complete = plan.status == MaterialSummaryStatus.COMPLETE
        if not complete:
            summary = QLabel(
                "材料数据不完整，以下为已识别的材料",
                self._result_body,
            )
            summary.setStyleSheet(themed_style(
                "color:#d29922;font-weight:800"
            ))
            self._result_layout.addWidget(summary)
        if plan.required_experience:
            overflow = f"，经验书最小溢出 {plan.experience_overflow:,}" if plan.experience_overflow else ""
            self._result_layout.addWidget(QLabel(
                f"角色升级经验 {plan.required_experience:,}{overflow}", self._result_body
            ))
        if plan.fork_required_experience:
            overflow = (
                f"，材料最小溢出 {plan.fork_experience_overflow:,}"
                if plan.fork_experience_overflow else ""
            )
            self._result_layout.addWidget(QLabel(
                f"弧盘升级经验 {plan.fork_required_experience:,}{overflow}",
                self._result_body,
            ))
        total = QFrame(self._result_body)
        total.setObjectName("cultivationCalculatorTotals")
        total.setStyleSheet(themed_style(
            "QFrame#cultivationCalculatorTotals{background:#0d1117;border:1px solid #58a6ff;border-radius:8px;}"
        ))
        total_layout = QVBoxLayout(total)
        total_layout.setContentsMargins(10, 8, 10, 8)
        owned = self._owned_materials.quantities()
        visible_totals = self._visible(plan.totals)
        remaining = remaining_materials(visible_totals, owned)
        total_header = QHBoxLayout()
        total_heading = QLabel("仍需合计", total)
        total_heading.setStyleSheet(themed_style("color:#58a6ff;font-size:14px;font-weight:900"))
        total_header.addWidget(total_heading)
        total_header.addStretch(1)
        total_stamina = QLabel(
            stamina_summary_text(
                self._last_stamina_plan.total if self._last_stamina_plan else None
            ),
            total,
        )
        style_stamina_badge(total_stamina)
        total_header.addWidget(total_stamina)
        total_layout.addLayout(total_header)
        if remaining:
            total_grid = build_material_grid(
                remaining,
                icon_lookup=self._asset_catalog.progression_item_icon,
                parent=total,
            )
            total_grid.layout_changed.connect(self.layout_changed)
            total_layout.addWidget(total_grid)
        elif visible_totals:
            total_layout.addWidget(QLabel("已有材料已覆盖全部需求", total))
        elif self._material_scope == "stamina":
            total_layout.addWidget(QLabel("本次目标没有需消耗体力刷取的材料", total))
        else:
            total_layout.addWidget(QLabel("本次目标没有新增材料", total))
        total_runs = stamina_runs_text(
            self._last_stamina_plan.total if self._last_stamina_plan else None
        )
        if total_runs:
            run_label = QLabel(total_runs, total)
            run_label.setWordWrap(True)
            run_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
            total_layout.addWidget(run_label)
        self._result_layout.addWidget(total, 0, Qt.AlignmentFlag.AlignTop)
        if plan.gaps:
            self._result_layout.addWidget(QLabel(
                "部分正式材料数量尚未提供，合计只包含已识别条目。", self._result_body
            ))
        if plan.sections:
            self._result_layout.addWidget(
                self._details_panel(plan, self._last_stamina_plan),
                0,
                Qt.AlignmentFlag.AlignTop,
            )
        self._result_layout.addStretch()
        self.layout_changed.emit()

    def _details_panel(
        self,
        plan: CultivationPlan,
        stamina_plan: CultivationStaminaPlan | None,
    ) -> QFrame:
        panel = QFrame(self._result_body)
        panel.setObjectName("cultivationCalculatorDetailsPanel")
        panel.setStyleSheet(themed_style(
            "QFrame#cultivationCalculatorDetailsPanel{background:#0d1117;"
            "border:1px solid #30363d;border-radius:8px;}"
            "QToolButton#cultivationCalculatorDetailsToggle{border:0;"
            "padding:9px;text-align:left;color:#c9d1d9;font-weight:800;}"
        ))
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        toggle = QToolButton(panel)
        toggle.setObjectName("cultivationCalculatorDetailsToggle")
        toggle.setText(f"计算明细 · {len(plan.sections)}项")
        toggle.setCheckable(True)
        toggle.setChecked(self._details_expanded)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(toggle)
        content = QWidget(panel)
        content.setObjectName("cultivationCalculatorDetailsContent")
        content.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 4, 10, 10)
        content_layout.setSpacing(8)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        for index, section in enumerate(plan.sections):
            card = QFrame(content)
            card.setObjectName("cultivationCalculatorResultSection")
            card.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Maximum,
            )
            card.setStyleSheet(themed_style(
                "QFrame#cultivationCalculatorResultSection{background:#161b22;"
                "border:1px solid #30363d;border-radius:8px;}"
            ))
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            heading_row = QHBoxLayout()
            heading = QLabel(section.label, card)
            heading.setStyleSheet(themed_style("color:#58a6ff;font-weight:800"))
            heading_row.addWidget(heading)
            heading_row.addStretch(1)
            section_stamina = (
                stamina_plan.sections[index].result
                if stamina_plan is not None and index < len(stamina_plan.sections)
                else None
            )
            stamina = QLabel(stamina_summary_text(section_stamina), card)
            style_stamina_badge(stamina)
            heading_row.addWidget(stamina)
            card_layout.addLayout(heading_row)
            section_materials = self._visible(section.materials)
            if section_materials:
                grid = build_material_grid(
                    section_materials,
                    icon_lookup=self._asset_catalog.progression_item_icon,
                    parent=card,
                    minimum_card_width=118,
                )
                grid.layout_changed.connect(self.layout_changed)
                card_layout.addWidget(grid)
            else:
                values = QLabel(
                    "本模块没有需消耗体力刷取的材料"
                    if self._material_scope == "stamina" else "无额外材料",
                    card,
                )
                values.setStyleSheet(themed_style("color:#8b949e"))
                card_layout.addWidget(values)
            if section.description:
                description = QLabel(section.description, card)
                description.setWordWrap(True)
                description.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
                card_layout.addWidget(description)
            runs = stamina_runs_text(section_stamina)
            if runs:
                run_label = QLabel(runs, card)
                run_label.setWordWrap(True)
                run_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
                card_layout.addWidget(run_label)
            content_layout.addWidget(card)
        layout.addWidget(content)
        toggle.toggled.connect(
            lambda expanded: self._set_details_expanded(expanded, toggle, content)
        )
        self._set_details_expanded(self._details_expanded, toggle, content)
        return panel

    def _set_details_expanded(
        self,
        expanded: bool,
        toggle: QToolButton,
        content: QWidget,
    ) -> None:
        self._details_expanded = bool(expanded)
        toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        content.setVisible(expanded)
        content.updateGeometry()
        toggle.parentWidget().updateGeometry()
        self.layout_changed.emit()

    def copy_plan(self) -> None:
        if self._last_plan is None or self._materials_dirty:
            return
        lines = [f"{self._last_plan.character_name} · 养成材料"]
        if self._last_plan.fork_required_experience:
            lines.append(f"弧盘升级经验 × {self._last_plan.fork_required_experience:,}")
        for material in remaining_materials(
            self._visible(self._last_plan.totals),
            self._owned_materials.quantities(),
        ):
            lines.append(f"{material.name} × {material.quantity:,}")
        QApplication.clipboard().setText("\n".join(lines))

    def reset_draft(self) -> None:
        """Restore the selected role's saved state and clear transient results."""

        self._details_expanded = False
        self._last_plan = None
        self._last_stamina_plan = None
        self._materials_dirty = False
        self._calculate_button.setText("计算所需材料与体力")
        self.plan_available.emit(False)
        if self._seed is not None:
            self._load_selected_seed(self._seed.character_id)
        elif self._roles:
            self._load_selected_seed(self._roles[0].character_id)
        else:
            self._set_result_message("正在读取可用于养成计算的角色。")

    def _set_result_message(self, text: str, *, error: bool = False) -> None:
        self._clear_result()
        message = QLabel(text, self._result_body)
        message.setWordWrap(True)
        message.setStyleSheet(themed_style(
            "color:#f85149" if error else "color:#8b949e"
        ))
        self._result_layout.addWidget(message)
        self._result_layout.addStretch()
        self.layout_changed.emit()

    def _clear_result(self) -> None:
        while self._result_layout.count():
            item = self._result_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()


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


__all__ = ["CultivationCalculatorContent"]
