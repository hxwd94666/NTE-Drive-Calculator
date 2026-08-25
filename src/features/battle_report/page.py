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
from src.domain.battle_report import BattleCaptureState, BattleSummary
from src.features.battle_report.analysis_view import BattleLongAnalysisView
from src.features.battle_report.analysis_progress_bar import (
    BattleAnalysisProgressBar,
)
from src.features.battle_report.marginal_page import BattleMarginalPage
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
    marginal_recalculate_requested = Signal(object)
    marginal_restore_requested = Signal()
    marginal_analysis_requested = Signal(int, object, object)
    analysis_details_requested = Signal(str, object)

    def __init__(self, *, game_ui_asset_root, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._latest_summary: BattleSummary | None = None
        self._detail_scope = "current"
        self._stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._stack, 1)
        self.analysis_progress = BattleAnalysisProgressBar(self)
        layout.addWidget(self.analysis_progress)
        self._build_report(game_ui_asset_root)
        self._build_marginal(game_ui_asset_root)

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
        self.start_button = QPushButton("开始采集")
        self.start_button.setObjectName("btnPrimary")
        self.start_button.clicked.connect(self.start_requested)
        actions.addWidget(self.start_button)
        self.stop_button = QPushButton("结束并生成战报")
        self.stop_button.setObjectName("btnDanger")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_requested)
        actions.addWidget(self.stop_button)
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
        self.marginal_page.restore_saved_requested.connect(
            self.marginal_restore_requested
        )
        self.marginal_page.analysis_requested.connect(
            self.marginal_analysis_requested
        )
        self._stack.addWidget(self.marginal_page)

    def show_marginal(self, editor_data: dict) -> None:
        self.marginal_page.set_editor_data(editor_data)
        self._stack.setCurrentWidget(self.marginal_page)

    def show_report(self) -> None:
        self.marginal_page.clear_candidate()
        self._stack.setCurrentIndex(0)

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
        self.start_button.setEnabled(not state.running)
        self.stop_button.setEnabled(state.running and state.phase != "stopping")
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
        self._latest_summary = None
        self._detail_scope = "current"
        for label in self.metric_labels.values():
            label.setText("—")
        self.long_analysis_view.clear()

    def set_analysis(self, analysis, *, selected_character_id=None) -> None:
        if self._latest_summary is not None:
            raw_damage = max(0.0, float(self._latest_summary.total_damage))
            overkill_correction = (
                analysis.timeline_damage_correction_total
                if analysis.axis_complete
                else 0.0
            )
            corrected_damage = max(
                0.0,
                raw_damage - overkill_correction,
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
        self.marginal_page.set_analysis(analysis)

    def set_marginal_analysis(self, analysis) -> None:
        """Update the selected-role half without replacing report scope."""

        self.marginal_page.set_analysis(analysis)

    def complete_analysis_details(self, kind: str, payload: object) -> None:
        self.long_analysis_view.complete_analysis_details(kind, payload)

    def begin_analysis_details(self, kind: str) -> None:
        self.analysis_progress.show_for(kind)

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
