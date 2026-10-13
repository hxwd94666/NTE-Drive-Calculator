# 实现工具页多角色养成目标、共享材料与合并体力结果。
"""Batch cultivation UI embedded beside the single-target calculator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.toolbox.cultivation.batch_result import CultivationBatchResultMixin
from src.features.toolbox.cultivation.history_binding import CultivationHistoryDraftBinding, CultivationHistorySaveStatus
from src.services.progression.history_adapters import batch_history_payload
from src.services.progression.history_adapters import PreparedHistoryRestore
from src.features.toolbox.cultivation.batch_controller import (
    CultivationBatchController,
)
from src.features.toolbox.cultivation.batch_target import (
    CultivationBatchTargetCard,
)
from src.features.toolbox.cultivation.owned_materials import (
    CultivationOwnedMaterials,
    visible_materials,
    visible_owned_inputs,
)
from src.features.toolbox.cultivation.selectors import (
    select_cultivation_item,
    select_cultivation_items,
)
from src.features.toolbox.cultivation.stamina_ui import (
    CultivationStaminaControls,
)
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.services.progression.batch_planner import (
    CultivationBatchPlan,
    CultivationBatchPlannerService,
    CultivationBatchRequest,
    CultivationBatchPreparation,
    CultivationTargetDraft,
)
from src.services.progression.planner_service import (
    CultivationFork,
    CultivationMaterial,
    CultivationPlannerService,
    CultivationRole,
    CultivationSeed,
)
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.utils.cultivation_trace import trace_cultivation


class CultivationBatchContent(CultivationBatchResultMixin, QWidget):
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
        history_binding: CultivationHistoryDraftBinding | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._history_binding = history_binding
        self._restoring = False
        self._context_identity = context_identity
        self._initial_identity = self._identity()
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
        self._recalculate_timer.timeout.connect(lambda: self.calculate(explicit=False))
        self._prepare_timer = QTimer(self)
        self._prepare_timer.setSingleShot(True)
        self._prepare_timer.setInterval(250)
        self._prepare_timer.timeout.connect(self._prepare_materials)
        self._controller.preparation_ready.connect(self._receive_preparation)
        self._controller.preparation_error.connect(self._preparation_failed)
        self._controller.preparing_changed.connect(self._preparation_busy)
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
        self._preparation_hint = QLabel("目标确定后会提前列出已有材料输入项。", self)
        self._preparation_hint.setWordWrap(True)
        self._preparation_hint.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self._preparation_hint)
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
        if self._history_binding is not None:
            root.addWidget(CultivationHistorySaveStatus(self._history_binding, self))

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

    def calculate(self, _checked: bool = False, *, explicit: bool = True) -> None:
        if not self._cards:
            self._set_result_message("请先选择至少一个角色目标。")
            return
        identity = self._identity()
        if identity != self._initial_identity:
            return
        self._prepare_timer.stop()
        self._recalculate_timer.stop()
        self._trace_sequence += 1
        self._active_trace_id = self._trace_sequence
        request = self._build_request(identity)
        if self._history_binding is not None:
            envelope = self._history_binding.freeze(self.export_history_configuration(), explicit=explicit)
            request = replace(request, history_envelope=envelope)
        self._has_calculated = True
        trace_cultivation(request.trace_id, "ui.submit", targets=len(request.ordered_targets), owned_fields=len(request.owned_quantities))
        self._controller.submit(request, identity)

    def _prepare_materials(self) -> None:
        identity = self._identity()
        if self._cards and not self._restoring and identity == self._initial_identity:
            self._controller.prepare(self._build_request(identity), identity)

    def _receive_preparation(self, value: object) -> None:
        if not isinstance(value, CultivationBatchPreparation):
            return
        if value.ordered_targets != self._build_request(self._identity()).ordered_targets:
            return
        self._last_materials = value.merged_totals
        self._last_input_options = value.owned_inputs or value.merged_totals
        self._last_stamina_item_ids = value.stamina_item_ids
        self._owned_materials.set_materials(self._visible_inputs())
        self._preparation_hint.hide()

    def _preparation_busy(self, busy: bool) -> None:
        if busy:
            self._preparation_hint.setText("材料输入准备中；尚未求解体力。")
            self._preparation_hint.show()

    def _preparation_failed(self, _message: str) -> None:
        self._preparation_hint.setText("材料输入准备失败；已填数量保留，可点击计算重新尝试。")
        self._preparation_hint.show()

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
        if self._history_binding is not None and value.history_envelope is not None:
            self._history_binding.accept(
                value.history_envelope, lambda: batch_history_payload(
                    value.history_envelope.configuration_json, value, dict(value.dataset_metadata),
                ), history_error=value.history_error,
            )
        if self._calculate_button.isEnabled():
            self._calculate_button.setText("计算多角色材料与体力")
        self.plan_available.emit(True)
        self._render_plan(value)
        self._preparation_hint.hide()
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
        if self._restoring:
            return
        if self._history_binding is not None:
            self._history_binding.invalidate()
        self._controller.invalidate()
        self._prepare_timer.start()
        if not self._has_calculated:
            return
        self._recalculate_timer.stop()
        if not self._materials_dirty:
            self._materials_dirty = True
            self._last_plan = None
            self.plan_available.emit(False)
            self._set_result_message("已有材料已修改，请点击重新计算更新材料与体力。")
        if self._calculate_button.isEnabled():
            self._calculate_button.setText("重新计算多角色材料与体力")

    def _draft_changed(self, *_args: object) -> None:
        if self._restoring:
            return
        if self._history_binding is not None:
            self._history_binding.invalidate()
        self._controller.invalidate()
        self._prepare_timer.stop()
        if not self._cards:
            if self._history_binding is not None:
                self._history_binding.reset()
            self._recalculate_timer.stop()
            self._controller.invalidate(clear_preparation=True)
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
            self._last_plan = None
            self.plan_available.emit(False)
            self._set_result_message("养成目标已修改，旧结果已过期。")
            if not self._materials_dirty:
                self._recalculate_timer.start()
            else:
                self._prepare_timer.start()
        else:
            self._prepare_timer.start()
        self.layout_changed.emit()

    def export_history_configuration(self) -> dict[str, object]:
        hunter, identification = self._stamina_controls.values()
        return {
            "version": 1, "mode": "batch", "hunter_level": hunter, "identification_level": identification,
            "material_scope": self._material_scope, "owned_materials": self._owned_materials.export_history_materials(),
            "targets": [card.export_history_target() for card in self._cards],
        }

    def restore_from_history(self, prepared: PreparedHistoryRestore) -> None:
        if prepared.mode != "batch":
            raise ValueError("历史模式不匹配")
        configuration = json.loads(prepared.configuration_json)
        self._recalculate_timer.stop()
        self._prepare_timer.stop()
        self._controller.invalidate(clear_preparation=True)
        self._restoring = True
        try:
            for card in self._cards:
                self._cards_layout.removeWidget(card)
                card.deleteLater()
            self._cards.clear()
            for target, seed in zip(configuration["targets"], prepared.seeds, strict=True):
                self._append_target(seed)
                self._cards[-1].restore_history_target(target)
            self._stamina_controls.restore_values(configuration["hunter_level"], configuration["identification_level"])
            self._owned_materials.clear_materials()
            self._owned_materials.restore_history_materials(configuration["owned_materials"])
            self._material_scope = configuration["material_scope"]
            self._has_calculated = False
            self._materials_dirty = False
            self._last_plan = None
            self._last_materials = ()
            self._last_input_options = ()
            self._last_stamina_item_ids = frozenset()
        finally:
            self._restoring = False
        self._refresh_target_state()
        self.plan_available.emit(False)
        self._set_result_message("已加载历史配置；请确认当前状态与材料数量后点击计算。")
        self._prepare_timer.start()

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
        if self._history_binding is not None:
            self._history_binding.reset()
        self._recalculate_timer.stop()
        self._prepare_timer.stop()
        self._controller.invalidate(clear_preparation=True)
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
        self._recalculate_timer.stop()
        self._prepare_timer.stop()
        self._controller.close()
        if self._history_binding is not None:
            self._history_binding.close()

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
        try:
            return self._context_identity() if self._context_identity is not None else None
        except (OSError, RuntimeError):
            return None

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


def _path(value: object) -> str | None:
    return str(value) if value is not None else None


__all__ = ["CultivationBatchContent"]
