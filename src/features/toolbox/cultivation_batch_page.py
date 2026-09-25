# 实现工具页多角色养成目标、共享材料与合并体力结果。
"""Batch cultivation UI embedded beside the single-target calculator."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.toolbox.cultivation_batch_controller import (
    CultivationBatchController,
)
from src.features.toolbox.cultivation_batch_target import (
    CultivationBatchTargetCard,
)
from src.features.toolbox.cultivation_owned_materials import (
    CultivationOwnedMaterials,
    build_material_grid,
    visible_materials,
    visible_owned_inputs,
)
from src.features.toolbox.cultivation_selectors import (
    select_cultivation_item,
    select_cultivation_items,
)
from src.features.toolbox.cultivation_stamina_ui import (
    CultivationStaminaControls,
    stamina_runs_text,
    stamina_summary_text,
    style_stamina_badge,
)
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.services.cultivation_batch_planner_service import (
    CultivationBatchPlan,
    CultivationBatchPlannerService,
    CultivationBatchRequest,
    CultivationMaterialSource,
    CultivationTargetDraft,
    CultivationTargetPlan,
)
from src.services.cultivation_planner_service import (
    CultivationFork,
    CultivationMaterial,
    CultivationPlannerService,
    CultivationRole,
    CultivationSeed,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.utils.cultivation_trace import trace_cultivation


class CultivationBatchContent(QWidget):
    """Own an ordered multi-target draft for the current account context."""

    plan_available = Signal(bool)
    layout_changed = Signal()
    result_replaced = Signal(int, object)
    result_view_requested = Signal()

    def __init__(
        self,
        service: CultivationPlannerService,
        *,
        context_identity: Callable[[], object] | None,
        parent: QWidget,
        asset_root: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._context_identity = context_identity
        self._batch_service = CultivationBatchPlannerService(service)
        self._controller = CultivationBatchController(
            self._batch_service,
            context_identity=context_identity,
            parent=self,
        )
        self._roles: tuple[CultivationRole, ...] = ()
        self._forks: tuple[CultivationFork, ...] = ()
        self._cards: list[CultivationBatchTargetCard] = []
        self._line_sequence = 0
        self._trace_sequence = 0
        self._active_trace_id = 0
        self._last_plan: CultivationBatchPlan | None = None
        self._last_materials: tuple[CultivationMaterial, ...] = ()
        self._last_input_options: tuple[CultivationMaterial, ...] = ()
        self._last_stamina_item_ids: frozenset[str] = frozenset()
        self._has_calculated = False
        self._materials_dirty = False
        self._material_scope = "stamina"
        self._expanded_results: set[str] = set()
        self._asset_catalog = GameUiAssetCatalog(
            asset_root if asset_root is not None else bundled_game_ui_asset_root()
        )
        self._recalculate_timer = QTimer(self)
        self._recalculate_timer.setSingleShot(True)
        self._recalculate_timer.setInterval(250)
        self._recalculate_timer.timeout.connect(self.calculate)
        self._build()
        self._connect_controller()
        self._load_roles()

    @property
    def owned_materials(self) -> CultivationOwnedMaterials:
        return self._owned_materials

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(10)
        target_panel = QFrame(self)
        target_panel.setObjectName("cultivationBatchTargets")
        target_panel.setStyleSheet(themed_style(
            "QFrame#cultivationBatchTargets{background:#0d1117;"
            "border:1px solid #30363d;border-radius:9px;}"
        ))
        target_root = QVBoxLayout(target_panel)
        target_root.setContentsMargins(12, 10, 12, 12)
        target_root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("角色目标", target_panel)
        title.setStyleSheet(themed_style("color:#c9d1d9;font-size:14px;font-weight:900"))
        header.addWidget(title)
        self._count = QLabel("已选 0 名", target_panel)
        self._count.setStyleSheet(themed_style("color:#8b949e"))
        header.addWidget(self._count)
        header.addStretch(1)
        self._add_button = QPushButton("选择角色", target_panel)
        self._add_button.setObjectName("cultivationBatchAddRole")
        self._add_button.clicked.connect(self._select_role)
        header.addWidget(self._add_button)
        target_root.addLayout(header)
        self._empty = QLabel("选择角色后，可展开编辑每个角色的等级、技能和弧盘目标。", target_panel)
        self._empty.setStyleSheet(themed_style("color:#8b949e"))
        target_root.addWidget(self._empty)
        self._cards_host = QWidget(target_panel)
        self._cards_host.installEventFilter(self)
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        self._cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        target_root.addWidget(self._cards_host)
        root.addWidget(target_panel)

        self._stamina_controls = CultivationStaminaControls(self)
        self._stamina_controls.values_changed.connect(self._draft_changed)
        root.addWidget(self._stamina_controls)
        self._owned_materials = CultivationOwnedMaterials(
            self._asset_catalog.progression_item_icon,
            self,
        )
        self._owned_materials.quantities_changed.connect(self._owned_quantities_changed)
        self._owned_materials.layout_changed.connect(self.layout_changed)
        root.addWidget(self._owned_materials)
        self._calculate_button = QPushButton("计算多角色材料与体力", self)
        self._calculate_button.setObjectName("cultivationBatchCalculate")
        self._calculate_button.setMinimumHeight(44)
        self._calculate_button.setStyleSheet(themed_style(
            "QPushButton#cultivationBatchCalculate{background:#1f6feb;color:#fff;"
            "border:1px solid #58a6ff;border-radius:7px;font-weight:900;}"
            "QPushButton#cultivationBatchCalculate:disabled{background:#21262d;color:#6e7681;}"
        ))
        self._calculate_button.clicked.connect(self.calculate)
        root.addWidget(self._calculate_button)

        self._result = QFrame(self)
        self._result.setObjectName("cultivationBatchResult")
        self._result.setStyleSheet(themed_style(
            "QFrame#cultivationBatchResult{background:#161b22;"
            "border:1px solid #30363d;border-radius:9px;}"
        ))
        self._result_layout = QVBoxLayout(self._result)
        self._result_layout.setContentsMargins(14, 12, 14, 12)
        self._result_layout.setSpacing(8)
        root.addWidget(self._result)
        root.addStretch(1)
        self._set_result_message("选择角色目标后计算跨角色合计。")

    def _connect_controller(self) -> None:
        self._controller.result_ready.connect(self._receive_plan)
        self._controller.error.connect(self._calculation_error)
        self._controller.busy_changed.connect(self._set_busy)

    def _load_roles(self) -> None:
        try:
            self._roles = self._service.list_roles()
        except Exception as exc:
            self._set_result_message(f"读取角色列表失败：{exc}", error=True)
        self._refresh_target_state()

    def _select_role(self) -> None:
        options = tuple(
            (
                str(role.character_id),
                role.name,
                _path(self._asset_catalog.character_icon(role.character_id)),
            )
            for role in self._roles
        )
        selected = select_cultivation_items(
            self,
            title="选择角色目标",
            description="勾选本次需要计算的全部角色；取消勾选会移除对应目标。已有目标保留原顺序，新目标依角色列表顺序加入。",
            options=options,
            selected_ids=tuple(str(card.character_id) for card in self._cards),
        )
        if selected is not None:
            self._apply_role_selection(tuple(int(value) for value in selected))

    def _apply_role_selection(self, selected_ids: tuple[int, ...]) -> None:
        requested = tuple(dict.fromkeys(selected_ids))
        known = {role.character_id for role in self._roles}
        if any(character_id not in known for character_id in requested):
            QMessageBox.warning(self, "多角色养成", "角色列表已变化，请重新选择。")
            return
        retained = set(requested)
        existing = {card.character_id for card in self._cards}
        if retained == existing:
            return
        seeds = {}
        try:
            for character_id in requested:
                if character_id not in existing:
                    seeds[character_id] = self._service.load_seed(character_id)
        except Exception as exc:
            QMessageBox.warning(self, "多角色养成", f"读取角色养成状态失败：{exc}")
            return
        for card in tuple(self._cards):
            if card.character_id not in retained:
                self._cards.remove(card)
                self._cards_layout.removeWidget(card)
                self._expanded_results.discard(card.line_id)
                card.deleteLater()
        for character_id in requested:
            if character_id in seeds:
                self._append_target(seeds[character_id])
        self._refresh_target_state()
        self._draft_changed()

    def _add_role_by_id(self, character_id: int) -> None:
        if any(card.character_id == character_id for card in self._cards):
            return
        try:
            seed = self._service.load_seed(character_id)
        except Exception as exc:
            QMessageBox.warning(self, "多角色养成", f"读取角色养成状态失败：{exc}")
            return
        self._append_target(seed)
        self._refresh_target_state()
        self._draft_changed()

    def _append_target(self, seed: CultivationSeed) -> None:
        self._line_sequence += 1
        card = CultivationBatchTargetCard(
            f"target-{self._line_sequence}",
            seed,
            avatar_path=self._asset_catalog.character_icon(seed.character_id),
            fork_icon_lookup=self._asset_catalog.fork_icon,
            parent=self._cards_host,
        )
        card.changed.connect(self._draft_changed)
        card.remove_requested.connect(self._remove_target)
        card.fork_requested.connect(self._select_fork)
        card.expanded_changed.connect(lambda: self._target_expanded(card))
        self._cards.append(card)
        self._cards_layout.addWidget(card)
        card.update_available_width(self._cards_host.width())

    def _target_expanded(self, active: CultivationBatchTargetCard) -> None:
        if active.edit.isChecked():
            for card in self._cards:
                if card is not active and card.edit.isChecked():
                    card.edit.setChecked(False)
        QTimer.singleShot(0, self, self._refresh_card_layouts)
        self.layout_changed.emit()

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self._cards_host and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self, self._refresh_card_layouts)
        return super().eventFilter(watched, event)

    def _refresh_card_layouts(self) -> None:
        width = self._cards_host.contentsRect().width()
        for card in self._cards:
            card.update_available_width(width)

    def _remove_target(self, line_id: str) -> None:
        card = self._card(line_id)
        if card is None:
            return
        self._cards.remove(card)
        self._cards_layout.removeWidget(card)
        card.deleteLater()
        self._expanded_results.discard(line_id)
        self._refresh_target_state()
        self._draft_changed()

    def _select_fork(self, line_id: str) -> None:
        card = self._card(line_id)
        if card is None:
            return
        try:
            if not self._forks:
                self._forks = self._service.list_forks()
        except Exception as exc:
            QMessageBox.warning(self, "多角色养成", f"读取弧盘列表失败：{exc}")
            return
        selected = select_cultivation_item(
            self,
            title="选择弧盘",
            description=f"为 {card.seed.character_name} 选择养成弧盘。",
            options=tuple(
                (
                    item.fork_id,
                    item.name,
                    _path(self._asset_catalog.fork_icon(item.fork_id)),
                )
                for item in self._forks
            ),
            selected_id=card.request().fork.fork_id if card.request().fork else None,
        )
        if selected is None:
            return
        try:
            card.set_fork_seed(self._service.load_fork_seed(
                selected,
                character_id=card.character_id,
            ))
        except Exception as exc:
            QMessageBox.warning(self, "多角色养成", f"读取弧盘养成状态失败：{exc}")

    def calculate(self) -> None:
        if not self._cards:
            self._set_result_message("请先选择至少一个角色目标。")
            return
        identity = self._identity()
        self._trace_sequence += 1
        self._active_trace_id = self._trace_sequence
        request = self._build_request(identity)
        self._has_calculated = True
        trace_cultivation(request.trace_id, "ui.submit", targets=len(request.ordered_targets), owned_fields=len(request.owned_quantities))
        self._controller.submit(request, identity)

    def _build_request(self, identity: object) -> CultivationBatchRequest:
        account_id, generation, dataset = _identity_fields(identity)
        hunter, identification = self._stamina_controls.values()
        return CultivationBatchRequest(
            account_id=account_id,
            generation=generation,
            dataset_identity=dataset,
            hunter_level=hunter,
            effective_identification_level=identification,
            ordered_targets=tuple(
                CultivationTargetDraft(card.line_id, card.character_id, card.request())
                for card in self._cards
            ),
            owned_quantities=tuple(sorted(self._owned_materials.quantities().items())),
            trace_id=self._active_trace_id,
        )

    def _receive_plan(self, value: object) -> None:
        if not isinstance(value, CultivationBatchPlan):
            return
        self._active_trace_id = value.trace_id
        trace_cultivation(value.trace_id, "ui.result_received", targets=len(value.target_plans))
        self._last_plan = value
        self._last_materials = value.merged_totals
        self._last_input_options = value.owned_inputs or value.merged_totals
        self._last_stamina_item_ids = value.stamina_item_ids
        self._owned_materials.set_materials(self._visible_inputs())
        trace_cultivation(value.trace_id, "ui.owned_inputs_updated")
        self._materials_dirty = False
        if self._calculate_button.isEnabled():
            self._calculate_button.setText("计算多角色材料与体力")
        self.plan_available.emit(True)
        self._render_plan(value)
        trace_cultivation(value.trace_id, "ui.result_rendered")
        trace_cultivation(value.trace_id, "ui.scroll_requested")
        self.result_view_requested.emit()

    def _calculation_error(self, message: str) -> None:
        trace_cultivation(self._active_trace_id, "ui.worker_error")
        self._set_result_message(f"计算多角色材料失败：{message}", error=True)
        self.result_view_requested.emit()

    def _set_busy(self, busy: bool) -> None:
        self._calculate_button.setEnabled(not busy)
        if not busy and self._last_plan is not None and self.isVisible():
            self._calculate_button.setFocus(Qt.FocusReason.OtherFocusReason)
        self._calculate_button.setText(
            "正在计算中" if busy else (
                "重新计算多角色材料与体力" if self._materials_dirty
                else "计算多角色材料与体力"
            )
        )

    def _owned_quantities_changed(self) -> None:
        if not self._has_calculated:
            return
        self._recalculate_timer.stop()
        self._controller.invalidate()
        if not self._materials_dirty:
            self._materials_dirty = True
            self._last_plan = None
            self.plan_available.emit(False)
            self._set_result_message("已有材料已修改，请点击重新计算更新材料与体力。")
        if self._calculate_button.isEnabled():
            self._calculate_button.setText("重新计算多角色材料与体力")

    def _draft_changed(self, *_args: object) -> None:
        if not self._cards:
            self._recalculate_timer.stop()
            self._controller.invalidate()
            self._has_calculated = False
            self._materials_dirty = False
            self._last_plan = None
            self._last_materials = ()
            self._last_input_options = ()
            self._last_stamina_item_ids = frozenset()
            self._owned_materials.clear_materials()
            self.plan_available.emit(False)
            if self._calculate_button.isEnabled():
                self._calculate_button.setText("计算多角色材料与体力")
            self._set_result_message("选择角色目标后计算跨角色合计。")
            return
        if self._has_calculated:
            self._controller.invalidate()
            if not self._materials_dirty:
                self._recalculate_timer.start()
        self.layout_changed.emit()

    def set_material_scope(self, scope: str) -> None:
        if scope not in {"all", "stamina"} or scope == self._material_scope:
            return
        self._material_scope = scope
        self._owned_materials.set_materials(self._visible_inputs())
        if self._last_plan is not None and not self._materials_dirty:
            self._render_plan(self._last_plan)

    def _visible(
        self, materials: tuple[CultivationMaterial, ...]
    ) -> tuple[CultivationMaterial, ...]:
        return visible_materials(
            materials, self._material_scope, self._last_stamina_item_ids
        )

    def _visible_inputs(self) -> tuple[CultivationMaterial, ...]:
        return visible_owned_inputs(
            self._last_input_options,
            self._last_materials,
            self._material_scope,
            self._last_stamina_item_ids,
        )

    def _render_plan(self, plan: CultivationBatchPlan) -> None:
        trace_cultivation(plan.trace_id, "ui.render_begin")
        retired = self._clear_result()
        self._result_layout.addWidget(self._combined_panel(plan))
        ledger = _ledger_index(plan.source_ledger)
        for target in plan.target_plans:
            self._result_layout.addWidget(self._target_result(target, ledger))
        trace_cultivation(plan.trace_id, "ui.result_widgets_attached", targets=len(plan.target_plans))
        self._result_layout.addStretch()
        self.result_replaced.emit(plan.trace_id, retired)
        self.layout_changed.emit()

    def _combined_panel(self, plan: CultivationBatchPlan) -> QFrame:
        panel = QFrame(self._result)
        panel.setObjectName("cultivationBatchCombinedTotals")
        panel.setStyleSheet(themed_style(
            "QFrame#cultivationBatchCombinedTotals{background:#0d1117;"
            "border:1px solid #58a6ff;border-radius:8px;}"
        ))
        layout = QVBoxLayout(panel)
        header = QHBoxLayout()
        title = QLabel("跨角色仍需合计", panel)
        title.setStyleSheet(themed_style("color:#58a6ff;font-size:15px;font-weight:900"))
        header.addWidget(title)
        header.addWidget(QLabel("合并副本掉落已去重", panel))
        header.addStretch(1)
        badge = QLabel(stamina_summary_text(plan.combined_stamina), panel)
        style_stamina_badge(badge)
        header.addWidget(badge)
        layout.addLayout(header)
        if plan.gaps:
            warning = QLabel(
                f"存在 {len(plan.gaps)} 项正式数据缺口，合计仅包含已识别材料。",
                panel,
            )
            warning.setStyleSheet(themed_style("color:#d29922;font-weight:800"))
            layout.addWidget(warning)
        if plan.saved_stamina:
            saved = QLabel(f"相比分角色分别刷取，合并方案节省 {plan.saved_stamina:,} 体力", panel)
            saved.setStyleSheet(themed_style("color:#3fb950;font-weight:800"))
            layout.addWidget(saved)
        remaining_totals = self._visible(plan.remaining_totals)
        if remaining_totals:
            grid = build_material_grid(
                remaining_totals,
                icon_lookup=self._asset_catalog.progression_item_icon,
                parent=panel,
            )
            grid.layout_changed.connect(self.layout_changed)
            layout.addWidget(grid)
        else:
            message = (
                "本次目标没有需消耗体力刷取的材料"
                if self._material_scope == "stamina" and not self._visible(plan.merged_totals)
                else "已有材料已覆盖全部角色需求"
            )
            layout.addWidget(QLabel(message, panel))
        runs = stamina_runs_text(plan.combined_stamina)
        if runs:
            layout.addWidget(_muted_label(runs, panel))
        return panel

    def _target_result(
        self,
        target: CultivationTargetPlan,
        ledger: dict[tuple[str, int], tuple[CultivationMaterialSource, ...]],
    ) -> QFrame:
        panel = QFrame(self._result)
        panel.setObjectName("cultivationBatchTargetResult")
        panel.setStyleSheet(themed_style(
            "QFrame#cultivationBatchTargetResult{background:#0d1117;"
            "border:1px solid #30363d;border-radius:8px;}"
        ))
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        toggle = QToolButton(panel)
        toggle.setObjectName("cultivationBatchTargetResultToggle")
        toggle.setText(f"{target.plan.character_name} · {len(target.plan.sections)} 项明细")
        toggle.setCheckable(True)
        toggle.setChecked(target.line_id in self._expanded_results)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        root.addWidget(toggle)
        content = QWidget(panel)
        content.setObjectName("cultivationBatchTargetResultContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 4, 10, 10)
        layout.setSpacing(8)
        summary = QHBoxLayout()
        summary.addWidget(QLabel("该角色仍需", content))
        summary.addStretch(1)
        badge = QLabel(stamina_summary_text(target.stamina.total), content)
        style_stamina_badge(badge)
        summary.addWidget(badge)
        layout.addLayout(summary)
        root.addWidget(content)
        details_built = False

        def show_details(expanded: bool) -> None:
            nonlocal details_built
            if expanded and not details_built:
                details_built = True
                self._populate_target_details(content, layout, target, ledger)
            self._toggle_result(target.line_id, toggle, content, expanded)

        toggle.toggled.connect(show_details)
        show_details(toggle.isChecked())
        return panel

    def _populate_target_details(
        self,
        content: QWidget,
        layout: QVBoxLayout,
        target: CultivationTargetPlan,
        ledger: dict[tuple[str, int], tuple[CultivationMaterialSource, ...]],
    ) -> None:
        """Build large material grids only when this result is opened."""

        remaining_totals = self._visible(target.remaining_totals)
        if remaining_totals:
            grid = build_material_grid(
                remaining_totals,
                icon_lookup=self._asset_catalog.progression_item_icon,
                parent=content,
            )
            grid.layout_changed.connect(self.layout_changed)
            layout.addWidget(grid)
        for index, section in enumerate(target.plan.sections):
            layout.addWidget(self._section_result(
                target,
                index,
                section.label,
                section.materials,
                ledger.get((target.line_id, index), ()),
            ))

    def _section_result(
        self,
        target: CultivationTargetPlan,
        index: int,
        label: str,
        materials: tuple[CultivationMaterial, ...],
        rows: tuple[CultivationMaterialSource, ...],
    ) -> QFrame:
        card = QFrame(self._result)
        card.setObjectName("cultivationBatchSectionResult")
        card.setStyleSheet(themed_style(
            "QFrame#cultivationBatchSectionResult{background:#161b22;"
            "border:1px solid #30363d;border-radius:7px;}"
        ))
        layout = QVBoxLayout(card)
        header = QHBoxLayout()
        title = QLabel(label, card)
        title.setStyleSheet(themed_style("color:#58a6ff;font-weight:800"))
        header.addWidget(title)
        header.addStretch(1)
        stamina = (
            target.stamina.sections[index].result
            if index < len(target.stamina.sections) else None
        )
        badge = QLabel(stamina_summary_text(stamina), card)
        style_stamina_badge(badge)
        header.addWidget(badge)
        layout.addLayout(header)
        materials = self._visible(materials)
        remaining_by_id = {row.item_id: row.remaining_quantity for row in rows}
        remaining = tuple(
            _quantity(material, remaining_by_id.get(material.item_id, material.quantity))
            for material in materials
            if remaining_by_id.get(material.item_id, material.quantity) > 0
        )
        if remaining:
            grid = build_material_grid(
                remaining,
                icon_lookup=self._asset_catalog.progression_item_icon,
                parent=card,
                minimum_card_width=118,
            )
            grid.layout_changed.connect(self.layout_changed)
            layout.addWidget(grid)
        else:
            message = (
                "本模块没有需消耗体力刷取的材料"
                if self._material_scope == "stamina" and not materials
                else "已有材料已覆盖该模块"
            )
            layout.addWidget(_muted_label(message, card))
        allocated = [
            f"{material.name} × {row.allocated_owned:,}"
            for material in materials
            for row in rows
            if material.item_id == row.item_id and row.allocated_owned
        ]
        if allocated:
            layout.addWidget(_muted_label("已有分配：" + "；".join(allocated), card))
        runs = stamina_runs_text(stamina)
        if runs:
            layout.addWidget(_muted_label(runs, card))
        return card

    def _toggle_result(
        self,
        line_id: str,
        toggle: QToolButton,
        content: QWidget,
        expanded: bool,
    ) -> None:
        if expanded:
            self._expanded_results.add(line_id)
        else:
            self._expanded_results.discard(line_id)
        toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        content.setVisible(expanded)
        self.layout_changed.emit()

    def copy_plan(self) -> None:
        plan = self._last_plan
        if plan is None:
            return
        lines = [f"多角色养成材料 · {len(plan.target_plans)} 个角色"]
        for material in self._visible(plan.remaining_totals):
            lines.append(f"{material.name} × {material.quantity:,}")
        if plan.combined_stamina.total_stamina is not None:
            lines.append(f"最低体力 × {plan.combined_stamina.total_stamina:,}")
        QApplication.clipboard().setText("\n".join(lines))

    def reset_draft(self) -> None:
        self._recalculate_timer.stop()
        self._controller.invalidate()
        self._has_calculated = False
        self._materials_dirty = False
        self._last_plan = None
        self._last_materials = ()
        self._last_input_options = ()
        self._last_stamina_item_ids = frozenset()
        if self._calculate_button.isEnabled():
            self._calculate_button.setText("计算多角色材料与体力")
        for card in self._cards:
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._owned_materials.clear_materials()
        self._expanded_results.clear()
        self.plan_available.emit(False)
        self._refresh_target_state()
        self._set_result_message("选择角色目标后计算跨角色合计。")

    def close_controller(self) -> None:
        self._controller.close()

    def _refresh_target_state(self) -> None:
        count = len(self._cards)
        self._count.setText(f"已选 {count} 名")
        self._empty.setVisible(count == 0)
        self._cards_host.setVisible(count > 0)
        self._add_button.setEnabled(bool(self._roles))
        self.layout_changed.emit()

    def _card(self, line_id: str) -> CultivationBatchTargetCard | None:
        return next((card for card in self._cards if card.line_id == line_id), None)

    def _identity(self) -> object:
        return self._context_identity() if self._context_identity is not None else None

    def _set_result_message(self, text: str, *, error: bool = False) -> None:
        retired = self._clear_result()
        label = QLabel(text, self._result)
        label.setWordWrap(True)
        label.setStyleSheet(themed_style(
            "color:#f85149" if error else "color:#8b949e"
        ))
        self._result_layout.addWidget(label)
        self._result_layout.addStretch()
        self.result_replaced.emit(self._active_trace_id, retired)
        self.layout_changed.emit()

    def _clear_result(self) -> tuple[QWidget, ...]:
        retired: list[QWidget] = []
        while self._result_layout.count():
            item = self._result_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
                retired.append(widget)
        return tuple(retired)


def _identity_fields(identity: object) -> tuple[str, object, str]:
    if isinstance(identity, tuple) and len(identity) >= 3:
        return str(identity[0]), identity[1], str(identity[2])
    return "", identity, str(identity or "")


def _ledger_index(
    rows: tuple[CultivationMaterialSource, ...],
) -> dict[tuple[str, int], tuple[CultivationMaterialSource, ...]]:
    result: dict[tuple[str, int], list[CultivationMaterialSource]] = {}
    for row in rows:
        result.setdefault((row.line_id, row.section_index), []).append(row)
    return {key: tuple(value) for key, value in result.items()}


def _quantity(material: CultivationMaterial, quantity: int) -> CultivationMaterial:
    return CultivationMaterial(
        material.item_id,
        material.name,
        quantity,
        material.quality,
        material.icon_path,
    )


def _muted_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
    return label


def _path(value: object) -> str | None:
    return str(value) if value is not None else None


__all__ = ["CultivationBatchContent"]
