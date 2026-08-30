# 在战报页内展示固定轴配置反事实，并复用角色页完整配置编辑器。
"""Dedicated fixed-axis marginal page for one battle report."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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
    BattleCharacterBaseline,
)
from src.features.battle_report.analysis_components import (
    analysis_section,
    analysis_table,
)
from src.features.battle_report.build_change_summary import (
    character_level_summary,
    fork_level_summary,
)
from src.features.battle_report.composition_view import BattleDamageCompositionPanel
from src.features.battle_report.hit_formula_dialog import BattleHitFormulaDialog
from src.features.battle_report.marginal_derived_settlement_view import BattleMarginalDerivedSettlementView
from src.features.battle_report.marginal_buff_render_mixin import (
    BattleMarginalBuffRenderMixin,
)
from src.features.battle_report.marginal_result_table_view import (
    BUFF_BENEFIT_HEADERS,
    BUFF_BENEFIT_WIDTHS,
    display_projection,
    render_attribute_results,
)
from src.features.battle_report.marginal_toolbar import build_marginal_toolbar
from src.features.battle_report.marginal_replacement_controller import (
    show_marginal_equipment_replacement,
)
from src.features.battle_report.role_contribution_view import (
    BattleRoleDamagePieWidget,
    render_counterfactual_roles,
)
from src.features.battle_report.timeline_layout import TimelineSelection
from src.features.battle_report.timeline_view import BattleUnifiedTimelineWidget
from src.features.official_role.profile_editor import OfficialRoleProfileEditor
from src.services.battle_build_equipment_service import freeze_equipment_context
from src.services.battle_build_timeline_projection_service import (
    BattleBuildTimelineProjectionService,
)
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidateService,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    ELAPSED_TIME_MODE,
)
from src.ui.dashboard_widgets import metric_card
from src.ui.widgets import NoWheelComboBox


def _number(value: float) -> str:
    return f"{value:,.0f}"


class BattleMarginalPage(BattleMarginalBuffRenderMixin, QWidget):
    """Edit one memory-only candidate and replay the selected role's battle half."""

    back_requested = Signal()
    recalculate_requested = Signal(object)
    reset_requested = Signal()
    draft_changed = Signal()
    role_changed = Signal(object)

    def __init__(self, *, game_ui_asset_root=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._game_ui_asset_root = game_ui_asset_root
        self._analysis: BattleAnalysisSnapshot | None = None
        self._candidate_analysis: BattleAnalysisSnapshot | None = None
        self._details: list[dict] = []
        self._editors: list[OfficialRoleProfileEditor | None] = []
        self._editor_character_ids: list[int] = []
        self._equipment_editable = True
        self._inferred_fact_ids: tuple[str, ...] = ()
        self._draft_dirty = False
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        toolbar = build_marginal_toolbar(
            back=self.back_requested.emit,
            role_changed=self._character_changed,
            inferred_toggled=self._mark_draft_changed,
            reset=self._reset_draft,
            recalculate=self._request_recalculate,
        )
        self.character_combo = toolbar.character_combo
        self.change_summary = toolbar.change_summary
        self.use_inferred_facts = toolbar.use_inferred_facts
        self.reset_button = toolbar.reset_button
        self.recalculate_button = toolbar.recalculate_button
        outer.addWidget(toolbar.widget)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(22, 14, 22, 22)
        root.setSpacing(14)

        metrics = QGridLayout()
        definitions = (
            ("dps", "当前/候选 DPS", "固定轴估计"),
            ("damage", "当前/候选总伤害", "相对当前生效基线"),
            ("role", "角色伤害", "当前分析角色"),
            ("structured", "结构化重放", "其余为分级估计"),
        )
        self.metric_labels: dict[str, QLabel] = {}
        self.metric_subtitles: dict[str, QLabel] = {}
        for column, (key, title, subtitle) in enumerate(definitions):
            card, value, sub = metric_card(title, "—", subtitle)
            self.metric_labels[key] = value
            self.metric_subtitles[key] = sub
            metrics.addWidget(card, 0, column)
        root.addLayout(metrics)

        self.derived_settlements = BattleMarginalDerivedSettlementView()
        self.derived_settlements.hit_activated.connect(
            lambda event_id: self._open_counterfactual_hit(TimelineSelection("hit", event_id, None))
        )
        root.addWidget(self.derived_settlements)

        timeline_card, timeline_layout = analysis_section("调整后逐击轴")
        timeline_controls = QHBoxLayout()
        timeline_controls.addWidget(QLabel("口径"))
        self.timeline_time_mode_combo = NoWheelComboBox()
        self.timeline_time_mode_combo.addItem("扣除时停", ACTIVE_TIME_MODE)
        self.timeline_time_mode_combo.addItem("包含时停", ELAPSED_TIME_MODE)
        self.timeline_time_mode_combo.setCurrentIndex(
            self.timeline_time_mode_combo.findData(ELAPSED_TIME_MODE)
        )
        self.timeline_time_mode_combo.currentIndexChanged.connect(
            self._timeline_time_mode_changed
        )
        timeline_controls.addWidget(self.timeline_time_mode_combo)
        timeline_controls.addWidget(QLabel("缩放"))
        self.timeline_zoom_combo = NoWheelComboBox()
        for percent in (10, 25, 50, 100, 200, 300, 400, 500, 600, 700, 800):
            self.timeline_zoom_combo.addItem(f"{percent}%", percent / 100.0)
        self.timeline_zoom_combo.setCurrentIndex(
            self.timeline_zoom_combo.findData(1.0)
        )
        self.timeline_zoom_combo.currentIndexChanged.connect(
            self._timeline_zoom_changed
        )
        timeline_controls.addWidget(self.timeline_zoom_combo)
        timeline_controls.addStretch()
        timeline_help_text = (
            "沿用原战报动作、命中和时段；完整或部分量化项使用相应投影值，"
            "未量化项保留原轴数值并在伤害名标成原轴占位。"
            "生命上限结算保留原角色归因；安魂曲五觉跟随噩梦伤害，"
            "法帝娅被动跟随其固有生命，证据不足才固定原值。"
        )
        timeline_help = QPushButton("?")
        timeline_help.setObjectName("btnHelp")
        timeline_help.setToolTip(timeline_help_text)
        timeline_help.clicked.connect(
            lambda _checked=False, button=timeline_help: show_help(
                button,
                "调整后逐击轴",
                timeline_help_text,
            )
        )
        timeline_controls.addWidget(timeline_help)
        timeline_layout.addLayout(timeline_controls)
        self.counterfactual_timeline = BattleUnifiedTimelineWidget(
            game_ui_asset_root=self._game_ui_asset_root
        )
        self.counterfactual_timeline.set_hit_heading(
            "调整后逐击（未量化项为原轴占位）"
        )
        self.counterfactual_timeline.selection_activated.connect(
            self._open_counterfactual_hit
        )
        self.counterfactual_timeline_scroll = QScrollArea()
        self.counterfactual_timeline_scroll.setWidgetResizable(True)
        self.counterfactual_timeline_scroll.setFrameShape(QFrame.NoFrame)
        self.counterfactual_timeline_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAsNeeded
        )
        self.counterfactual_timeline_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.counterfactual_timeline_scroll.setWidget(self.counterfactual_timeline)
        self.counterfactual_timeline_scroll.horizontalScrollBar().valueChanged.connect(
            self.counterfactual_timeline.set_horizontal_view_offset
        )
        self.counterfactual_timeline.horizontal_pan_requested.connect(
            self._pan_counterfactual_timeline
        )
        self.counterfactual_timeline.content_height_changed.connect(
            self._fit_counterfactual_timeline_height
        )
        self._fit_counterfactual_timeline_height(
            self.counterfactual_timeline.sizeHint().height()
        )
        timeline_layout.addWidget(self.counterfactual_timeline_scroll)
        root.addWidget(timeline_card)

        attribute_card, attribute_layout = analysis_section("属性单位边际")
        attribute_note = QLabel(
            "默认单位为一格金色驱动词条；面板属性是当前生效基线，伤害加权平均属性按关联伤害发生时的"
            "动态属性加权。三个伤害百分比分别使用角色总伤害和全队总伤害作明确分母。"
        )
        attribute_note.setStyleSheet(
            themed_style("color:#8b949e;font-size:12px")
        )
        attribute_note.setWordWrap(True)
        attribute_layout.addWidget(attribute_note)
        self.attribute_table = analysis_table(
            (
                "属性单位",
                "面板属性",
                "伤害加权平均属性",
                "角色期望收益",
                "全队期望收益",
                "关联角色伤害",
                "角色伤害",
                "关联全队伤害",
            ),
            280,
            default_widths=(220, 125, 165, 145, 145, 135, 120, 145),
        )
        attribute_layout.addWidget(self.attribute_table)
        root.addWidget(attribute_card)

        buff_card, buff_layout = analysis_section("团队 Buff 边际")
        buff_note = QLabel(
            "逐个独立移除 Buff，并按实际造成伤害的角色拆分收益；"
            "角色收益之间可加总为该 Buff 的全队收益，不同 Buff 之间不可直接相加。"
            "具有正式逐击因果证据的机制被动也在此按来源角色合并展示。"
            "伤害覆盖率统计固定轴有效伤害，并包含由覆盖逐击联动的生命上限结算。"
        )
        buff_note.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        buff_note.setWordWrap(True)
        buff_layout.addWidget(buff_note)
        self.buff_benefit_table = analysis_table(
            BUFF_BENEFIT_HEADERS,
            240,
            default_widths=BUFF_BENEFIT_WIDTHS,
        )
        buff_layout.addWidget(self.buff_benefit_table)
        root.addWidget(buff_card)

        editor_card, editor_layout = analysis_section("角色配置")
        self.editor_stack = QStackedWidget()
        editor_layout.addWidget(self.editor_stack)
        root.addWidget(editor_card)

        roles_card, roles_layout = analysis_section("重算后角色贡献")
        roles_row = QHBoxLayout()
        self.roles_table = analysis_table(
            ("角色", "新伤害", "新占比", "角色收益"),
            180,
            default_widths=(180, 150, 150, 140),
        )
        roles_row.addWidget(self.roles_table, 3)
        self.roles_pie = BattleRoleDamagePieWidget()
        roles_row.addWidget(self.roles_pie, 2)
        roles_layout.addLayout(roles_row)
        root.addWidget(roles_card)

        composition_card, composition_layout = analysis_section("重算后角色伤害构成")
        self.composition_panel = BattleDamageCompositionPanel(
            game_ui_asset_root=self._game_ui_asset_root
        )
        composition_layout.addWidget(self.composition_panel)
        root.addWidget(composition_card)
        root.addStretch()

    def set_editor_data(
        self,
        editor_data: dict,
        *,
        selected_character_id: int | None = None,
    ) -> None:
        self._draft_dirty = False
        self._load_editor_data(
            editor_data,
            selected_character_id=selected_character_id,
        )

    def _load_editor_data(
        self,
        editor_data: dict,
        *,
        selected_character_id: int | None = None,
    ) -> None:
        while self.editor_stack.count():
            widget = self.editor_stack.widget(0)
            self.editor_stack.removeWidget(widget)
            widget.deleteLater()
        self._editors.clear()
        self._details.clear()
        self._editor_character_ids.clear()
        self._equipment_editable = bool(
            editor_data.get("marginal_equipment_editable", True)
        )
        self._inferred_fact_ids = tuple(
            str(getattr(fact, "fact_id", ""))
            for fact in editor_data.get("inferred_character_facts") or ()
            if str(getattr(fact, "fact_id", ""))
        )
        self.use_inferred_facts.blockSignals(True)
        self.use_inferred_facts.setChecked(True)
        self.use_inferred_facts.blockSignals(False)
        self.use_inferred_facts.setVisible(bool(self._inferred_fact_ids))
        self.character_combo.blockSignals(True)
        self.character_combo.clear()
        for detail in editor_data.get("details") or ():
            character_id = int(detail["character"]["character_id"])
            name = str(detail["character"].get("name_zh") or character_id)
            self._details.append(detail)
            self._editors.append(None)
            self._editor_character_ids.append(character_id)
            self.editor_stack.addWidget(QWidget())
            self.character_combo.addItem(name, character_id)
        if selected_character_id is not None:
            selected_index = self.character_combo.findData(selected_character_id)
            if selected_index >= 0:
                self.character_combo.setCurrentIndex(selected_index)
        self.character_combo.blockSignals(False)
        self._character_changed()

    def clear_candidate(self) -> None:
        self._load_editor_data({"details": [], "marginal_equipment_editable": True})
        self._draft_dirty = False
        self._analysis = None
        self._candidate_analysis = None
        for label in self.metric_labels.values():
            label.setText("—")
        for key, text in {
            "dps": "固定轴估计",
            "damage": "相对当前生效基线",
            "role": "当前分析角色",
            "structured": "其余为分级估计",
        }.items():
            self.metric_subtitles[key].setText(text)
        self.counterfactual_timeline.set_analysis(None)
        self.composition_panel.clear()
        self.attribute_table.setRowCount(0)
        self.buff_benefit_table.setRowCount(0)
        self.roles_table.setRowCount(0)
        self.roles_pie.set_roles(())
        self.derived_settlements.render(None)

    def set_source_analysis(self, analysis: BattleAnalysisSnapshot) -> None:
        self._render_analysis(analysis)

    def set_marginal_result(self, analysis: BattleAnalysisSnapshot) -> None:
        self._draft_dirty = False
        self._render_analysis(analysis)

    def _render_analysis(self, analysis: BattleAnalysisSnapshot) -> None:
        self._analysis = analysis
        comparison = analysis.build_counterfactual
        self.derived_settlements.render(comparison)
        if comparison is None:
            self._candidate_analysis = analysis
            self.counterfactual_timeline.set_analysis(analysis)
            self.metric_labels["dps"].setText(_number(analysis.effective_dps))
            self.metric_labels["damage"].setText(_number(analysis.effective_damage))
            self.metric_subtitles["damage"].setText(
                "+0.00% · 当前生效基线（本次未修改）"
            )
            self.metric_labels["structured"].setText("—")
            self.roles_table.setRowCount(0)
            self.roles_pie.set_roles(())
            self.composition_panel.clear()
        else:
            self._candidate_analysis = BattleBuildTimelineProjectionService.project(
                analysis,
                comparison,
            )
            self.counterfactual_timeline.set_analysis(self._candidate_analysis)
            projected_damage = display_projection(
                candidate=comparison.candidate_damage,
                heuristic=comparison.heuristic_projection_damage,
                known=comparison.known_projection_damage,
            )
            projected_dps = display_projection(
                candidate=comparison.candidate_dps,
                heuristic=comparison.heuristic_projection_dps,
                known=comparison.known_projection_dps,
            )
            gain = (
                None
                if projected_damage is None or not comparison.baseline_damage
                else (
                    projected_damage / comparison.baseline_damage - 1.0
                ) * 100.0
            )
            self.metric_labels["dps"].setText(
                "—" if projected_dps is None else _number(projected_dps)
            )
            self.metric_labels["damage"].setText(
                "—" if projected_damage is None else _number(projected_damage)
            )
            self.metric_subtitles["damage"].setText(
                "等待候选重算"
                if gain is None
                else f"{gain:+.2f}% · 基线 {_number(comparison.baseline_damage)}"
            )
            self.metric_labels["structured"].setText(
                f"{comparison.structured_percent:.1f}%"
            )
            self.metric_subtitles["structured"].setText(
                f"估计 {max(0.0, 100.0 - comparison.structured_percent):.1f}%"
            )
            render_counterfactual_roles(
                self.roles_table, self.roles_pie,
                comparison.roles, projected_damage,
            )
            if comparison.quantification.status == "unavailable":
                self.composition_panel.clear()
            else:
                self.composition_panel.render(comparison.composition)
        self._render_selected_role()

    def _timeline_time_mode_changed(self, _index: int = -1) -> None:
        mode = str(self.timeline_time_mode_combo.currentData() or ACTIVE_TIME_MODE)
        self.counterfactual_timeline.set_time_mode(mode)

    def _timeline_zoom_changed(self, _index: int = -1) -> None:
        factor = float(self.timeline_zoom_combo.currentData() or 1.0)
        self.counterfactual_timeline.set_zoom_factor(factor)

    def _pan_counterfactual_timeline(self, delta: int) -> None:
        scrollbar = self.counterfactual_timeline_scroll.horizontalScrollBar()
        scrollbar.setValue(scrollbar.value() + delta)

    def _fit_counterfactual_timeline_height(self, height: int) -> None:
        scrollbar = self.counterfactual_timeline_scroll.horizontalScrollBar()
        scrollbar_height = scrollbar.sizeHint().height()
        self.counterfactual_timeline_scroll.setFixedHeight(
            max(300, int(height)) + scrollbar_height + 2
        )

    def _open_counterfactual_hit(self, selection: TimelineSelection) -> None:
        analysis = self._analysis
        candidate = self._candidate_analysis
        if analysis is None or candidate is None or selection.kind != "hit":
            return
        comparison = analysis.build_counterfactual
        comparison_hits = comparison.hits if comparison is not None else ()
        related_counterfactuals = tuple(
            row
            for row in comparison_hits
            if row.source_event_id == selection.item_id
        )
        projection = next(
            (row for row in comparison_hits if row.event_id == selection.item_id),
            None,
        )
        original_hit = next(
            (
                row
                for snapshot in (analysis, candidate)
                for row in (*snapshot.hits, *snapshot.timeline_hits)
                if row.event_id == selection.item_id
            ),
            selection.payload,
        )
        if getattr(original_hit, "event_id", None) != selection.item_id:
            return
        replay = next(
            (
                row
                for snapshot in (analysis, candidate)
                for row in snapshot.hit_replays
                if row.event_id == selection.item_id
            ),
            None,
        )
        active_buffs = tuple(
            row
            for row in candidate.buff_intervals
            if row.start_us <= original_hit.relative_time_us < row.end_us
            and (
                row.target_scope in {"team", "target", "unknown"}
                or (
                    row.target_scope == "self"
                    and row.source_character_id == original_hit.character_id
                )
            )
        )
        dialog = getattr(self, "_counterfactual_hit_dialog", None)
        if dialog is None:
            dialog = BattleHitFormulaDialog(self)
            dialog.setWindowTitle("边际逐击详情")
            self._counterfactual_hit_dialog = dialog
        dialog.show_for_hit(
            original_hit,
            replay,
            active_buffs=active_buffs,
            counterfactual=projection,
            related_counterfactuals=related_counterfactuals,
            related_analysis=candidate,
        )

    def profiles(self) -> list[dict]:
        profiles = []
        for index, character_id in enumerate(self._editor_character_ids):
            editor = self._editors[index]
            detail = self._details[index]
            if editor is None:
                profile = dict(detail.get("profile") or {})
                context_key = str(
                    detail.get("selected_equipment_context_key") or "battle"
                )
                context = (detail.get("equipment_contexts") or {}).get(context_key)
                selection = None if context is None else (context_key, context)
            else:
                profile = editor.profile()
                selection = editor.selected_equipment_context()
            profile.pop("battle_stat_overrides", None)
            if selection is None:
                raise ValueError("战报边际候选缺少计算配装")
            context_key, context = selection
            profile.update({
                "equipment_context_key": context_key,
                "equipment_context_title": str(
                    context.get("source_title") or context.get("title") or "战报配装副本"
                ),
                "equipment_source_kind": str(context.get("source_kind") or "edited_copy"),
                "equipment_override": freeze_equipment_context(context),
            })
            profiles.append(profile)
        return profiles

    def selected_character_id(self) -> int | None:
        value = self.character_combo.currentData()
        return None if value is None else int(value)

    def selected_detail_scope(self) -> str | None:
        index = self.character_combo.currentIndex()
        if not 0 <= index < len(self._details):
            return None
        scope = self._details[index].get("analysis_detail_scope")
        return str(scope) if scope in {"first", "second"} else None

    def equipment_editable(self) -> bool:
        return self._equipment_editable

    def allows_automatic_recalculation(self) -> bool:
        return not self._draft_dirty

    def disabled_inferred_fact_ids(self) -> tuple[str, ...]:
        return () if self.use_inferred_facts.isChecked() else self._inferred_fact_ids

    def _character_changed(self, _index: int = -1) -> None:
        index = self.character_combo.currentIndex()
        if 0 <= index < self.editor_stack.count():
            self._ensure_editor(index)
            self.editor_stack.setCurrentIndex(index)
        self._render_selected_role()
        self._refresh_change_summary()
        self.role_changed.emit(self.selected_detail_scope())

    def _refresh_change_summary(self, *_args) -> None:
        index = self.character_combo.currentIndex()
        if not 0 <= index < len(self._editors):
            self.change_summary.setText("等待角色配置")
            self.change_summary.setToolTip("")
            return
        editor = self._ensure_editor(index)
        try:
            profile = editor.profile()
            equipment = editor.selected_equipment_context()
        except (KeyError, TypeError, ValueError):
            self.change_summary.setText("当前候选尚未完整")
            self.change_summary.setToolTip("请完成角色养成与可冻结配装选择。")
            return
        awakening_count = len(profile.get("selected_awaken_effect_ids") or ())
        skill_levels = tuple(
            int(value) for value in (profile.get("skill_levels") or {}).values()
        )
        items = tuple((equipment or ("", {}))[1].get("items") or ())
        core_count = sum(str(item.get("kind") or "") == "core" for item in items)
        drive_count = sum(str(item.get("kind") or "") != "core" for item in items)
        parts = [
            character_level_summary(self._details[index], profile),
            f"{awakening_count}觉",
            "好感10" if profile.get("likeability_level_10_enabled") else "未启用好感10",
            fork_level_summary(self._details[index], profile),
            "技能 " + ("/".join(str(value) for value in skill_levels) or "未配置"),
            f"空幕{core_count}/驱动{drive_count}",
        ]
        summary = " · ".join(parts)
        if self._draft_dirty:
            summary += " · 配置已变化，待重算"
        self.change_summary.setText(summary)
        self.change_summary.setToolTip("当前候选：" + summary)

    def _ensure_editor(self, index: int) -> OfficialRoleProfileEditor:
        existing = self._editors[index]
        if existing is not None:
            return existing
        host = self.window()
        editor = OfficialRoleProfileEditor(
            self._details[index],
            self,
            include_analysis=False,
            include_equipment=True,
            allow_equipment_replacement=self._equipment_editable,
            show_equipment_context_selector=False,
            equipment_replacement_handler=(
                lambda target, context_key, current=index: (
                    self._replace_equipment(current, target, context_key)
                )
            ),
            scoring_engine=getattr(host, "scoring_engine", None),
            shape_areas=getattr(host, "_shape_areas", {}),
        )
        placeholder = self.editor_stack.widget(index)
        self.editor_stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self.editor_stack.insertWidget(index, editor)
        self._editors[index] = editor
        editor.changed.connect(self._mark_draft_changed)
        return editor

    def _replace_equipment(
        self,
        index: int,
        target: dict,
        context_key: str,
    ) -> bool:
        if not self._equipment_editable or not 0 <= index < len(self._details):
            return False
        detail = self._details[index]
        context = (detail.get("equipment_contexts") or {}).get(context_key)
        if not isinstance(context, dict):
            return False

        def apply_replacement(replacement) -> None:
            BattleMarginalCandidateService.replace_equipment(
                context,
                target,
                replacement,
            )

        accepted = show_marginal_equipment_replacement(
            self,
            detail,
            target,
            context_key=context_key,
            on_replaced=apply_replacement,
        )
        if accepted:
            self._mark_draft_changed()
        return accepted

    def _mark_draft_changed(self, *_args) -> None:
        if not self._details:
            return
        self._draft_dirty = True
        self._refresh_change_summary()
        self.draft_changed.emit()

    def _reset_draft(self) -> None:
        self.reset_requested.emit()

    def _render_selected_role(self) -> None:
        analysis = self._analysis
        character_id = self.selected_character_id()
        if analysis is None or character_id is None:
            self.attribute_table.setRowCount(0)
            self.buff_benefit_table.setRowCount(0)
            self.metric_labels["role"].setText("—")
            return
        baseline = next(
            (row for row in analysis.baselines if row.character_id == character_id),
            None,
        )
        self._render_attributes(baseline)
        self._render_buff_benefits(
            analysis.buff_counterfactuals,
            passive_results=getattr(analysis, "passive_counterfactuals", ()),
        )
        comparison = analysis.build_counterfactual
        role = next(
            (row for row in (comparison.roles if comparison else ()) if row.character_id == character_id),
            None,
        )
        if role is None:
            original = next(
                (row for row in analysis.roles if row.character_id == character_id),
                None,
            )
            self.metric_labels["role"].setText(
                "—" if original is None else _number(original.damage)
            )
            self.metric_subtitles["role"].setText(
                "+0.00% · 当前生效基线（本次未修改）"
            )
        else:
            projected_damage = display_projection(
                candidate=role.candidate_damage,
                heuristic=role.heuristic_projection_damage,
                known=role.known_projection_damage,
            )
            gain = (
                None
                if projected_damage is None or not role.baseline_damage
                else (projected_damage / role.baseline_damage - 1.0) * 100.0
            )
            self.metric_labels["role"].setText(
                "—" if projected_damage is None else _number(projected_damage)
            )
            self.metric_subtitles["role"].setText(
                "等待候选重算"
                if gain is None
                else f"{gain:+.2f}% · 基线 {_number(role.baseline_damage)}"
            )

    def _render_attributes(self, baseline: BattleCharacterBaseline | None) -> None:
        analysis = self._analysis
        if baseline is None or analysis is None:
            self.attribute_table.setRowCount(0)
            return
        units = BattleMarginalCalculationService.default_units(
            baseline,
            hits=analysis.hits,
            replays={row.event_id: row for row in analysis.hit_replays},
        )
        results = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=baseline.character_id,
            edited_values={},
            units=units,
        )
        render_attribute_results(self.attribute_table, results)

    def _request_recalculate(self) -> None:
        self._refresh_change_summary()
        self.recalculate_requested.emit(self.profiles())
