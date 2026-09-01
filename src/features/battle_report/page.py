# 展示 nte-core 实时聚合伤害数据和历史战报。
"""Battle report page displaying live nte-core aggregate damage data."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.i18n import tr, display_term
from src.app.theme import themed_style
from src.domain.battle_report import (
    BattleCaptureState,
    BattleCharacterSummary,
    BattleSkillSummary,
    BattleSummary,
    active_abyss_half,
)
from src.features.battle_report.composition_view import BattleDamageCompositionPanel
from src.services.battle_damage_composition_service import (
    BattleDamageCompositionService,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.ui.dashboard_widgets import metric_card, set_status_badge


def _section(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    title_label = QLabel(title)
    title_label.setObjectName("cardTitle")
    layout.addWidget(title_label)
    if description:
        subtitle = QLabel(description)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(subtitle)
    return card, layout


def _format_number(value: float) -> str:
    return f"{value:,.0f}"


class ProportionalTableWidget(QTableWidget):
    """Keep table columns at stable percentages of the visible table width."""

    def __init__(
        self,
        column_ratios: tuple[float, ...],
        parent: QWidget | None = None,
    ) -> None:
        if not column_ratios or any(ratio <= 0 for ratio in column_ratios):
            raise ValueError("column ratios must contain positive values")
        total = sum(column_ratios)
        self._column_ratios = tuple(ratio / total for ratio in column_ratios)
        super().__init__(0, len(column_ratios), parent)
        self.horizontalHeader().setSectionsMovable(False)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.refresh_proportional_columns()

    def refresh_proportional_columns(self) -> None:
        available_width = self.viewport().width()
        if available_width <= 0:
            return
        consumed = 0
        last_column = len(self._column_ratios) - 1
        for column, ratio in enumerate(self._column_ratios):
            width = (
                available_width - consumed
                if column == last_column
                else round(available_width * ratio)
            )
            self.setColumnWidth(column, max(1, width))
            consumed += width


class BattleReportPage(QWidget):
    start_requested = Signal()
    stop_requested = Signal()
    overlay_visibility_changed = Signal(bool)
    overlay_passthrough_changed = Signal(bool)
    detail_scope_changed = Signal(str)
    save_result_requested = Signal()
    history_requested = Signal()

    def __init__(self, *, game_ui_asset_root, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._asset_catalog = GameUiAssetCatalog(game_ui_asset_root)
        self._latest_summary: BattleSummary | None = None
        self._detail_scope = "current"
        self._build()

    def _build(self) -> None:
        content = QWidget()
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        root = QVBoxLayout(content)
        root.setContentsMargins(22, 18, 22, 22)
        root.setSpacing(16)

        control_card, control_layout = _section(
            tr("战报采集"),
            tr("使用 nte-core 实时统计队伍与技能伤害。采集期间会暂停背包同步，结束后自动恢复。"),
        )
        status_row = QHBoxLayout()
        self.status_badge = QLabel()
        self.status_badge.setAlignment(Qt.AlignCenter)
        set_status_badge(self.status_badge, tr("未开始"), "neutral")
        self.status_detail = QLabel(tr("尚未开始战报采集。"))
        self.status_detail.setWordWrap(True)
        self.status_detail.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        status_row.addWidget(self.status_badge)
        status_row.addWidget(self.status_detail, 1)
        control_layout.addLayout(status_row)
        action_row = QHBoxLayout()
        self.start_button = QPushButton(tr("开始采集"))
        self.start_button.setObjectName("btnPrimary")
        self.start_button.clicked.connect(self.start_requested)
        self.stop_button = QPushButton(tr("结束并生成战报"))
        self.stop_button.setObjectName("btnDanger")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_requested)
        self.save_result_button = QPushButton(tr("保存伤害结果"))
        self.save_result_button.setEnabled(False)
        self.save_result_button.clicked.connect(self.save_result_requested)
        self.history_button = QPushButton(tr("读取历史战报"))
        self.history_button.clicked.connect(self.history_requested)
        self.overlay_toggle = QCheckBox(tr("显示实时悬浮窗"))
        self.overlay_toggle.setChecked(True)
        self.overlay_toggle.toggled.connect(self.overlay_visibility_changed)
        self.passthrough_toggle = QCheckBox(tr("鼠标穿透"))
        self.passthrough_toggle.setChecked(True)
        self.passthrough_toggle.setToolTip(tr("关闭后可以拖动悬浮窗，开启后鼠标操作会落到游戏窗口。"))
        self.passthrough_toggle.toggled.connect(self.overlay_passthrough_changed)
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.stop_button)
        action_row.addWidget(self.save_result_button)
        action_row.addWidget(self.history_button)
        action_row.addSpacing(12)
        action_row.addWidget(self.overlay_toggle)
        action_row.addWidget(self.passthrough_toggle)
        action_row.addStretch()
        control_layout.addLayout(action_row)
        root.addWidget(control_card)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(12)
        definitions = (
            ("dps", tr("队伍 DPS"), tr("扣除停表时间")),
            ("damage", tr("总伤害"), tr("当前统计范围")),
            ("duration", tr("战斗时长"), tr("秒")),
            ("taken", tr("承受伤害"), tr("全队")),
        )
        self.metric_labels: dict[str, QLabel] = {}
        for column, (key, title, subtitle) in enumerate(definitions):
            card, value_label, _ = metric_card(title, "—", subtitle)
            self.metric_labels[key] = value_label
            metrics.addWidget(card, 0, column)
        root.addLayout(metrics)

        character_card, character_layout = _section(
            tr("角色伤害贡献"),
            tr("范围切换只影响角色贡献和技能伤害明细；顶部汇总始终显示整场统计。"),
        )
        scope_row = QHBoxLayout()
        scope_title = QLabel(tr("明细范围"))
        scope_title.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        scope_row.addWidget(scope_title)
        self.scope_button_group = QButtonGroup(self)
        self.scope_button_group.setExclusive(True)
        self.scope_buttons: dict[str, QPushButton] = {}
        scope_button_style = themed_style(
            "QPushButton{padding:4px 13px;font-size:11px;}"
            "QPushButton:checked{background:#1f6feb55;color:#58a6ff;"
            "border-color:#1f6feb;font-weight:600;}"
        )
        for mode, label in (
            ("current", tr("跟随当前")),
            ("first", tr("上半")),
            ("second", tr("下半")),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setStyleSheet(scope_button_style)
            button.clicked.connect(
                lambda _checked=False, selected_mode=mode: self._select_detail_scope(
                    selected_mode
                )
            )
            self.scope_button_group.addButton(button)
            self.scope_buttons[mode] = button
            scope_row.addWidget(button)
        self.scope_buttons["current"].setChecked(True)
        self.scope_buttons["first"].setEnabled(False)
        self.scope_buttons["second"].setEnabled(False)
        scope_row.addStretch()
        character_layout.addLayout(scope_row)
        self.scope_label = QLabel(tr("当前范围：等待战斗数据"))
        self.scope_label.setStyleSheet(
            themed_style("color:#58a6ff;font-size:12px;font-weight:600")
        )
        character_layout.addWidget(self.scope_label)
        self.character_table = ProportionalTableWidget(
            (0.30, 0.10, 0.16, 0.15, 0.13, 0.16)
        )
        self.character_table.setHorizontalHeaderLabels(
            (tr("角色"), tr("命中"), tr("伤害"), "DPS", tr("队伍占比"), tr("承伤"))
        )
        self.character_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.character_table.setSelectionMode(QTableWidget.NoSelection)
        self.character_table.verticalHeader().setVisible(False)
        header = self.character_table.horizontalHeader()
        header.setMinimumSectionSize(1)
        for column in range(self.character_table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        self.character_table.setMinimumHeight(210)
        character_layout.addWidget(self.character_table)
        root.addWidget(character_card)

        skill_card, skill_layout = _section(
            tr("技能伤害明细"),
            tr("按当前统计范围的累计伤害排序；技能分类来自 nte-core。"),
        )
        self.skill_table = ProportionalTableWidget(
            (0.16, 0.34, 0.16, 0.09, 0.15, 0.10)
        )
        self.skill_table.setHorizontalHeaderLabels(
            (tr("角色"), tr("技能"), tr("分类"), tr("命中"), tr("伤害"), tr("占比"))
        )
        self.skill_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.skill_table.setSelectionMode(QTableWidget.NoSelection)
        self.skill_table.verticalHeader().setVisible(False)
        skill_header = self.skill_table.horizontalHeader()
        skill_header.setMinimumSectionSize(1)
        for column in range(self.skill_table.columnCount()):
            skill_header.setSectionResizeMode(column, QHeaderView.Fixed)
        self.skill_table.setMinimumHeight(270)
        skill_layout.addWidget(self.skill_table)
        root.addWidget(skill_card)

        composition_card, composition_layout = _section(
            tr("角色伤害构成"),
            tr("按所选半场统计直伤、特殊伤害、具体环合及其他伤害；每张卡固定显示五行。"),
        )
        self.damage_composition_panel = BattleDamageCompositionPanel(
            game_ui_asset_root=self._asset_catalog.asset_root
        )
        composition_layout.addWidget(self.damage_composition_panel)
        root.addWidget(composition_card)

        self.quality_label = QLabel(tr("数据质量：等待采集"))
        self.quality_label.setWordWrap(True)
        self.quality_label.setStyleSheet(themed_style("color:#6e7681;font-size:11px"))
        root.addWidget(self.quality_label)
        root.addStretch()

    def update_state(self, state: BattleCaptureState) -> None:
        tones = {
            "starting": (tr("启动中"), "active"),
            "running": (tr("采集中"), "success"),
            "stopping": (tr("正在结束"), "warning"),
            "stopped": (tr("已结束"), "neutral"),
            "history": (tr("历史战报"), "neutral"),
            "error": (tr("采集异常"), "error"),
        }
        label, tone = tones.get(state.phase, (state.phase, "neutral"))
        set_status_badge(self.status_badge, label, tone)
        # state.message is produced by the domain/service layer in Chinese.
        detail = tr(state.message)
        if state.error:
            detail += tr("\n技术详情：{error}", error=state.error)
        self.status_detail.setText(detail)
        self.start_button.setEnabled(not state.running)
        self.stop_button.setEnabled(state.running and state.phase != "stopping")
        self.history_button.setEnabled(not state.running)
        is_manual = state.retention_kind == "manual"
        self.save_result_button.setText(
            tr("已手动保存") if is_manual else tr("保存伤害结果")
        )
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
        button = self.scope_buttons.get(mode)
        if button is None or not button.isEnabled():
            mode = "current"
            button = self.scope_buttons[mode]
        self._detail_scope = mode
        button.setChecked(True)
        if self._latest_summary is not None:
            self._render_damage_details(self._latest_summary)

    def detail_scope(self) -> str:
        return self._detail_scope

    def clear_summary(self) -> None:
        self._latest_summary = None
        self._detail_scope = "current"
        for label in self.metric_labels.values():
            label.setText("—")
        self.character_table.setRowCount(0)
        self.skill_table.setRowCount(0)
        self.scope_buttons["current"].setChecked(True)
        self.scope_buttons["first"].setEnabled(False)
        self.scope_buttons["second"].setEnabled(False)
        self.scope_label.setText(tr("当前范围：等待战斗数据"))
        self.damage_composition_panel.clear()
        self.quality_label.setText(tr("数据质量：等待采集"))

    def _render_summary(self, summary: BattleSummary) -> None:
        self._latest_summary = summary
        self.metric_labels["dps"].setText(_format_number(summary.total_dps))
        self.metric_labels["damage"].setText(_format_number(summary.total_damage))
        self.metric_labels["duration"].setText(f"{summary.duration_seconds:.1f}s")
        self.metric_labels["taken"].setText(_format_number(summary.total_damage_taken))
        self._update_detail_scope_availability(summary)
        self._render_damage_details(summary)
        quality = summary.quality
        self.quality_label.setText(
            tr(
                "数据质量：{source} · {packets} 个包 · {hits} 条伤害 · "
                "未识别角色 {unknown} 条 · 未映射技能 {unmapped} 条",
                source=quality.source,
                packets=f"{quality.packet_count:,}",
                hits=f"{quality.hit_count:,}",
                unknown=f"{quality.unknown_character_hits:,}",
                unmapped=f"{quality.unmapped_skill_hits:,}",
            )
        )

    def _select_detail_scope(self, mode: str) -> None:
        button = self.scope_buttons.get(mode)
        if button is None or not button.isEnabled():
            return
        self._detail_scope = mode
        button.setChecked(True)
        if self._latest_summary is not None:
            self._render_damage_details(self._latest_summary)
        self.detail_scope_changed.emit(mode)

    def _update_detail_scope_availability(self, summary: BattleSummary) -> None:
        first_available = summary.abyss.first_half is not None
        second_available = summary.abyss.second_half is not None
        self.scope_buttons["first"].setEnabled(first_available)
        self.scope_buttons["second"].setEnabled(second_available)
        selected = self.scope_buttons[self._detail_scope]
        if not selected.isEnabled():
            self._detail_scope = "current"
            self.scope_buttons["current"].setChecked(True)

    def _render_damage_details(self, summary: BattleSummary) -> None:
        if self._detail_scope == "first" and summary.abyss.first_half is not None:
            half = summary.abyss.first_half
            scope_name = tr("上半")
        elif self._detail_scope == "second" and summary.abyss.second_half is not None:
            half = summary.abyss.second_half
            scope_name = tr("下半")
        else:
            half = active_abyss_half(summary)
            scope_name = self._current_scope_name(summary)

        if half is None:
            characters = summary.characters
            skills = summary.skills
            segment_total_damage = summary.total_damage
        else:
            characters = half.characters
            skills = half.skills
            segment_total_damage = half.total_damage
        floor = tr("第 {floor} 层 · ", floor=summary.abyss.floor) if summary.abyss.floor else ""
        self.scope_label.setText(tr("当前范围：{scope}", scope=f"{floor}{scope_name}"))
        self._render_characters(characters)
        self._render_skills(skills)
        self.damage_composition_panel.render(
            BattleDamageCompositionService.calculate(
                characters=characters,
                skills=skills,
                segment_total_damage=segment_total_damage,
            )
        )

    @staticmethod
    def _current_scope_name(summary: BattleSummary) -> str:
        active = (summary.abyss.active_half or "").lower()
        if "ascending" in active or "first" in active or "上" in active:
            return tr("跟随当前 · 上半")
        if "descending" in active or "second" in active or "下" in active:
            return tr("跟随当前 · 下半")
        return tr("跟随当前")

    def _render_characters(
        self, characters: tuple[BattleCharacterSummary, ...]
    ) -> None:
        ordered = sorted(characters, key=lambda item: item.damage, reverse=True)
        self.character_table.setRowCount(len(ordered))
        for row, character in enumerate(ordered):
            identity = QWidget()
            identity_layout = QHBoxLayout(identity)
            identity_layout.setContentsMargins(4, 2, 4, 2)
            identity_layout.setSpacing(8)
            icon_path = self._asset_catalog.character_icon(character.character_id)
            if icon_path is not None:
                icon = QLabel()
                icon.setFixedSize(34, 34)
                icon.setPixmap(
                    QPixmap(str(icon_path)).scaled(
                        34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                )
                identity_layout.addWidget(icon)
            identity_layout.addWidget(QLabel(display_term(str(character.name))))
            identity_layout.addStretch()
            self.character_table.setCellWidget(row, 0, identity)
            values = (
                f"{character.hits:,}",
                _format_number(character.damage),
                _format_number(character.dps),
                f"{character.damage_share_percent:.1f}%",
                _format_number(character.damage_taken),
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.character_table.setItem(row, column, item)
            self.character_table.setRowHeight(row, 42)
        self.character_table.refresh_proportional_columns()

    def _render_skills(self, skills: tuple[BattleSkillSummary, ...]) -> None:
        ordered = sorted(skills, key=lambda item: item.damage, reverse=True)
        self.skill_table.setRowCount(len(ordered))
        for row, skill in enumerate(ordered):
            values = (
                display_term(str(skill.character_name)),
                # Skill names are shown exactly as nte-core reports them: a local
                # mapping would need re-exporting for every new character released.
                str(skill.name),
                tr(str(skill.category)),
                f"{skill.hits:,}",
                _format_number(skill.damage),
                f"{skill.damage_share_percent:.1f}%",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.skill_table.setItem(row, column, item)
        self.skill_table.refresh_proportional_columns()
