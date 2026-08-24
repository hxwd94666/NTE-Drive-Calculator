# 在战报页内展示固定轴配置反事实，并复用角色页完整配置编辑器。
"""Dedicated fixed-axis marginal page for one battle report."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.dialogs import show_help
from src.domain.battle_report import (
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleRangeRoleSummary,
)
from src.features.battle_report.analysis_components import (
    analysis_section,
    analysis_table,
)
from src.features.battle_report.composition_view import BattleDamageCompositionPanel
from src.features.battle_report.hit_formula_dialog import BattleHitFormulaDialog
from src.features.battle_report.role_contribution_view import (
    BattleRoleDamagePieWidget,
    BattleRoleShareBar,
    role_contribution_color,
)
from src.features.battle_report.timeline_layout import TimelineSelection
from src.features.battle_report.timeline_view import BattleUnifiedTimelineWidget
from src.features.official_role.profile_editor import OfficialRoleProfileEditor
from src.services.battle_build_equipment_service import freeze_equipment_context
from src.services.battle_build_timeline_projection_service import (
    BattleBuildTimelineProjectionService,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    ELAPSED_TIME_MODE,
)
from src.ui.dashboard_widgets import metric_card
from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox


def _number(value: float) -> str:
    return f"{value:,.0f}"


class BattleMarginalPage(QWidget):
    """Edit one saved candidate and present its full-axis estimated result."""

    back_requested = Signal()
    recalculate_requested = Signal(object)
    import_role_page_requested = Signal()
    restore_original_requested = Signal()
    sync_role_page_requested = Signal()

    def __init__(self, *, game_ui_asset_root=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._game_ui_asset_root = game_ui_asset_root
        self._analysis: BattleAnalysisSnapshot | None = None
        self._candidate_analysis: BattleAnalysisSnapshot | None = None
        self._details: list[dict] = []
        self._editors: list[OfficialRoleProfileEditor | None] = []
        self._editor_character_ids: list[int] = []
        self._attribute_edits: dict[int, dict[str, float]] = {}
        self._build()

    def _build(self) -> None:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(22, 18, 22, 22)
        root.setSpacing(14)

        header = QHBoxLayout()
        back = QPushButton("← 返回战报")
        back.clicked.connect(self.back_requested)
        header.addWidget(back)
        title = QLabel("固定轴边际计算")
        title.setObjectName("pageTitle")
        title.setToolTip(
            "冻结本场动作、逐击、目标和时段，只替换角色属性与配置。"
            "所有原轴逐击都会得到候选估计；特殊机制尚不能量化时按零增量保守估计。"
        )
        header.addWidget(title)
        header.addStretch()
        help_text = (
            "冻结本场动作、逐击、目标和时段，只替换角色属性与配置。"
            "所有原轴逐击都会得到候选估计；特殊机制尚不能量化时按零增量保守估计。"
        )
        help_button = QPushButton("?")
        help_button.setObjectName("btnHelp")
        help_button.setToolTip(help_text)
        help_button.clicked.connect(
            lambda _checked=False, button=help_button: show_help(
                button,
                "固定轴边际计算",
                help_text,
            )
        )
        header.addWidget(help_button)
        root.addLayout(header)

        selector = QHBoxLayout()
        selector.addWidget(QLabel("分析角色"))
        self.character_combo = NoWheelComboBox()
        self.character_combo.currentIndexChanged.connect(self._character_changed)
        selector.addWidget(self.character_combo)
        self.change_summary = QLabel("等待角色配置")
        self.change_summary.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        selector.addWidget(self.change_summary, 1)
        recalculate = QPushButton("重算")
        recalculate.setObjectName("btnPrimary")
        recalculate.clicked.connect(self._request_recalculate)
        selector.addWidget(recalculate)
        root.addLayout(selector)

        metrics = QGridLayout()
        definitions = (
            ("dps", "新 DPS", "固定轴估计"),
            ("damage", "新总伤害", "相对原始战报"),
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
            "沿用原战报动作、命中和时段；条与点的伤害大小使用调整后候选值。"
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
        self.counterfactual_timeline.set_hit_heading("调整后逐击（固定轴估计）")
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

        attribute_card, attribute_layout = analysis_section("属性调整")
        attribute_actions = QHBoxLayout()
        attribute_actions.addStretch()
        clear_attributes = QPushButton("清除手工属性")
        clear_attributes.setToolTip("清除当前角色的手工覆盖；点击重算后恢复由养成与配装自动生成。")
        clear_attributes.clicked.connect(self._clear_attribute_overrides)
        attribute_actions.addWidget(clear_attributes)
        attribute_layout.addLayout(attribute_actions)
        self.attribute_table = analysis_table(
            ("属性", "当前候选值", "调整值"),
            230,
            default_widths=(240, 170, 180),
        )
        attribute_layout.addWidget(self.attribute_table)
        root.addWidget(attribute_card)

        editor_card, editor_layout = analysis_section("角色配置")
        self.editor_stack = QStackedWidget()
        editor_layout.addWidget(self.editor_stack)
        actions = QHBoxLayout()
        from_role = QPushButton("从角色页同步")
        from_role.clicked.connect(self.import_role_page_requested)
        actions.addWidget(from_role)
        restore = QPushButton("恢复原始")
        restore.clicked.connect(self.restore_original_requested)
        actions.addWidget(restore)
        actions.addStretch()
        sync = QPushButton("同步养成到角色页")
        sync.clicked.connect(self.sync_role_page_requested)
        actions.addWidget(sync)
        editor_layout.addLayout(actions)
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

    def set_editor_data(self, editor_data: dict) -> None:
        while self.editor_stack.count():
            widget = self.editor_stack.widget(0)
            self.editor_stack.removeWidget(widget)
            widget.deleteLater()
        self._editors.clear()
        self._details.clear()
        self._editor_character_ids.clear()
        self._attribute_edits.clear()
        self.character_combo.blockSignals(True)
        self.character_combo.clear()
        for detail in editor_data.get("details") or ():
            character_id = int(detail["character"]["character_id"])
            name = str(detail["character"].get("name_zh") or character_id)
            self._details.append(detail)
            self._editors.append(None)
            self._editor_character_ids.append(character_id)
            self._attribute_edits[character_id] = {
                str(key): float(value)
                for key, value in (
                    (detail.get("profile") or {}).get("battle_stat_overrides")
                    or {}
                ).items()
            }
            self.editor_stack.addWidget(QWidget())
            self.character_combo.addItem(name, character_id)
        self.character_combo.blockSignals(False)
        self._character_changed()

    def set_analysis(self, analysis: BattleAnalysisSnapshot) -> None:
        self._analysis = analysis
        comparison = analysis.build_counterfactual
        if comparison is None:
            self._candidate_analysis = None
            self.counterfactual_timeline.set_analysis(None)
            self.metric_labels["dps"].setText(_number(analysis.effective_dps))
            self.metric_labels["damage"].setText(_number(analysis.effective_damage))
            self.metric_subtitles["damage"].setText("尚未保存修改副本")
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
            self.metric_labels["dps"].setText(_number(comparison.predicted_dps))
            self.metric_labels["damage"].setText(_number(comparison.predicted_damage))
            self.metric_subtitles["damage"].setText(
                f"{comparison.gain_percent:+.2f}% · 原始 {_number(comparison.baseline_damage)}"
            )
            self.metric_labels["structured"].setText(
                f"{comparison.structured_percent:.1f}%"
            )
            self.metric_subtitles["structured"].setText(
                f"估计 {100.0 - comparison.structured_percent:.1f}%"
            )
            self._render_roles(comparison.roles, comparison.predicted_damage)
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
        comparison = analysis.build_counterfactual if analysis is not None else None
        if (
            analysis is None
            or candidate is None
            or comparison is None
            or selection.kind != "hit"
        ):
            return
        projection = next(
            (row for row in comparison.hits if row.event_id == selection.item_id),
            None,
        )
        original_hit = next(
            (row for row in analysis.hits if row.event_id == selection.item_id),
            None,
        )
        if projection is None or original_hit is None:
            return
        replay = next(
            (row for row in candidate.hit_replays if row.event_id == selection.item_id),
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
            dialog.setWindowTitle("调整后逐击详情")
            self._counterfactual_hit_dialog = dialog
        dialog.show_for_hit(
            original_hit,
            replay,
            active_buffs=active_buffs,
            counterfactual=projection,
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
            if selection is None:
                raise ValueError("战报角色副本缺少边际配装上下文")
            context_key, context = selection
            profile.update({
                "equipment_context_key": context_key,
                "equipment_context_title": str(
                    context.get("source_title") or context.get("title") or "战报配装副本"
                ),
                "equipment_source_kind": str(context.get("source_kind") or "edited_copy"),
                "equipment_override": freeze_equipment_context(context),
                "battle_stat_overrides": dict(self._attribute_edits.get(character_id, {})),
            })
            profiles.append(profile)
        return profiles

    def selected_character_id(self) -> int | None:
        value = self.character_combo.currentData()
        return None if value is None else int(value)

    def _character_changed(self, _index: int = -1) -> None:
        index = self.character_combo.currentIndex()
        if 0 <= index < self.editor_stack.count():
            self._ensure_editor(index)
            self.editor_stack.setCurrentIndex(index)
        self._render_selected_role()
        self._refresh_change_summary()

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
        fork_refinement = profile.get("fork_refinement_level")
        items = tuple((equipment or ("", {}))[1].get("items") or ())
        core_count = sum(str(item.get("kind") or "") == "core" for item in items)
        drive_count = sum(str(item.get("kind") or "") != "core" for item in items)
        parts = [
            f"Lv.{int(profile.get('character_level') or 1)}",
            f"{awakening_count}觉",
            "好感10" if profile.get("likeability_level_10_enabled") else "未启用好感10",
            "未装备弧盘" if not profile.get("fork_id") else f"弧盘精{int(fork_refinement or 1)}",
            "技能 " + ("/".join(str(value) for value in skill_levels) or "未配置"),
            f"空幕{core_count}/驱动{drive_count}",
        ]
        summary = " · ".join(parts)
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
            scoring_engine=getattr(host, "scoring_engine", None),
            shape_areas=getattr(host, "_shape_areas", {}),
        )
        placeholder = self.editor_stack.widget(index)
        self.editor_stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self.editor_stack.insertWidget(index, editor)
        self._editors[index] = editor
        for widget in editor.findChildren(QAbstractButton):
            widget.clicked.connect(self._refresh_change_summary)
        for widget in editor.findChildren(QComboBox):
            widget.currentIndexChanged.connect(self._refresh_change_summary)
        for widget in editor.findChildren(QSpinBox):
            widget.valueChanged.connect(self._refresh_change_summary)
        for widget in editor.findChildren(QDoubleSpinBox):
            widget.valueChanged.connect(self._refresh_change_summary)
        return editor

    def _render_selected_role(self) -> None:
        analysis = self._analysis
        character_id = self.selected_character_id()
        if analysis is None or character_id is None:
            self.attribute_table.setRowCount(0)
            self.metric_labels["role"].setText("—")
            return
        baseline = next(
            (row for row in analysis.baselines if row.character_id == character_id),
            None,
        )
        self._render_attributes(baseline)
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
            self.metric_subtitles["role"].setText("等待候选重算")
        else:
            self.metric_labels["role"].setText(_number(role.predicted_damage))
            self.metric_subtitles["role"].setText(
                f"{role.gain_percent:+.2f}% · 原始 {_number(role.baseline_damage)}"
            )

    def _render_attributes(self, baseline: BattleCharacterBaseline | None) -> None:
        if baseline is None:
            self.attribute_table.setRowCount(0)
            return
        edits = self._attribute_edits.setdefault(baseline.character_id, {})
        self.attribute_table.setRowCount(len(baseline.stats))
        for row_index, stat in enumerate(baseline.stats):
            self.attribute_table.setItem(row_index, 0, QTableWidgetItem(stat.label))
            current = f"{stat.value * 100:.2f}%" if stat.is_percent else f"{stat.value:,.2f}"
            self.attribute_table.setItem(row_index, 1, QTableWidgetItem(current))
            editor = NoWheelDoubleSpinBox()
            editor.setDecimals(4 if stat.is_percent else 2)
            editor.setRange(-999999.0, 9999999.0)
            editor.setSuffix("%" if stat.is_percent else "")
            editor.setValue(edits.get(stat.property_id, stat.value) * (100 if stat.is_percent else 1))
            editor.valueChanged.connect(
                lambda value, cid=baseline.character_id, key=stat.property_id, percent=stat.is_percent: self._attribute_changed(
                    cid, key, value, percent
                )
            )
            self.attribute_table.setCellWidget(row_index, 2, editor)

    def _attribute_changed(
        self,
        character_id: int,
        property_id: str,
        value: float,
        percent: bool,
    ) -> None:
        self._attribute_edits.setdefault(character_id, {})[property_id] = (
            value / 100.0 if percent else value
        )

    def _clear_attribute_overrides(self) -> None:
        character_id = self.selected_character_id()
        if character_id is None:
            return
        self._attribute_edits[character_id] = {}
        self._render_selected_role()

    def _request_recalculate(self) -> None:
        self._refresh_change_summary()
        self.recalculate_requested.emit(self.profiles())

    def _render_roles(self, roles, predicted_total: float) -> None:
        self.roles_table.setRowCount(len(roles))
        pie_rows = []
        for row_index, role in enumerate(roles):
            share = role.predicted_damage / predicted_total * 100.0 if predicted_total else 0.0
            self.roles_table.setItem(row_index, 0, QTableWidgetItem(role.character_name))
            self.roles_table.setItem(row_index, 1, QTableWidgetItem(_number(role.predicted_damage)))
            self.roles_table.setCellWidget(
                row_index,
                2,
                BattleRoleShareBar(share_percent=share, color=role_contribution_color(row_index)),
            )
            self.roles_table.setItem(row_index, 3, QTableWidgetItem(f"{role.gain_percent:+.2f}%"))
            pie_rows.append(BattleRangeRoleSummary(
                character_id=role.character_id,
                character_name=role.character_name,
                hits=0,
                damage=role.predicted_damage,
                dps=0.0,
                share_percent=share,
            ))
        self.roles_pie.set_roles(tuple(pie_rows))
