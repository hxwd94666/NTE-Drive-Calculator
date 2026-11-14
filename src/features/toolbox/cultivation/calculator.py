# 实现工具页养成计算器的交互界面。
"""Toolbox page content for calculating a character's formal cultivation materials."""

from __future__ import annotations

from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip, resolve_text_catalog
from src.ui.common.enum_text import ui_enum_text
from src.domain.localization.catalog import localized_text
from collections.abc import Callable
from dataclasses import replace
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.progression.history import PAYLOAD_VERSION
from src.features.toolbox.cultivation.single.single_result import CultivationSingleResultMixin
from src.features.toolbox.cultivation.history.history_binding import CultivationHistoryDraftBinding, CultivationHistorySaveStatus
from src.features.toolbox.cultivation.history.history_draft import capture_target, apply_target_values
from src.services.progression.history_adapters import single_history_payload
from src.services.progression.history_adapters import PreparedHistoryRestore
from src.services.progression.models import CultivationPreparedTarget
from src.features.toolbox.cultivation.owned_materials import (
    CultivationOwnedMaterials,
    remaining_materials,
    visible_materials,
    visible_owned_inputs,
)
from src.features.toolbox.cultivation.selectors import select_cultivation_item
from src.features.toolbox.cultivation.single.single_controller import (
    CultivationSingleController,
    SingleCalculationRequest,
    SingleCalculationResult,
)
from src.features.toolbox.cultivation.single.single_editor import CultivationSingleEditor
from src.features.toolbox.cultivation.stamina_ui import (
    CultivationStaminaControls,
)
from src.integrations.runtime.bundled_resources import bundled_game_ui_asset_root
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.services.progression.planner_service import (
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


class CultivationCalculatorContent(CultivationSingleResultMixin, QWidget):
    """Editable planning draft; calculation never writes the account or inventory."""

    plan_available = Signal(bool)
    layout_changed = Signal()
    calculation_completed = Signal()

    def __init__(
        self,
        service: CultivationPlannerService,
        parent: QWidget,
        *,
        context_identity: Callable[[], object] | None = None,
        asset_root: str | Path | None = None,
        history_binding: CultivationHistoryDraftBinding | None = None,
        text_catalog=None,
        load_initial_data: bool = True,
    ) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._service = service
        self._history_binding = history_binding
        self._restoring = False
        self._context_identity = context_identity
        self._initial_identity = context_identity() if context_identity is not None else None
        self._controller = CultivationSingleController(
            service, context_identity=context_identity, parent=self,
        )
        self._controller.result_ready.connect(self._receive_plan)
        self._controller.error.connect(self._calculation_error)
        self._controller.busy_changed.connect(self._set_busy)
        self._controller.preparation_ready.connect(self._receive_preparation)
        self._controller.preparation_error.connect(self._preparation_failed)
        self._prepared: CultivationPreparedTarget | None = None
        self._prepare_timer = QTimer(self)
        self._prepare_timer.setSingleShot(True)
        self._prepare_timer.setInterval(250)
        self._prepare_timer.timeout.connect(self._prepare_materials)
        self._roles: tuple[CultivationRole, ...] = ()
        self._forks: tuple[CultivationFork, ...] = ()
        self._seed: CultivationSeed | None = None
        self._fork_seed: CultivationForkSeed | None = None
        self._skill_inputs: dict[str, tuple[QSpinBox, QSpinBox]] = {}
        self._last_plan: CultivationPlan | None = None
        self._last_stamina_plan: CultivationStaminaPlan | None = None
        self._materials_dirty = False
        self._result_current = False
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
        if load_initial_data:
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

        self._editor = CultivationSingleEditor(input_body, text_catalog=getattr(self, 'text_catalog', None))
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
        for control in (
            self._current_level, self._current_stage, self._target_level, self._target_stage,
            self._fork_current_level, self._fork_current_stage,
            self._fork_target_level, self._fork_target_stage,
        ):
            if isinstance(control, QSpinBox):
                control.valueChanged.connect(self._draft_edited)
            else:
                control.currentIndexChanged.connect(self._draft_edited)
        for toggle in (self._character_toggle, self._skills_toggle, self._fork_toggle):
            toggle.toggled.connect(self._draft_edited)
        self._set_fork_controls_enabled(False)

        self._stamina_controls = CultivationStaminaControls(input_body, text_catalog=getattr(self, 'text_catalog', None))
        self._stamina_controls.values_changed.connect(self._stamina_inputs_changed)
        input_layout.addWidget(self._stamina_controls)
        self._preparation_hint = plain_label('', input_body)
        # 材料预准备不插入/隐藏进度行；只在真实失败时显示一次错误，避免反复推动结果区。
        self._preparation_hint.hide()
        self._preparation_hint.setWordWrap(True)
        self._preparation_hint.setTextFormat(Qt.TextFormat.PlainText)
        input_layout.addWidget(self._preparation_hint)

        self._owned_materials = CultivationOwnedMaterials(
            self._asset_catalog.progression_item_icon,
            input_body,
            text_catalog=getattr(self, 'text_catalog', None),
        )
        self._owned_materials.quantities_changed.connect(
            self._owned_quantities_changed
        )
        self._owned_materials.layout_changed.connect(self.layout_changed)
        input_layout.addWidget(self._owned_materials)

        action_row = QHBoxLayout()
        self._calculate_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.build.calculate_button'), input_body)
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
        if self._history_binding is not None:
            input_layout.addWidget(CultivationHistorySaveStatus(self._history_binding, input_body, text_catalog=getattr(self, 'text_catalog', None)))
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
        result_caption = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.build.materials_heading'), result_panel)
        result_caption.setStyleSheet(themed_style("font-size:15px;font-weight:900;color:#58a6ff"))
        result_layout.addWidget(result_caption)
        self._result_body = QWidget(result_panel)
        self._result_layout = QVBoxLayout(self._result_body)
        self._result_layout.setContentsMargins(12, 10, 12, 10)
        self._result_layout.setSpacing(8)
        result_layout.addWidget(self._result_body)
        layout.addWidget(result_panel)
        layout.addStretch(1)
        self._set_result_message(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.build.self'))

    def _load_roles(self) -> None:
        try:
            self._roles = self._service.list_roles()
        except Exception as exc:
            self._set_result_message(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.load_roles.self_2', exc=f'{exc}'), error=True)
            return
        if self._roles:
            self._load_selected_seed(self._roles[0].character_id)
        else:
            self._set_result_message(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.load_roles.self'), error=True)

    def _select_role(self) -> None:
        selected = select_cultivation_item(
            self,
            title=resolve_text_catalog(getattr(self, 'text_catalog', None)).render('matching.role'),
            description=resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.select_role.selected'),
            options=tuple(
                (
                    str(role.character_id),
                    role.name,
                    _asset_path(self._asset_catalog.character_icon(role.character_id)),
                )
                for role in self._roles
            ),
            selected_id=str(self._seed.character_id) if self._seed else None,
            text_catalog=getattr(self, 'text_catalog', None),
        )
        if selected is not None:
            self._load_selected_seed(int(selected))

    def _load_selected_seed(self, character_id: int) -> None:
        self._prepare_timer.stop()
        self._controller.invalidate(clear_preparation=True)
        self._prepared = None
        try:
            seed = self._service.load_seed(int(character_id))
        except Exception as exc:
            self._set_result_message(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.load_selected_seed.self_2', exc=f'{exc}'), error=True)
            return
        self._install_selected_seed(seed)

    def install_initial_data(self, roles, seed) -> None:
        """主页面的启动读取在后台统一完成，GUI 只接收同一冻结账号的纯值。"""
        self._roles = tuple(roles)
        if seed is not None:
            self._install_selected_seed(seed)
        else:
            self._set_result_message(self.text_catalog.render('ui.toolbox.load_roles.self'), error=True)

    def _install_selected_seed(self, seed: CultivationSeed) -> None:
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
        self._result_current = False
        self._calculate_button.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.build.calculate_button'))
        self.plan_available.emit(False)
        self._set_result_message(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.load_selected_seed.self'))
        if self._history_binding is not None:
            self._history_binding.reset()
        self._prepare_timer.start()

    def _select_fork(self) -> None:
        try:
            if not self._forks:
                self._forks = self._service.list_forks()
        except Exception as exc:
            QMessageBox.warning(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.open.qmessagebox'), plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.select_fork.qmessagebox', exc=f'{exc}')))
            return
        selected = select_cultivation_item(
            self,
            title=resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.build_hero.name'),
            description=resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.select_fork.selected'),
            options=tuple(
                (
                    item.fork_id,
                    item.name,
                    _asset_path(self._asset_catalog.fork_icon(item.fork_id)),
                )
                for item in self._forks
            ),
            selected_id=self._fork_seed.fork_id if self._fork_seed else None,
            text_catalog=getattr(self, 'text_catalog', None),
        )
        if selected is None:
            return
        try:
            self._apply_fork_seed(self._service.load_fork_seed(
                selected,
                character_id=self._seed.character_id if self._seed else None,
            ))
        except Exception as exc:
            QMessageBox.warning(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.open.qmessagebox'), plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.select_fork.qmessagebox_2', exc=f'{exc}')))

    def _apply_fork_seed(self, seed: CultivationForkSeed | None) -> None:
        self._controller.invalidate()
        if self._history_binding is not None and not self._restoring:
            self._history_binding.invalidate()
        self._fork_seed = seed
        self._set_fork_controls_enabled(seed is not None)
        if seed is None:
            self._editor.set_fork(None, None)
            self._draft_edited()
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
        self._draft_edited()

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
        for current, target in self._skill_inputs.values():
            current.valueChanged.connect(self._draft_edited)
            target.valueChanged.connect(self._draft_edited)

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

    def _set_stages(self, combo: QComboBox, level: int, preferred: object) -> None:
        previous = int(preferred) if preferred is not None else None
        options = _stages_for_level(level)
        blocked = combo.blockSignals(True)
        combo.clear()
        for stage in options:
            combo.addItem(
                _stage_label(
                    level, stage, text_catalog=getattr(self, 'text_catalog', None),
                ),
                stage,
            )
        selected = previous if previous in options else options[0]
        combo.setCurrentIndex(options.index(selected))
        combo.blockSignals(blocked)

    def _calculate(self, _checked: bool = False, *, explicit: bool = True) -> None:
        if self._seed is None:
            return
        identity = self._identity()
        if identity != self._initial_identity:
            return
        self._prepare_timer.stop()
        request = self._single_request()
        if self._history_binding is not None:
            envelope = self._history_binding.freeze(self.export_history_configuration(), explicit=explicit)
            request = replace(request, history_envelope=envelope)
        self._controller.submit(request, identity)

    def _prepare_materials(self) -> None:
        if self._seed is None or self._restoring:
            return
        identity = self._identity()
        if identity == self._initial_identity:
            self._controller.prepare(self._single_request(), identity)

    def _receive_preparation(self, value: object) -> None:
        if not isinstance(value, CultivationPreparedTarget) or self._seed is None:
            return
        if value.request != self._single_request().target:
            return
        self._prepared = value
        self._owned_materials.set_materials(self._input_options(value.plan))
        self._preparation_hint.hide()

    def _preparation_failed(self, _message: str) -> None:
        self._preparation_hint.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.preparation_failed.preparation_hint'))
        self._preparation_hint.show()

    def _single_request(self) -> SingleCalculationRequest:
        seed = self._seed
        if seed is None:
            raise ValueError("请先选择角色")
        target = CultivationRequest(
            character_id=seed.character_id,
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
        hunter, identification = self._stamina_controls.values()
        return SingleCalculationRequest(
            target, tuple(sorted(self._owned_materials.quantities().items())),
            hunter, identification,
        )

    def _receive_plan(self, value: object) -> None:
        if not isinstance(value, SingleCalculationResult) or self._seed is None:
            return
        if value.request != self._single_request():
            return
        plan = value.plan
        self._last_plan = plan
        self._last_stamina_plan = value.stamina
        self._prepared = value.preparation
        self._materials_dirty = False
        self._result_current = True
        self._calculate_button.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.build.calculate_button'))
        self.plan_available.emit(True)
        self._render_plan(plan)
        self._preparation_hint.hide()
        self.calculation_completed.emit()
        envelope = value.request.history_envelope
        if self._history_binding is not None and envelope is not None:
            self._history_binding.accept(
                envelope, lambda: single_history_payload(
                    envelope.configuration_json, value.plan, value.stamina, dict(value.dataset_metadata),
                    stamina_item_ids=value.preparation.stamina_item_ids if value.preparation is not None else None,
                ), history_error=value.history_error,
            )

    def _calculation_error(self, message: str) -> None:
        QMessageBox.warning(self, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.open.qmessagebox'), plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.calculation_error.qmessagebox', message=f'{message}')))

    def _set_busy(self, busy: bool) -> None:
        self._calculate_button.setEnabled(not busy)
        self._calculate_button.setText(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.set_busy.calculate_button') if busy else (
                resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.set_busy.calculate_button_2') if self._materials_dirty
                else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.build.calculate_button')
            )
        )

    def _draft_edited(self, *_args: object) -> None:
        if self._restoring:
            return
        self._controller.invalidate()
        if self._history_binding is not None:
            self._history_binding.invalidate()
        self._prepared = None
        self._result_current = False
        if self._last_plan is not None:
            self._materials_dirty = True
            self.plan_available.emit(False)
            self._set_result_message(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.draft_edited.self'))
        self._prepare_timer.start()

    def _owned_quantities_changed(self) -> None:
        if self._restoring:
            return
        self._controller.invalidate()
        if self._history_binding is not None:
            self._history_binding.invalidate()
        self._prepare_timer.start()
        self._result_current = False
        if self._last_plan is not None and not self._materials_dirty:
            self._materials_dirty = True
            self._calculate_button.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.set_busy.calculate_button_2'))
            self.plan_available.emit(False)
            self._set_result_message(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.owned_quantities_changed.self'))

    def _stamina_inputs_changed(self) -> None:
        if self._restoring:
            return
        self._controller.invalidate()
        if self._history_binding is not None:
            self._history_binding.invalidate()
        self._result_current = False
        if self._last_plan is not None and not self._materials_dirty:
            self.plan_available.emit(False)
            self._set_result_message(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.stamina_inputs_changed.self'))
            self._calculate(explicit=False)
        else:
            self._prepare_timer.start()

    def export_history_configuration(self) -> dict[str, object]:
        if self._seed is None:
            raise ValueError("请先选择角色")
        hunter, identification = self._stamina_controls.values()
        return {
            "version": PAYLOAD_VERSION, "mode": "single", "hunter_level": hunter, "identification_level": identification,
            "material_scope": self._material_scope, "owned_materials": self._owned_materials.export_history_materials(),
            "targets": [capture_target(self._editor, self._seed, self._fork_seed, self._skill_inputs, line_id="single")],
        }

    def restore_from_history(self, prepared: PreparedHistoryRestore) -> None:
        if prepared.mode != "single" or len(prepared.seeds) != 1:
            raise ValueError("历史模式或角色数量不匹配")
        configuration = json.loads(prepared.configuration_json)
        self._prepare_timer.stop()
        self._controller.invalidate(clear_preparation=True)
        self._prepared = None
        self._restoring = True
        try:
            self._seed = prepared.seeds[0]
            self._editor.set_role(self._seed.character_name, self._asset_catalog.character_icon(self._seed.character_id))
            self._rebuild_skills(self._seed)
            self._apply_fork_seed(self._seed.fork)
            apply_target_values(self._editor, configuration["targets"][0], self._skill_inputs, self._set_stages)
            self._set_character_progression_enabled(self._character_toggle.isChecked())
            self._set_skills_enabled(self._skills_toggle.isChecked())
            self._set_fork_controls_enabled(self._fork_seed is not None)
            self._stamina_controls.restore_values(configuration["hunter_level"], configuration["identification_level"])
            self._owned_materials.clear_materials()
            self._owned_materials.restore_history_materials(configuration["owned_materials"])
            self._material_scope = configuration["material_scope"]
            self._last_plan = None
            self._last_stamina_plan = None
            self._materials_dirty = False
            self._result_current = False
        finally:
            self._restoring = False
        self.plan_available.emit(False)
        self._set_result_message(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.restore_from_history.self'))
        self._prepare_timer.start()

    def set_material_scope(self, scope: str) -> None:
        if scope not in {"all", "stamina"} or scope == self._material_scope:
            return
        self._material_scope = scope
        if self._prepared is not None:
            self._owned_materials.set_materials(self._input_options(self._prepared.plan))
        if self._last_plan is None:
            return
        if self._result_current:
            self._render_plan(self._last_plan)

    def _visible(
        self, materials: tuple[CultivationMaterial, ...]
    ) -> tuple[CultivationMaterial, ...]:
        item_ids = (
            getattr(self._last_stamina_plan, "stamina_item_ids", frozenset())
            if self._last_stamina_plan is not None else frozenset()
        )
        return visible_materials(materials, self._material_scope, item_ids)

    def _input_options(self, plan: CultivationPlan) -> tuple[CultivationMaterial, ...]:
        item_ids = (
            self._last_stamina_plan.stamina_item_ids
            if self._result_current and self._last_stamina_plan is not None else (
                self._prepared.stamina_item_ids if self._prepared is not None else frozenset()
            )
        )
        return visible_owned_inputs(
            plan.owned_inputs or plan.totals, plan.totals,
            self._material_scope, item_ids,
        )

    def copy_plan(self) -> None:
        if self._last_plan is None or not self._result_current:
            return
        lines = [localized_text(
            getattr(self, 'text_catalog', None),
            "cultivation.calculator.copy.title",
            "{name} · 养成材料",
            name=self._last_plan.character_name,
        )]
        if self._last_plan.fork_required_experience:
            lines.append(localized_text(
                getattr(self, 'text_catalog', None),
                "cultivation.calculator.copy.fork_experience",
                "弧盘升级经验 × {experience}",
                experience=f"{self._last_plan.fork_required_experience:,}",
            ))
        for material in remaining_materials(
            self._visible(self._last_plan.totals),
            self._owned_materials.quantities(),
        ):
            lines.append(f"{material.name} × {material.quantity:,}")
        QApplication.clipboard().setText("\n".join(lines))

    def reset_draft(self) -> None:
        """Restore the selected role's saved state and clear transient results."""

        self._controller.invalidate()
        self._details_expanded = False
        self._last_plan = None
        self._last_stamina_plan = None
        self._materials_dirty = False
        self._result_current = False
        self._calculate_button.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.build.calculate_button'))
        self.plan_available.emit(False)
        if self._seed is not None:
            self._load_selected_seed(self._seed.character_id)
        elif self._roles:
            self._load_selected_seed(self._roles[0].character_id)
        else:
            self._set_result_message(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.reset_draft.self'))

    def close_controller(self) -> None:
        self._prepare_timer.stop()
        self._controller.close()
        if self._history_binding is not None:
            self._history_binding.close()

    def _identity(self) -> object:
        try:
            return self._context_identity() if self._context_identity is not None else None
        except (OSError, RuntimeError):
            return None

    def _set_result_message(self, text: str, *, error: bool = False) -> None:
        self._clear_result()
        message = plain_label(text, self._result_body)
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


def _stage_label(level: int, stage: int, *, text_catalog=None) -> str:
    alternatives = _stages_for_level(level)
    if len(alternatives) == 2:
        return localized_text(
            text_catalog,
            "cultivation.calculator.stage_label.with_phase",
            "突破 {stage}（{phase}）",
            stage=stage,
            phase=ui_enum_text(
                text_catalog, "突破前" if stage == alternatives[0] else "突破后",
            ),
        )
    return localized_text(
        text_catalog,
        "cultivation.calculator.stage_label.plain",
        "突破 {stage}",
        stage=stage,
    )


def _asset_path(value: object) -> str | None:
    return str(value) if value is not None else None


__all__ = ["CultivationCalculatorContent"]
