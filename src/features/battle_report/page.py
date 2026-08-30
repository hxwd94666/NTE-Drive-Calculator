# 展示精简战报主页面，并在页内切换固定轴边际分析。
"""Battle report page with a compact report view and marginal subpage."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.dialogs import show_help
from src.domain.battle_report import (
    BattleAnalysisSnapshot,
    BattleCaptureState,
    BattleSummary,
)
from src.features.battle_report.analysis_view import BattleLongAnalysisView
from src.features.battle_report.analysis_progress_bar import (
    BattleAnalysisProgressBar,
)
from src.services.battle_analysis_progress import BattleAnalysisProgress
from src.features.battle_report.marginal_page import BattleMarginalPage
from src.services.battle_buff_counterfactual_service import (
    BUFF_COUNTERFACTUAL_MODEL_VERSION,
)
from src.services.battle_passive_counterfactual_service import (
    PASSIVE_COUNTERFACTUAL_MODEL_VERSION,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    projected_range_duration_us,
    time_stop_overlap_us,
)
from src.ui.dashboard_widgets import metric_card, set_status_badge


def _section(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    label = QLabel(title)
    label.setObjectName("cardTitle")
    layout.addWidget(label)
    return card, layout


def _format_number(value: float) -> str:
    return f"{value:,.0f}"


class BattleReportPage(QWidget):
    start_requested = Signal()
    stop_requested = Signal()
    rerecord_requested = Signal()
    overlay_visibility_changed = Signal(bool)
    overlay_passthrough_changed = Signal(bool)
    detail_scope_changed = Signal(str)
    save_result_requested = Signal()
    history_requested = Signal()
    export_requested = Signal()
    analysis_range_requested = Signal(int, int)
    analysis_range_reset_requested = Signal()
    analysis_character_changed = Signal(int)
    target_condition_save_requested = Signal(object)
    build_edit_requested = Signal()
    build_edit_activation_requested = Signal(bool)
    marginal_requested = Signal()
    marginal_baseline_requested = Signal()
    marginal_recalculate_requested = Signal(object)
    marginal_reset_requested = Signal()
    marginal_draft_changed = Signal()
    marginal_closed = Signal()
    analysis_details_requested = Signal(str, object)

    def __init__(self, *, game_ui_asset_root, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._latest_summary: BattleSummary | None = None
        self._source_analysis = None
        self._marginal_result_scope: str | None = None
        self._marginal_result_is_candidate = False
        self._marginal_baseline_by_scope: dict[
            str | None,
            BattleAnalysisSnapshot,
        ] = {}
        self._detail_scope = "current"
        self._capture_running = False
        self._stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._stack, 1)
        self.analysis_progress = BattleAnalysisProgressBar(self)
        layout.addWidget(self.analysis_progress)
        self._build_report(game_ui_asset_root)
        self._build_marginal(game_ui_asset_root)
        self.target_condition_save_requested.connect(
            self._invalidate_marginal_baselines
        )
        self.build_edit_requested.connect(self._invalidate_marginal_baselines)
        self.build_edit_activation_requested.connect(
            self._invalidate_marginal_baselines
        )

    def _build_report(self, game_ui_asset_root) -> None:
        content = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self._stack.addWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(22, 18, 22, 22)
        root.setSpacing(16)

        control_card, control_layout = _section("战报采集工作台")
        status_row = QHBoxLayout()
        self.status_badge = QLabel()
        self.status_badge.setAlignment(Qt.AlignCenter)
        set_status_badge(self.status_badge, "未开始", "neutral")
        self.status_detail = QLabel("尚未开始战报采集。")
        self.status_detail.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        status_row.addWidget(self.status_badge)
        status_row.addWidget(self.status_detail, 1)
        help_text = (
            "使用 nte-core 采集战斗；采集期间暂停背包同步，结束后自动恢复。"
            "悬浮窗只在当前采集会话中显示。"
        )
        help_button = QPushButton("?")
        help_button.setObjectName("btnHelp")
        help_button.setToolTip(help_text)
        help_button.clicked.connect(
            lambda _checked=False, button=help_button: show_help(
                button,
                "战报采集工作台",
                help_text,
            )
        )
        self.export_button = QPushButton("导出战报")
        self.export_button.setObjectName("btnAction")
        self.export_button.clicked.connect(self.export_requested)
        status_row.addWidget(self.export_button)
        status_row.addWidget(help_button)
        control_layout.addLayout(status_row)
        actions = QHBoxLayout()
        self.capture_button = QPushButton("开始采集")
        self.capture_button.setObjectName("btnPrimary")
        self.capture_button.clicked.connect(self._request_capture_action)
        actions.addWidget(self.capture_button)
        self.rerecord_button = QPushButton("放弃重录")
        self.rerecord_button.setObjectName("btnDanger")
        self.rerecord_button.setToolTip(
            "丢弃本次尚未保存的战报，并立即重新开始采集。"
        )
        self.rerecord_button.hide()
        self.rerecord_button.clicked.connect(self.rerecord_requested)
        self.save_result_button = QPushButton("保存伤害结果")
        self.save_result_button.setEnabled(False)
        self.save_result_button.clicked.connect(self.save_result_requested)
        actions.addWidget(self.save_result_button)
        self.history_button = QPushButton("读取历史战报")
        self.history_button.clicked.connect(self.history_requested)
        actions.addWidget(self.history_button)
        actions.addSpacing(12)
        self.overlay_toggle = QCheckBox("显示实时悬浮窗")
        self.overlay_toggle.setChecked(True)
        self.overlay_toggle.toggled.connect(self.overlay_visibility_changed)
        actions.addWidget(self.overlay_toggle)
        self.passthrough_toggle = QCheckBox("鼠标穿透")
        self.passthrough_toggle.setChecked(True)
        self.passthrough_toggle.setToolTip("关闭后可拖动悬浮窗；开启后鼠标操作落到游戏。")
        self.passthrough_toggle.toggled.connect(self.overlay_passthrough_changed)
        actions.addWidget(self.passthrough_toggle)
        actions.addSpacing(12)
        actions.addWidget(self.rerecord_button)
        actions.addStretch()
        control_layout.addLayout(actions)
        root.addWidget(control_card)

        metrics = QGridLayout()
        definitions = (
            ("dps", "队伍 DPS", "扣除停表时间"),
            ("damage", "总伤害", "完整战报"),
            ("duration", "战斗时长", "扣除停表（括号为真实时长）"),
            ("taken", "承受伤害", "全队"),
        )
        self.metric_labels: dict[str, QLabel] = {}
        for column, (key, title, subtitle) in enumerate(definitions):
            card, value, _sub = metric_card(title, "—", subtitle)
            self.metric_labels[key] = value
            metrics.addWidget(card, 0, column)
        root.addLayout(metrics)

        self.long_analysis_view = BattleLongAnalysisView(
            game_ui_asset_root=game_ui_asset_root
        )
        self.long_analysis_view.range_requested.connect(self.analysis_range_requested)
        self.long_analysis_view.range_reset_requested.connect(
            self.analysis_range_reset_requested
        )
        self.long_analysis_view.character_selected.connect(
            self.analysis_character_changed
        )
        self.long_analysis_view.detail_scope_changed.connect(
            self._select_detail_scope
        )
        self.long_analysis_view.marginal_requested.connect(
            self.marginal_requested
        )
        self.long_analysis_view.target_vital_panel.condition_save_requested.connect(
            self.target_condition_save_requested
        )
        self.long_analysis_view.details_requested.connect(
            self.analysis_details_requested
        )
        self.long_analysis_view.build_edit_control.edit_requested.connect(
            self.build_edit_requested
        )
        root.addWidget(self.long_analysis_view)
        self.quality_label = QLabel("数据质量：等待采集")
        self.quality_label.hide()
        root.addWidget(self.quality_label)
        root.addStretch()

    def _build_marginal(self, game_ui_asset_root) -> None:
        self.marginal_page = BattleMarginalPage(
            game_ui_asset_root=game_ui_asset_root,
        )
        self.marginal_page.back_requested.connect(self.show_report)
        self.marginal_page.recalculate_requested.connect(
            self.marginal_recalculate_requested
        )
        self.marginal_page.reset_requested.connect(self.marginal_reset_requested)
        self.marginal_page.draft_changed.connect(self.marginal_draft_changed)
        self.marginal_page.role_changed.connect(self._marginal_role_changed)
        self._stack.addWidget(self.marginal_page)

    def show_marginal(self, editor_data: dict) -> None:
        self.marginal_page.set_editor_data(
            editor_data,
            selected_character_id=self.long_analysis_view.selected_character_id(),
        )
        source_analysis = self._marginal_baseline_by_scope.get(
            self.marginal_detail_scope()
        ) or self._source_analysis
        if source_analysis is not None:
            self.marginal_page.set_source_analysis(source_analysis)
        self._stack.setCurrentWidget(self.marginal_page)
        self._request_marginal_lazy_recalculation()

    def show_report(self) -> None:
        self.marginal_page.clear_candidate()
        self._stack.setCurrentIndex(0)
        self.marginal_closed.emit()

    def reset_marginal_draft(self, editor_data: dict) -> None:
        selected_character_id = self.marginal_page.selected_character_id()
        self.marginal_page.set_editor_data(
            editor_data,
            selected_character_id=selected_character_id,
        )
        self.invalidate_marginal_result()
        self._request_marginal_lazy_recalculation()

    def invalidate_marginal_result(self) -> None:
        self._marginal_result_scope = None
        self._marginal_result_is_candidate = False
        baseline = self._marginal_baseline_by_scope.get(
            self.marginal_detail_scope()
        )
        source = baseline or self._source_analysis
        if source is not None:
            self.marginal_page.set_source_analysis(source)

    def _invalidate_marginal_baselines(self, *_args) -> None:
        self._marginal_baseline_by_scope.clear()

    def _marginal_role_changed(self, detail_scope: object) -> None:
        scope = str(detail_scope) if detail_scope in {"first", "second"} else None
        if self._marginal_result_scope is not None and scope != self._marginal_result_scope:
            use_candidate = self._marginal_result_is_candidate
            self.invalidate_marginal_result()
            self._request_marginal_lazy_recalculation(
                force=use_candidate,
                use_candidate=use_candidate,
            )
        elif self._marginal_result_scope is None:
            self._request_marginal_lazy_recalculation()

    def _request_marginal_lazy_recalculation(
        self,
        *,
        force: bool = False,
        use_candidate: bool = False,
    ) -> None:
        if (
            self._stack.currentWidget() is not self.marginal_page
            or not self.marginal_page.allows_automatic_recalculation()
        ):
            return
        source_analysis = self._marginal_baseline_by_scope.get(
            self.marginal_detail_scope()
        ) or self._source_analysis
        passive_model_current = (
            not hasattr(source_analysis, "passive_counterfactual_model_version")
            or getattr(
                source_analysis,
                "passive_counterfactual_model_version",
                "",
            )
            == PASSIVE_COUNTERFACTUAL_MODEL_VERSION
        )
        if (
            not force
            and getattr(
                source_analysis,
                "buff_counterfactual_model_version",
                "",
            )
            == BUFF_COUNTERFACTUAL_MODEL_VERSION
            and passive_model_current
        ):
            return
        if use_candidate:
            profiles = self.marginal_page.profiles()
            if profiles:
                self.marginal_recalculate_requested.emit(profiles)
            return
        self.marginal_baseline_requested.emit()

    def update_state(self, state: BattleCaptureState) -> None:
        tones = {
            "starting": ("启动中", "active"),
            "running": ("采集中", "success"),
            "stopping": ("正在结束", "warning"),
            "stopped": ("已结束", "neutral"),
            "history": ("历史战报", "neutral"),
            "error": ("采集异常", "error"),
        }
        label, tone = tones.get(state.phase, (state.phase, "neutral"))
        set_status_badge(self.status_badge, label, tone)
        self.status_detail.setText(
            state.message if not state.error else f"{state.message}：{state.error}"
        )
        self._capture_running = state.running
        stopping = state.phase == "stopping"
        self.capture_button.setText("结束保存" if state.running else "开始采集")
        object_name = "btnDanger" if state.running else "btnPrimary"
        if self.capture_button.objectName() != object_name:
            self.capture_button.setObjectName(object_name)
            self.capture_button.style().unpolish(self.capture_button)
            self.capture_button.style().polish(self.capture_button)
        self.capture_button.setEnabled(not stopping)
        self.rerecord_button.setVisible(state.phase == "running")
        self.rerecord_button.setEnabled(state.phase == "running")
        self.history_button.setEnabled(not state.running)
        is_manual = state.retention_kind == "manual"
        self.save_result_button.setText("已手动保存" if is_manual else "保存伤害结果")
        self.save_result_button.setEnabled(
            not state.running
            and state.battle_record_id is not None
            and state.retention_kind == "auto"
        )
        if state.summary is not None:
            self._render_summary(state.summary)

    def _request_capture_action(self) -> None:
        if self._capture_running:
            self.stop_requested.emit()
        else:
            self.start_requested.emit()

    def set_rerecord_hotkey_label(self, hotkey: str) -> None:
        self.rerecord_button.setToolTip(
            "丢弃本次尚未保存的战报，并立即重新开始采集。"
            f"全局快捷键：连续按两次 {hotkey}。"
        )

    def show_rerecord_hotkey_confirmation(
        self,
        hotkey: str,
        seconds: float,
    ) -> None:
        self.status_detail.setText(
            f"请在 {seconds:g} 秒内再次按 {hotkey}，确认放弃当前战报并重录。"
        )

    def set_overlay_checked(self, visible: bool) -> None:
        self.overlay_toggle.blockSignals(True)
        self.overlay_toggle.setChecked(visible)
        self.overlay_toggle.blockSignals(False)

    def set_detail_scope(self, mode: str) -> None:
        self._detail_scope = mode
        summary = self._latest_summary
        self.long_analysis_view.set_detail_scope(
            mode,
            first_available=bool(summary and summary.abyss.first_half is not None),
            second_available=bool(summary and summary.abyss.second_half is not None),
        )

    def detail_scope(self) -> str:
        return self.long_analysis_view.detail_scope()

    def clear_summary(self) -> None:
        self.marginal_page.clear_candidate()
        self._source_analysis = None
        self._marginal_result_scope = None
        self._marginal_result_is_candidate = False
        self._marginal_baseline_by_scope.clear()
        self._latest_summary = None
        self._detail_scope = "current"
        for label in self.metric_labels.values():
            label.setText("—")
        self.long_analysis_view.clear()

    def set_analysis(self, analysis, *, selected_character_id=None) -> None:
        self._source_analysis = analysis
        self._marginal_baseline_by_scope.clear()
        if self._latest_summary is not None:
            raw_damage = max(0.0, float(self._latest_summary.total_damage))
            overkill_correction = (
                analysis.timeline_damage_correction_total
                if analysis.axis_complete
                else 0.0
            )
            max_hp_settlement = (
                sum(
                    max(0.0, float(event.effective_hp_loss))
                    for event in getattr(analysis, "timeline_max_hp_events", ())
                    if getattr(event, "included_in_effective_damage", True)
                )
                if analysis.axis_complete else 0.0
            )
            corrected_damage = max(
                0.0,
                raw_damage
                - overkill_correction
                + max_hp_settlement,
            )
            battle_start_us = int(getattr(analysis, "battle_start_us", 0))
            intervals = tuple(getattr(analysis, "time_stop_intervals", ()))
            summary_duration_us = round(
                self._latest_summary.duration_seconds * 1_000_000
            )
            if (
                getattr(analysis, "time_stop_source_kind", "") == "nte_core"
                and getattr(
                    self._latest_summary,
                    "dps_time_mode",
                    "subtract_time_stop",
                )
                == "subtract_time_stop"
            ):
                interval_end_us = max(
                    (end_us or battle_start_us for _start_us, end_us in intervals),
                    default=battle_start_us,
                )
                summary_duration_us += time_stop_overlap_us(
                    battle_start_us,
                    max(int(analysis.battle_end_us), interval_end_us),
                    intervals,
                )
            raw_duration_us = max(
                summary_duration_us,
                int(analysis.battle_end_us) - battle_start_us,
            )
            active_duration_us = projected_range_duration_us(
                battle_start_us,
                battle_start_us + raw_duration_us,
                intervals=intervals,
                mode=ACTIVE_TIME_MODE,
            )
            duration = max(0.001, active_duration_us / 1_000_000.0)
            real_duration = raw_duration_us / 1_000_000.0
            self.metric_labels["damage"].setText(_format_number(corrected_damage))
            self.metric_labels["dps"].setText(_format_number(corrected_damage / duration))
            self.metric_labels["duration"].setText(
                f"{duration:.1f}s（{real_duration:.1f}s）"
            )
        self.long_analysis_view.set_analysis(
            analysis,
            selected_character_id=selected_character_id,
        )
        self.marginal_page.set_source_analysis(analysis)

    def set_marginal_analysis(
        self,
        analysis,
        *,
        detail_scope=None,
        is_candidate: bool = False,
    ) -> None:
        """Update the selected-role half without replacing report scope."""

        scope = (
            str(detail_scope) if detail_scope in {"first", "second"} else None
        )
        self._marginal_result_scope = scope
        self._marginal_result_is_candidate = bool(is_candidate)
        if not self._marginal_result_is_candidate:
            self._marginal_baseline_by_scope[scope] = analysis
        self.marginal_page.set_marginal_result(analysis)

    def complete_analysis_details(self, kind: str, payload: object) -> None:
        self.long_analysis_view.complete_analysis_details(kind, payload)

    def begin_analysis_details(self, kind: str) -> None:
        self.analysis_progress.show_for(kind)

    def update_analysis_progress(
        self,
        progress: BattleAnalysisProgress,
    ) -> None:
        self.analysis_progress.update_progress(progress)

    def end_analysis_details(self) -> None:
        self.analysis_progress.finish()

    def set_target_catalog(self, catalog: dict[str, object]) -> None:
        self.long_analysis_view.set_target_catalog(catalog)

    def analysis_range(self):
        return self.long_analysis_view.selected_range()

    def analysis_character_id(self):
        return self.marginal_page.selected_character_id()

    def marginal_detail_scope(self):
        return self.marginal_page.selected_detail_scope()

    def marginal_equipment_editable(self) -> bool:
        return self.marginal_page.equipment_editable()

    def marginal_profiles(self) -> list[dict]:
        return self.marginal_page.profiles()

    def marginal_comparison_baseline(self) -> BattleAnalysisSnapshot | None:
        return self._marginal_baseline_by_scope.get(
            self.marginal_detail_scope()
        )

    def marginal_disabled_inferred_fact_ids(self) -> tuple[str, ...]:
        return self.marginal_page.disabled_inferred_fact_ids()

    def clear_analysis(self, message: str) -> None:
        self.long_analysis_view.clear(message)

    def set_build_edit_state(
        self,
        *,
        has_edit: bool,
        active: bool,
        available: bool = True,
    ) -> None:
        self.long_analysis_view.build_edit_control.set_state(
            has_edit=has_edit,
            active=active,
            available=available,
        )
        self.long_analysis_view.audit_buttons["marginal"].setEnabled(available)

    def _render_summary(self, summary: BattleSummary) -> None:
        self._latest_summary = summary
        self.metric_labels["dps"].setText(_format_number(summary.total_dps))
        self.metric_labels["damage"].setText(_format_number(summary.total_damage))
        self.metric_labels["duration"].setText(f"{summary.duration_seconds:.1f}s")
        self.metric_labels["taken"].setText(_format_number(summary.total_damage_taken))
        self.set_detail_scope(self._detail_scope)
        quality = summary.quality
        self.quality_label.setText(
            f"{quality.source} · {quality.packet_count:,}包 · {quality.hit_count:,}击"
        )

    def _select_detail_scope(self, mode: str) -> None:
        self._detail_scope = mode
        self.detail_scope_changed.emit(mode)
