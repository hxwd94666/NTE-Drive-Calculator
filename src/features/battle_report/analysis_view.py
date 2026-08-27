# 展示统一分析时段下的战斗时间轴、逐击证据和角色边际。
"""Long-form battle analysis view; all calculations stay in application services."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.dialogs import show_help
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report import BattleAnalysisSnapshot, BattleDamageComposition
from src.features.battle_report.analysis_composition_mixin import (
    BattleAnalysisCompositionMixin,
)
from src.features.battle_report.analysis_components import (
    apply_inferred_scope_warning,
    analysis_section as _section,
    analysis_table as _table,
)
from src.features.battle_report.analysis_log_mixin import BattleAnalysisLogMixin
from src.features.battle_report.analysis_scope_mixin import BattleAnalysisScopeMixin
from src.features.battle_report.analysis_timeline_detail_mixin import (
    BattleTimelineDetailMixin,
)
from src.features.battle_report.composition_view import (
    BattleDamageCompositionPanel,
)
from src.features.battle_report.inferred_fact_view import BattleInferredFactLabel
from src.features.battle_report.build_snapshot_control import (
    BattleBuildSnapshotControl,
)
from src.features.battle_report.buff_evidence_view import BattleBuffEvidencePanel
from src.features.battle_report.role_contribution_view import (
    BattleRoleDamagePieWidget,
)
from src.features.battle_report.timeline_view import (
    BattleUnifiedTimelineWidget,
)
from src.features.battle_report.timeline_layout import (
    format_analysis_evidence,
    format_damage as _number,
    format_time as _time,
    format_time_stop_evidence,
)
from src.features.battle_report.target_vital_view import BattleTargetVitalPanel
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    ELAPSED_TIME_MODE,
    BattleTimelineTimeMode,
    project_timeline_time_us,
    projected_range_duration_us,
    unproject_timeline_time_us,
)
from src.services.skill_name_rendering_service import (
    preferred_battle_damage_name,
    render_battle_classification,
)
from src.ui.dashboard_widgets import metric_card
from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox

class BattleLongAnalysisView(
    BattleAnalysisCompositionMixin,
    BattleAnalysisScopeMixin,
    BattleTimelineDetailMixin,
    BattleAnalysisLogMixin,
    QWidget,
):
    range_requested = Signal(int, int)
    range_reset_requested = Signal()
    character_selected = Signal(int)
    detail_scope_changed = Signal(str)
    marginal_requested = Signal()
    details_requested = Signal(str, object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        game_ui_asset_root: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._game_ui_asset_root = game_ui_asset_root
        self._analysis: BattleAnalysisSnapshot | None = None
        self._current_composition: BattleDamageComposition | None = None
        self._analysis_record_id: int | None = None
        self._selected_character_id: int | None = None
        self._time_mode: BattleTimelineTimeMode = ELAPSED_TIME_MODE
        self._detail_scope = "current"
        self._composition_grouping = "coarse"
        self._topple_detail_requested_analysis: BattleAnalysisSnapshot | None = None
        self._log_page = 0
        self._log_page_size = 200
        self._build()
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        timeline_card, timeline_layout = _section("统一战斗时间轴")
        range_row = QGridLayout()
        range_row.setHorizontalSpacing(8)
        range_row.setVerticalSpacing(8)
        self.capability_label = QLabel("等待逐击证据", timeline_card)
        self.capability_label.hide()
        range_row.addWidget(QLabel("开始（轴秒）"), 0, 0)
        self.start_spin = NoWheelDoubleSpinBox()
        self.start_spin.setDecimals(3)
        self.start_spin.setRange(0.0, 999999.0)
        range_row.addWidget(self.start_spin, 0, 1)
        range_row.addWidget(QLabel("结束（轴秒）"), 0, 2)
        self.end_spin = NoWheelDoubleSpinBox()
        self.end_spin.setDecimals(3)
        self.end_spin.setRange(0.001, 999999.0)
        range_row.addWidget(self.end_spin, 0, 3)
        apply_button = QPushButton("确定")
        apply_button.setObjectName("btnPrimary")
        apply_button.clicked.connect(self._request_range)
        range_row.addWidget(apply_button, 0, 4)
        reset_button = QPushButton("重置")
        reset_button.clicked.connect(self.range_reset_requested)
        range_row.addWidget(reset_button, 0, 5)
        range_row.addWidget(QLabel("口径"), 0, 6)
        self.time_mode_combo = NoWheelComboBox()
        self.time_mode_combo.addItem("扣除时停", ACTIVE_TIME_MODE)
        self.time_mode_combo.addItem("包含时停", ELAPSED_TIME_MODE)
        self.time_mode_combo.setCurrentIndex(
            self.time_mode_combo.findData(ELAPSED_TIME_MODE)
        )
        self.time_mode_combo.currentIndexChanged.connect(self._time_mode_changed)
        range_row.addWidget(self.time_mode_combo, 0, 7)
        range_row.addWidget(QLabel("缩放"), 0, 8)
        self.zoom_combo = NoWheelComboBox()
        for percent in (10, 25, 50, 100, 200, 300, 400, 500, 600, 700, 800):
            self.zoom_combo.addItem(f"{percent}%", percent / 100.0)
        self.zoom_combo.setCurrentIndex(self.zoom_combo.findData(1.0))
        self.zoom_combo.currentIndexChanged.connect(self._zoom_changed)
        range_row.addWidget(self.zoom_combo, 0, 9)
        help_text = (
            "上方直伤、下方动作；特殊伤害和环合使用公共行。"
            "拖动可平移，右键可设置分析起止，时间轴本体不会因审计弹窗改变。"
        )
        help_button = QPushButton("?")
        help_button.setObjectName("btnHelp")
        help_button.setToolTip(help_text)
        help_button.clicked.connect(
            lambda _checked=False, button=help_button: show_help(
                button,
                "统一战斗时间轴",
                help_text,
            )
        )
        range_row.addWidget(help_button, 0, 10)
        range_row.setColumnStretch(10, 1)
        timeline_layout.addLayout(range_row)
        timeline_layout.addWidget(self.capability_label)

        context_row = QHBoxLayout()
        self.context_row = context_row
        self.build_edit_control = BattleBuildSnapshotControl()
        context_row.addWidget(QLabel("明细范围"))
        self.scope_button_group = QButtonGroup(self)
        self.scope_button_group.setExclusive(True)
        self.scope_buttons: dict[str, QPushButton] = {}
        for mode, label in (("current", "跟随"), ("first", "上半"), ("second", "下半")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=mode: self._select_detail_scope(value)
            )
            self.scope_button_group.addButton(button)
            self.scope_buttons[mode] = button
            context_row.addWidget(button)
        self.scope_buttons["current"].setChecked(True)
        self.scope_buttons["first"].setEnabled(False)
        self.scope_buttons["second"].setEnabled(False)
        context_row.addStretch()
        context_row.addWidget(self.build_edit_control)
        self.current_scope_title = QLabel("当前范围")
        context_row.addWidget(self.current_scope_title)
        self.current_scope_label = QLabel("未知")
        self.current_scope_label.setStyleSheet(
            themed_style("color:#58a6ff;font-weight:600")
        )
        context_row.addWidget(self.current_scope_label)
        self.environment_button = QPushButton("编辑")
        context_row.addWidget(self.environment_button)
        timeline_layout.addLayout(context_row)

        self.action_summary_label = QLabel("等待动作与输入推算", timeline_card)
        self.action_summary_label.hide()
        self.timeline = BattleUnifiedTimelineWidget(
            game_ui_asset_root=self._game_ui_asset_root
        )
        self.timeline.range_boundary_requested.connect(self._timeline_boundary_requested)
        self.timeline.selection_activated.connect(
            self._timeline_selection_activated
        )
        self.timeline_scroll = QScrollArea()
        self.timeline_scroll.setWidgetResizable(True)
        self.timeline_scroll.setFrameShape(QFrame.NoFrame)
        self.timeline_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.timeline_scroll.setWidget(self.timeline)
        self.timeline_scroll.horizontalScrollBar().valueChanged.connect(
            self.timeline.set_horizontal_view_offset
        )
        self.timeline.horizontal_pan_requested.connect(lambda delta: self.timeline_scroll.horizontalScrollBar().setValue(self.timeline_scroll.horizontalScrollBar().value() + delta))
        self.timeline.content_height_changed.connect(
            lambda height: self._fit_axis_height(self.timeline_scroll, height)
        )
        self._fit_axis_height(self.timeline_scroll, self.timeline.sizeHint().height())
        timeline_layout.addWidget(self.timeline_scroll)
        root.addWidget(timeline_card)

        metrics = QGridLayout()
        definitions = (
            ("damage", "有效伤害", "正式逐击 + 上限结算"),
            ("dps", "有效 DPS", "按统一时段"),
            ("raw_damage", "正式逐击", "nte-core 原始证据"),
            ("vital_damage", "生命上限结算", "观测差值 × 当前生命比例"),
            ("duration", "时段长度", "左闭右开"),
            ("hits", "逐击事件", "主伤害与追加拆分"),
        )
        self.metric_labels: dict[str, QLabel] = {}
        for index, (key, title, subtitle) in enumerate(definitions):
            card, value, _ = metric_card(title, "—", subtitle)
            self.metric_labels[key] = value
            metrics.addWidget(card, index // 3, index % 3)
        root.addLayout(metrics)
        roles_card, roles_layout = _section("选定时段角色贡献")
        roles_content = QHBoxLayout()
        roles_content.setContentsMargins(0, 0, 0, 0)
        roles_content.setSpacing(18)
        self.roles_table = _table(
            ("角色", "逐击 / 结算", "有效伤害", "有效 DPS", "时段占比"),
            180,
            default_widths=(155, 120, 112, 102, 126),
        )
        self.roles_table.setMinimumWidth(620)
        roles_content.addWidget(self.roles_table, 3)
        self.roles_pie = BattleRoleDamagePieWidget()
        roles_content.addWidget(self.roles_pie, 2)
        roles_layout.addLayout(roles_content)
        root.addWidget(roles_card)
        composition_card, composition_layout = _section("选定时段角色伤害构成")
        composition_controls = QHBoxLayout()
        composition_controls.addWidget(QLabel("分类口径"))
        self.composition_group = QButtonGroup(self)
        self.composition_group.setExclusive(True)
        self.composition_buttons: dict[str, QPushButton] = {}
        for grouping, label in (("coarse", "粗分"), ("fine", "细分")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(
                lambda _checked=False, value=grouping: (
                    self._set_composition_grouping(value)
                )
            )
            self.composition_group.addButton(button)
            self.composition_buttons[grouping] = button
            composition_controls.addWidget(button)
        self.composition_buttons["coarse"].setChecked(True)
        self.composition_status_label = QLabel()
        self.composition_status_label.setStyleSheet(
            themed_style("color:#d29922;font-size:12px")
        )
        self.composition_status_label.hide()
        composition_controls.addWidget(self.composition_status_label)
        composition_controls.addStretch()
        self.composition_topple_button = QPushButton("计算精准倾陷归属")
        self.composition_topple_button.clicked.connect(
            self._request_topple_attribution
        )
        self.composition_topple_button.hide()
        composition_controls.addWidget(self.composition_topple_button)
        composition_layout.addLayout(composition_controls)
        self.damage_composition_panel = BattleDamageCompositionPanel(
            game_ui_asset_root=self._game_ui_asset_root
        )
        composition_layout.addWidget(self.damage_composition_panel)
        root.addWidget(composition_card)

        audit_card, audit_layout = _section("审计")
        audit_row = QHBoxLayout()
        audit_row.setSpacing(10)
        self.audit_buttons: dict[str, QPushButton] = {}
        for key, label in (
            ("buff", "Buff"),
            ("skills", "技能明细"),
            ("hits", "逐击日志"),
            ("targets", "目标"),
            ("marginal", "边际计算"),
        ):
            button = QPushButton(label)
            self.audit_buttons[key] = button
            audit_row.addWidget(button)
        audit_row.addStretch()
        audit_layout.addLayout(audit_row)
        self.inferred_fact_label = BattleInferredFactLabel()
        audit_layout.addWidget(self.inferred_fact_label)
        root.addWidget(audit_card)

        self.buff_dialog, buff_layout = self._audit_dialog("Buff 审计")
        self.buff_panel = BattleBuffEvidencePanel()
        buff_layout.addWidget(self.buff_panel)
        self.skills_dialog, skills_layout = self._audit_dialog("技能明细")
        self.skills_table = _table(
            ("角色", "伤害项", "来源技能", "分类", "命中", "伤害", "占比"),
            250,
            default_widths=(130, 250, 230, 140, 86, 130, 92),
        )
        skills_layout.addWidget(self.skills_table)
        self.log_dialog, log_layout = self._audit_dialog("逐击日志", QSize(1380, 820))
        filter_row = QHBoxLayout()
        self.log_filter = QLineEdit()
        self.log_filter.setPlaceholderText("筛选角色、技能、伤害项或目标")
        self.log_filter.textChanged.connect(self._reset_log_page)
        filter_row.addWidget(self.log_filter, 1)
        self.prev_button = QPushButton("上一页")
        self.prev_button.pressed.connect(self._previous_log_page)
        filter_row.addWidget(self.prev_button)
        self.log_page_label = QLabel("0 / 0")
        filter_row.addWidget(self.log_page_label)
        self.next_button = QPushButton("下一页")
        self.next_button.pressed.connect(self._next_log_page)
        filter_row.addWidget(self.next_button)
        log_layout.addLayout(filter_row)
        self.log_table = _table(
            (
                "时间", "序号", "角色", "伤害项 / 来源技能", "类型",
                "目标", "伤害", "公式重放 / 误差", "暴击判定",
                "推算 Buff", "HP 前 → 后",
            ),
            360,
            default_widths=(
                112, 72, 140, 360, 230, 170, 130, 190, 125, 310, 190,
            ),
        )
        self.log_table.cellClicked.connect(self._log_cell_clicked)
        log_layout.addWidget(self.log_table)
        self.target_dialog, target_layout = self._audit_dialog(
            "受击目标与血量证据", QSize(1180, 780)
        )
        self.target_vital_panel = BattleTargetVitalPanel()
        target_layout.addWidget(self.target_vital_panel)
        self.environment_button.clicked.connect(
            self._open_environment_editor
        )
        self.audit_buttons["buff"].clicked.connect(
            lambda: self._request_detailed_analysis("buff")
        )
        self.audit_buttons["skills"].clicked.connect(
            lambda: self._open_lightweight_audit("skills")
        )
        self.audit_buttons["hits"].clicked.connect(
            lambda: self._request_detailed_analysis("hit")
        )
        self.audit_buttons["targets"].clicked.connect(
            lambda: self._open_lightweight_audit("targets")
        )
        self.audit_buttons["marginal"].clicked.connect(self.marginal_requested)

    def _audit_dialog(
        self,
        title: str,
        size: QSize = QSize(1080, 760),
    ) -> tuple[QDialog, QVBoxLayout]:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        fit_dialog_to_available_screen(dialog, size)
        return dialog, layout

    def clear(self, message: str = "当前记录只有聚合摘要，暂无正式逐击轴。") -> None:
        self._analysis = None
        self._current_composition = None
        self._topple_detail_requested_analysis = None
        self._analysis_record_id = None
        self._selected_character_id = None
        self.capability_label.setText(message)
        self.capability_label.show()
        self.action_summary_label.setText("当前没有可用的动作推算。")
        self.timeline.set_analysis(None)
        self.roles_pie.set_roles(())
        self.damage_composition_panel.clear()
        self.composition_status_label.hide()
        self.composition_topple_button.hide()
        self.buff_panel.clear()
        self.target_vital_panel.clear()
        self.inferred_fact_label.clear_facts()
        self._hide_hit_formula_dialog()
        self._hide_hit_buff_dialog()
        for label in self.metric_labels.values():
            label.setText("—")
        for table in (
            self.roles_table,
            self.skills_table,
            self.log_table,
        ):
            table.setRowCount(0)
        self.build_edit_control.set_state(
            has_edit=False,
            active=False,
            available=False,
        )
        self.current_scope_label.setText("未知")

    def set_loading(self, message: str) -> None:
        """Keep the previous projection visible while its replacement loads."""

        self.capability_label.setText(message)
        self.capability_label.show()

    def set_analysis(
        self,
        analysis: BattleAnalysisSnapshot,
        *,
        selected_character_id: int | None = None,
    ) -> None:
        self._analysis_record_id = analysis.battle_record_id
        self._selected_character_id = selected_character_id
        self._analysis = analysis
        self._hide_hit_formula_dialog()
        self._hide_hit_buff_dialog()
        self.timeline.set_analysis(analysis)
        self.timeline.set_time_mode(self._time_mode)
        self._focus_selected_timeline_range()
        self._render_time_presentation()
        self.capability_label.hide()
        self._render_damage_composition()
        self._render_roles()
        self._log_page = 0
        outer_buffs = tuple(dict.fromkeys(
            row.buff_name for row in analysis.timeline_buff_intervals
            if row.source_kind == "outer_realm_season_buff"
        ))
        outer_tip = (
            ""
            if not outer_buffs
            else "；赛季 Buff：" + "、".join(outer_buffs)
        )
        condition = analysis.target_condition
        self.environment_button.setEnabled(True)
        self.current_scope_label.setStyleSheet(
            themed_style("color:#58a6ff;font-weight:600")
        )
        if (
            condition is not None
            and condition.source_kind == "inferred_encounter_hp_injective_default"
        ):
            self.current_scope_label.setText(condition.target_name or "推断目标")
            self.environment_button.setToolTip(
                (
                    getattr(analysis, "target_identity_inference_basis", "")
                    or "环境与目标由完整遭遇的逐目标初始最大生命推断，仍可打开确认。"
                ) + outer_tip
            )
        elif condition is not None:
            target_name = condition.target_name or "已确认目标"
            summary = target_name
            if condition.feast_options:
                summary += f"；争锋加成 {len(condition.feast_options)} 项"
            if condition.witch_buff_name_zh:
                summary += f"；{condition.witch_buff_name_zh}"
            summary += outer_tip
            self.current_scope_label.setText(target_name)
            self.environment_button.setToolTip(summary)
        elif getattr(analysis, "detected_environment_kind", ""):
            self.current_scope_label.setText(
                getattr(analysis, "detected_environment_name", "") or "已推断环境"
            )
            self.environment_button.setToolTip(
                getattr(analysis, "target_identity_inference_basis", "")
                or "已从战报证据推断环境，可打开确认具体对象。"
            )
        else:
            self.current_scope_label.setText("未知")
            self.environment_button.setToolTip("打开后确认战斗环境和目标。")
        apply_inferred_scope_warning(self.current_scope_label, analysis, condition)

    def set_target_catalog(self, catalog: dict[str, object]) -> None:
        self.target_vital_panel.set_catalog(catalog)

    def _timeline_selection_activated(self, selected: object) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        if getattr(selected, "kind", None) != "hit":
            self._render_timeline_selection_detail(selected)
            return
        if not analysis.hit_replays:
            self.details_requested.emit("hit", selected)
            return
        self._render_timeline_selection_detail(selected)

    def _request_detailed_analysis(self, kind: str) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        if kind == "buff" and analysis.buff_counterfactual_model_version:
            self.complete_analysis_details(kind, None)
            return
        if kind == "hit" and analysis.hit_replays:
            self.complete_analysis_details(kind, None)
            return
        self.details_requested.emit(kind, None)

    def complete_analysis_details(self, kind: str, payload: object) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        if kind == "buff":
            self.buff_panel.render(analysis)
            self.buff_dialog.open()
        elif kind == "hit":
            if payload is None:
                self._render_log()
                self.log_dialog.open()
            else:
                self._render_timeline_selection_detail(payload)

    def _open_lightweight_audit(self, kind: str) -> None:
        if kind == "skills":
            self._render_skills()
            self.skills_dialog.open()
        elif kind == "targets":
            self._render_targets()
            self.target_dialog.open()

    def _open_environment_editor(self) -> None:
        self._render_targets()
        self.target_vital_panel.open_environment_dialog()

    def selected_range(self) -> tuple[int, int] | None:
        analysis = self._analysis
        if analysis is None:
            return None
        return analysis.range_start_us, analysis.range_end_us

    def detail_scope(self) -> str:
        return self._detail_scope

    def set_detail_scope(
        self,
        mode: str,
        *,
        first_available: bool,
        second_available: bool,
    ) -> None:
        self.scope_buttons["first"].setEnabled(first_available)
        self.scope_buttons["second"].setEnabled(second_available)
        button = self.scope_buttons.get(mode)
        if button is None or not button.isEnabled():
            mode = "current"
            button = self.scope_buttons[mode]
        self._detail_scope = mode
        button.setChecked(True)

    def _select_detail_scope(self, mode: str) -> None:
        button = self.scope_buttons.get(mode)
        if button is None or not button.isEnabled():
            return
        self._detail_scope = mode
        button.setChecked(True)
        self.detail_scope_changed.emit(mode)

    def selected_character_id(self) -> int | None:
        return self._selected_character_id

    @staticmethod
    def _fit_axis_height(scroll: QScrollArea, content_height: int) -> None:
        scrollbar_extent = scroll.style().pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent
        )
        scroll.setFixedHeight(
            max(1, int(content_height)) + scrollbar_extent + scroll.frameWidth() * 2
        )
    def _render_skills(self) -> None:
        analysis = self._analysis
        rows = analysis.skills if analysis else ()
        self.skills_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = (
                item.character_name,
                preferred_battle_damage_name(
                    item.damage_name,
                    item.skill_name,
                    item.ability_id,
                ),
                item.skill_name,
                render_battle_classification(item.classification),
                f"{item.hits:,}",
                _number(item.damage),
                f"{item.share_percent:.2f}%",
            )
            for column, value in enumerate(values):
                self.skills_table.setItem(row, column, QTableWidgetItem(value))
    def _render_targets(self) -> None:
        analysis = self._analysis
        if analysis is None or not hasattr(analysis, "target_condition"):
            self.target_vital_panel.clear()
            return
        self.target_vital_panel.render(
            analysis,
            projected_time=self._display_time_us,
        )

    def _time_mode_changed(self, _index: int = -1) -> None:
        value = self.time_mode_combo.currentData()
        self._time_mode = (
            ELAPSED_TIME_MODE if value == ELAPSED_TIME_MODE else ACTIVE_TIME_MODE
        )
        self.timeline.set_time_mode(self._time_mode)
        self._focus_selected_timeline_range()
        self._render_time_presentation()
        self._render_roles()
        if self.log_dialog.isVisible():
            self._render_log()
        if self.target_dialog.isVisible():
            self._render_targets()

    def _display_time_us(self, raw_time_us: int) -> int:
        analysis = self._analysis
        if analysis is None:
            return max(0, int(raw_time_us))
        return project_timeline_time_us(
            raw_time_us,
            battle_start_us=getattr(analysis, "battle_start_us", 0),
            intervals=getattr(analysis, "time_stop_intervals", ()),
            mode=self._time_mode,
        )

    def _selected_display_duration_seconds(self) -> float:
        analysis = self._analysis
        if analysis is None:
            return 0.0
        duration_us = projected_range_duration_us(
            analysis.range_start_us,
            analysis.range_end_us,
            intervals=analysis.time_stop_intervals,
            mode=self._time_mode,
        )
        return max(duration_us / 1_000_000.0, 0.001)

    def _render_time_presentation(self) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        start_display = self._display_time_us(analysis.range_start_us)
        end_display = self._display_time_us(analysis.range_end_us)
        mode_name = "扣除时停" if self._time_mode == ACTIVE_TIME_MODE else "包含时停"
        capability_name = format_analysis_evidence(analysis)
        hit_replays = getattr(analysis, "hit_replays", ())
        selected_hits, selected_actions, selected_inputs = (
            self._selected_axis_evidence()
        )
        replayed = sum(row.selected_damage is not None for row in hit_replays)
        crit_resolved = sum(
            row.critical_state in {"critical", "non_critical"}
            for row in hit_replays
        )
        self.capability_label.setText(
            f"{capability_name} · "
            f"{mode_name} {_time(start_display)}—{_time(end_display)} · "
            f"伤害模型 {analysis.formula_model_version} · "
            f"输入投影 {len(selected_inputs)} 块 / "
            f"动作 {len(selected_actions)} 段 · "
            f"公式重放 {replayed}/{len(hit_replays)} · "
            f"暴击判定 {crit_resolved} · "
            f"{getattr(analysis, 'hit_replay_model_version', '') or '公式按需加载'} · "
            f"{getattr(analysis, 'timeline_projection_version', '未生成时间轴')} · "
            f"{getattr(analysis, 'target_vital_model_version', '未生成生命轴')} · "
            f"{getattr(analysis, 'buff_inference_version', '') or 'Buff 按需加载'} · "
            f"{getattr(analysis, 'buff_attribute_projection_version', '') or 'Buff 投影按需加载'}"
        )
        self.inferred_fact_label.render_facts(
            tuple(getattr(analysis, "inferred_character_facts", ()))
        )
        action_event_ids = {
            event_id
            for action in selected_actions
            for event_id in action.evidence_event_ids
        }
        outgoing = tuple(
            hit
            for hit in selected_hits
            if hit.direction == "outgoing"
        )
        covered_damage = sum(
            hit.damage for hit in outgoing if hit.event_id in action_event_ids
        )
        outgoing_damage = sum(hit.damage for hit in outgoing)
        event_coverage = (
            len(action_event_ids) / len(outgoing) * 100.0 if outgoing else 0.0
        )
        damage_coverage = (
            covered_damage / outgoing_damage * 100.0 if outgoing_damage else 0.0
        )
        zoom_percent = round(float(self.zoom_combo.currentData() or 1.0) * 100)
        self.action_summary_label.setText(
            f"{mode_name} · {format_time_stop_evidence(analysis)} · "
            f"缩放 {zoom_percent}% · 当前时段推算输入 "
            f"{len(selected_inputs):,} 块 / 动作 "
            f"{len(selected_actions):,} 段 · 引用出伤事件 "
            f"{len(action_event_ids):,}/{len(outgoing):,}（{event_coverage:.1f}%） · "
            f"覆盖伤害 {damage_coverage:.1f}%。覆盖率只表示进入模型的证据，不代表动作准确率。"
        )
        self.start_spin.setValue(start_display / 1_000_000.0)
        self.end_spin.setValue(end_display / 1_000_000.0)
        duration = self._selected_display_duration_seconds()
        effective_damage = getattr(
            analysis,
            "effective_damage",
            getattr(analysis, "total_damage", 0.0),
        )
        self.metric_labels["damage"].setText(_number(effective_damage))
        self.metric_labels["dps"].setText(
            _number(effective_damage / duration if duration > 0 else 0.0)
        )
        raw_damage = getattr(analysis, "raw_total_damage", 0.0)
        if not raw_damage:
            raw_damage = getattr(analysis, "total_damage", 0.0)
        self.metric_labels["raw_damage"].setText(_number(raw_damage))
        self.metric_labels["vital_damage"].setText(
            _number(getattr(analysis, "max_hp_reduction_damage", 0.0))
        )
        self.metric_labels["duration"].setText(f"{duration:.3f}s")
        self.metric_labels["hits"].setText(
            f"{len(getattr(analysis, 'hits', ())):,}"
        )

    def _zoom_changed(self, _index: int = -1) -> None:
        factor = float(self.zoom_combo.currentData() or 1.0)
        scrollbar = self.timeline_scroll.horizontalScrollBar()
        viewport = self.timeline_scroll.viewport()
        center_time_us = self.timeline.display_time_at_widget_x(
            scrollbar.value() + viewport.width() / 2.0
        )
        self.timeline.set_zoom_factor(factor)

        def restore_center_time() -> None:
            try:
                target_x = self.timeline.widget_x_for_display_time(center_time_us)
                scrollbar.setValue(round(target_x - viewport.width() / 2.0))
            except RuntimeError:
                # 组合框信号排队后，页面可能已经随测试或导航被销毁。
                return

        QTimer.singleShot(0, restore_center_time)
        self._render_time_presentation()

    def _request_range(self) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        start = unproject_timeline_time_us(
            round(self.start_spin.value() * 1_000_000),
            battle_start_us=analysis.battle_start_us,
            battle_end_us=analysis.battle_end_us,
            intervals=analysis.time_stop_intervals,
            mode=self._time_mode,
        )
        end = unproject_timeline_time_us(
            round(self.end_spin.value() * 1_000_000),
            battle_start_us=analysis.battle_start_us,
            battle_end_us=analysis.battle_end_us,
            intervals=analysis.time_stop_intervals,
            mode=self._time_mode,
            prefer_interval_end=True,
        )
        if end > start:
            self.range_requested.emit(start, end)

    def _timeline_boundary_requested(self, boundary: str, raw_time_us: int) -> None:
        spin = self.start_spin if boundary == "start" else self.end_spin
        spin.setValue(self._display_time_us(raw_time_us) / 1_000_000.0)
        self._request_range()
