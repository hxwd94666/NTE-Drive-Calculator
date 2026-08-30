# 游戏资料库角色详情视图；只消费 DTO，不读取 SQLite 或账号状态。
"""Theme-aware, lazy character detail panel for the static catalog page."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.services.static_catalog_character_models import (
    CatalogSource,
    CharacterDetail,
    CombatLinkPage,
    GrowthPage,
)


_STATE_LABELS = {
    "normal": "普通等级",
    "breakthrough_before": "突破前",
    "breakthrough_after": "突破后",
    "max_level": "满级",
}


def _text(value: object) -> str:
    return "—" if value is None or value == "" else str(value)


def _number(value: float) -> str:
    return f"{value:,.3f}".rstrip("0").rstrip(".")


def _source_label(source: CatalogSource | None) -> str:
    if source is None:
        return "—"
    location = source.relative_path or source.row_key or "无路径"
    return f"{source.table_name} · {location}"


class CharacterDetailPanel(QWidget):
    """Render one character while a controller owns all loading decisions."""

    growth_page_requested = Signal(int, int, int)
    combat_page_requested = Signal(int, int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._detail: CharacterDetail | None = None
        self._growth_offset = 0
        self._growth_limit = 40
        self._growth_total = 0
        self._combat_offset = 0
        self._combat_limit = 100
        self._combat_total = 0
        self._growth_loaded_character_id: int | None = None
        self._combat_loaded_character_id: int | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        heading = QHBoxLayout()
        self.title_label = QLabel("选择角色查看资料")
        self.title_label.setStyleSheet(themed_style(
            "font-size:20px;font-weight:800;color:#f0f6fc"
        ))
        heading.addWidget(self.title_label)
        heading.addStretch()
        self.dataset_label = QLabel("只读发行静态库")
        self.dataset_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        heading.addWidget(self.dataset_label)
        root.addLayout(heading)

        self.tabs = QTabWidget()
        self.overview_scroll = QScrollArea()
        self.overview_scroll.setWidgetResizable(True)
        self.overview_scroll.setFrameShape(QFrame.NoFrame)
        self.overview_content = QWidget()
        self.overview_layout = QVBoxLayout(self.overview_content)
        self.overview_layout.setContentsMargins(4, 4, 4, 4)
        self.overview_layout.setSpacing(10)
        self.overview_layout.addStretch()
        self.overview_scroll.setWidget(self.overview_content)
        self.tabs.addTab(self.overview_scroll, "概览与养成")

        self.growth_tab = self._build_growth_tab()
        self.tabs.addTab(self.growth_tab, "1–80 级面板")
        self.combat_tab = self._build_combat_tab()
        self.tabs.addTab(self.combat_tab, "GA / GE / Buff")
        self.tabs.currentChanged.connect(self._tab_changed)
        root.addWidget(self.tabs, 1)

    def _build_growth_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        self.growth_hint = QLabel("选择此页后按需加载等级曲线")
        self.growth_hint.setStyleSheet(themed_style("color:#8b949e"))
        layout.addWidget(self.growth_hint)
        self.growth_table = self._table((
            "等级", "突破阶段", "状态", "生命", "攻击", "防御", "字段来源",
        ))
        layout.addWidget(self.growth_table, 1)
        controls = QHBoxLayout()
        self.growth_previous = QPushButton("上一页")
        self.growth_previous.clicked.connect(lambda: self._request_growth(-1))
        self.growth_next = QPushButton("下一页")
        self.growth_next.clicked.connect(lambda: self._request_growth(1))
        self.growth_page_label = QLabel("未加载")
        controls.addWidget(self.growth_previous)
        controls.addWidget(self.growth_next)
        controls.addWidget(self.growth_page_label)
        controls.addStretch()
        copy_button = QPushButton("复制选中单元格")
        copy_button.clicked.connect(lambda: self._copy_selected(self.growth_table))
        controls.addWidget(copy_button)
        layout.addLayout(controls)
        self._update_growth_controls()
        return tab

    def _build_combat_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        self.combat_hint = QLabel("关系较多；选择此页后分页加载正式资源关联")
        self.combat_hint.setStyleSheet(themed_style("color:#8b949e"))
        layout.addWidget(self.combat_hint)
        self.combat_table = self._table((
            "关系", "绑定", "GA", "GA 路径", "事件", "GE / Buff", "索引", "资源路径", "字段来源",
        ))
        layout.addWidget(self.combat_table, 1)
        controls = QHBoxLayout()
        self.combat_previous = QPushButton("上一页")
        self.combat_previous.clicked.connect(lambda: self._request_combat(-1))
        self.combat_next = QPushButton("下一页")
        self.combat_next.clicked.connect(lambda: self._request_combat(1))
        self.combat_page_label = QLabel("未加载")
        controls.addWidget(self.combat_previous)
        controls.addWidget(self.combat_next)
        controls.addWidget(self.combat_page_label)
        controls.addStretch()
        copy_button = QPushButton("复制选中单元格")
        copy_button.clicked.connect(lambda: self._copy_selected(self.combat_table))
        controls.addWidget(copy_button)
        layout.addLayout(controls)
        self._update_combat_controls()
        return tab

    @staticmethod
    def _table(headers: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectItems)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def set_detail(self, detail: CharacterDetail | None) -> None:
        self._detail = detail
        self._growth_loaded_character_id = None
        self._combat_loaded_character_id = None
        self._growth_offset = 0
        self._combat_offset = 0
        self._growth_total = detail.growth_count if detail else 0
        self._combat_total = detail.combat_link_count if detail else 0
        self.growth_table.setRowCount(0)
        self.combat_table.setRowCount(0)
        self._clear_layout(self.overview_layout)
        if detail is None:
            self.title_label.setText("选择角色查看资料")
            self.dataset_label.setText("只读发行静态库")
            self.overview_layout.addWidget(self._empty_label("暂无角色详情"))
            self.overview_layout.addStretch()
        else:
            self.title_label.setText(
                f"{detail.character.name_zh} · {detail.character.character_id}"
            )
            dataset = detail.dataset
            self.dataset_label.setText(
                f"{dataset.dataset_id} · schema {dataset.schema_version} · importer {dataset.importer_version} · 只读"
            )
            self._render_overview(detail)
        self._update_growth_controls()
        self._update_combat_controls()
        self._tab_changed(self.tabs.currentIndex())

    def _render_overview(self, detail: CharacterDetail) -> None:
        character = detail.character
        identity = self._section("角色身份", "character")
        identity.layout().addLayout(self._copy_row("正式 character_id", str(character.character_id)))
        identity.layout().addLayout(self._copy_row("中文名", character.name_zh))
        identity.layout().addLayout(self._copy_row("属性类型", f"{character.element_label} · {_text(character.element_type)}"))
        identity.layout().addLayout(self._copy_row("阵营/组别", _text(character.group_type)))
        identity.layout().addLayout(self._copy_row("角色资源路径", _text(character.actor_path)))
        identity.layout().addLayout(self._copy_row("逻辑角色键", _text(character.logical_character_key)))
        identity.layout().addLayout(self._copy_row("字段来源", _source_label(character.source)))
        self.overview_layout.addWidget(identity)

        breakthrough = self._section("等级曲线与突破阶段", "character_panel_growth")
        if detail.breakthroughs:
            for stage in detail.breakthroughs:
                label = QLabel(
                    f"{stage.level} 级 · 阶段 {stage.stage}　"
                    f"突破前 HP/ATK/DEF {_number(stage.before.hp_base)} / {_number(stage.before.atk_base)} / {_number(stage.before.def_base)}　→　"
                    f"突破后 {_number(stage.after.hp_base)} / {_number(stage.after.atk_base)} / {_number(stage.after.def_base)}"
                )
                label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                label.setWordWrap(True)
                breakthrough.layout().addWidget(label)
        else:
            breakthrough.layout().addWidget(self._empty_label("该角色没有正式等级/突破面板"))
        self.overview_layout.addWidget(breakthrough)

        self.overview_layout.addWidget(self._likeability_section(detail))
        self.overview_layout.addWidget(self._awakening_section(detail))
        self.overview_layout.addWidget(self._skill_section(detail))
        self.overview_layout.addWidget(self._graduation_section(detail))
        self.overview_layout.addWidget(self._gap_section(detail))
        self.overview_layout.addStretch()

    def _likeability_section(self, detail: CharacterDetail) -> QFrame:
        section = self._section("好感度", "character_likeability_bonus")
        bonus = detail.likeability
        if bonus is None:
            section.layout().addWidget(self._empty_label("静态库没有该角色的好感度正式属性修改"))
            return section
        section.layout().addLayout(self._copy_row("生效等级", str(bonus.required_level)))
        section.layout().addLayout(self._copy_row("修改 ID", bonus.modify_data_id))
        for item in bonus.properties:
            value = item.value * 100 if item.show_percent else item.value
            suffix = "%" if item.show_percent else ""
            section.layout().addLayout(self._copy_row(
                f"{item.display_name} ({item.property_id})",
                f"{_number(value)}{suffix} · {item.modifier_operation}",
            ))
        return section

    def _awakening_section(self, detail: CharacterDetail) -> QFrame:
        section = self._section("觉醒与结构化效果", "character_awaken_effect")
        if not detail.awakenings:
            section.layout().addWidget(self._empty_label("静态库没有该角色的觉醒行"))
            return section
        for effect in detail.awakenings:
            title = effect.title_zh or effect.effect_id
            summary = QLabel(
                f"{effect.ordinal + 1}. {title}　[{effect.awaken_type}]　"
                f"结构字段 {len(effect.structured_effects)} · GE {len(effect.gameplay_effect_ids)}"
            )
            summary.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            summary.setToolTip(effect.description_zh or "")
            section.layout().addWidget(summary)
            section.layout().addLayout(self._copy_row("正式 effect_id", effect.effect_id))
            for field in effect.structured_effects:
                section.layout().addLayout(self._copy_row(field.path, field.value_json))
            if effect.gameplay_effect_ids:
                section.layout().addLayout(self._copy_row("正式 GE", "\n".join(effect.gameplay_effect_ids)))
        return section

    def _skill_section(self, detail: CharacterDetail) -> QFrame:
        section = self._section("技能、等级与升级消耗", "character_skill / gameplay_ability_catalog")
        if not detail.skills:
            section.layout().addWidget(self._empty_label("静态库没有该角色的技能目录"))
            return section
        for skill in detail.skills:
            section.layout().addLayout(self._copy_row(
                skill.name_zh or skill.skill_id,
                f"{skill.skill_id} · {skill.ability_type} · {len(skill.levels)} 级消耗记录",
            ))
            for path in (skill.gameplay_tag, skill.gameplay_ability_path, skill.gameplay_effect_path):
                if path:
                    section.layout().addLayout(self._copy_row("正式标识/路径", path))
            for level in skill.levels:
                costs = "、".join(
                    f"{item.item_id} × {_number(item.quantity)}" for item in level.costs
                ) or "无消耗项"
                section.layout().addLayout(self._copy_row(
                    f"等级 {level.level}（突破 {level.required_breakthrough_stage} / 觉醒 {level.required_awaken_level}）",
                    costs,
                ))
        return section

    def _graduation_section(self, detail: CharacterDetail) -> QFrame:
        section = self._section("培养指南、毕业模板与专武", "character_cultivation_* / character_graduation_template")
        guide = detail.cultivation
        if guide is not None:
            section.layout().addLayout(self._copy_row("培养评分", f"S {guide.s_score} · A {guide.a_score}"))
            for fork_id, name, description in guide.fork_recommendations:
                section.layout().addLayout(self._copy_row(f"推荐弧盘 · {name}", fork_id))
                if description:
                    note = QLabel(description)
                    note.setWordWrap(True)
                    section.layout().addWidget(note)
        graduation = detail.graduation
        if graduation is None:
            section.layout().addWidget(self._empty_label("静态库没有该角色的毕业模板"))
            return section
        section.layout().addLayout(self._copy_row(
            "毕业弧盘/专武",
            f"{_text(graduation.fork_name_zh)} · {_text(graduation.fork_id)} · Lv.{_text(graduation.fork_level)}",
        ))
        section.layout().addLayout(self._copy_row(
            "空幕",
            f"{_text(graduation.core_suit_name_zh)} ({_text(graduation.core_suit_id)}) · "
            f"{_text(graduation.core_main_property_name_zh)} ({_text(graduation.core_main_property_id)})",
        ))
        for path in graduation.fork_paths:
            section.layout().addLayout(self._copy_row("专武资源路径", path))
        return section

    def _gap_section(self, detail: CharacterDetail) -> QFrame:
        section = self._section("数据可用性", "schema / manifest")
        for gap in detail.gaps:
            label = QLabel(f"[{gap.status}] {gap.label}：{gap.reason}")
            label.setWordWrap(True)
            label.setStyleSheet(themed_style(
                "color:#d29922" if gap.status == "partial" else "color:#f85149"
            ))
            section.layout().addWidget(label)
        return section

    def set_growth_page(self, page: GrowthPage) -> None:
        if self._detail is None or page.character_id != self._detail.character.character_id:
            return
        self._growth_loaded_character_id = page.character_id
        self._growth_offset = page.offset
        self._growth_limit = page.limit
        self._growth_total = page.total
        self.growth_table.setRowCount(len(page.items))
        for row_index, point in enumerate(page.items):
            values = (
                point.level,
                point.breakthrough_stage,
                _STATE_LABELS.get(point.state, point.state),
                _number(point.hp_base),
                _number(point.atk_base),
                _number(point.def_base),
                _source_label(point.breakthrough_source or point.level_curve_source),
            )
            self._set_row(self.growth_table, row_index, values)
        self.growth_hint.setText("官方基础面板；临界等级的突破前后为独立行")
        self._update_growth_controls()

    def set_combat_page(self, page: CombatLinkPage) -> None:
        if self._detail is None or page.character_id != self._detail.character.character_id:
            return
        self._combat_loaded_character_id = page.character_id
        self._combat_offset = page.offset
        self._combat_limit = page.limit
        self._combat_total = page.total
        self.combat_table.setRowCount(len(page.items))
        for row_index, link in enumerate(page.items):
            values = (
                link.relationship_kind,
                link.binding_kind,
                link.ability_id,
                link.ability_asset_path,
                link.event_tag,
                link.gameplay_effect_id or link.buff_definition_id,
                link.gameplay_effect_index,
                link.effect_asset_path or link.gameplay_effect_class_path,
                _source_label(link.source),
            )
            self._set_row(self.combat_table, row_index, values)
        self.combat_hint.setText("正式角色绑定、GA 资源、事件到 GE/Buff，以及角色所属 Buff")
        self._update_combat_controls()

    def _tab_changed(self, index: int) -> None:
        if self._detail is None:
            return
        character_id = self._detail.character.character_id
        if self.tabs.widget(index) is self.growth_tab and self._growth_loaded_character_id != character_id:
            self.growth_page_requested.emit(character_id, 0, self._growth_limit)
        if self.tabs.widget(index) is self.combat_tab and self._combat_loaded_character_id != character_id:
            self.combat_page_requested.emit(character_id, 0, self._combat_limit)

    def _request_growth(self, direction: int) -> None:
        if self._detail is None:
            return
        offset = max(0, self._growth_offset + direction * self._growth_limit)
        if offset < self._growth_total:
            self.growth_page_requested.emit(
                self._detail.character.character_id, offset, self._growth_limit,
            )

    def _request_combat(self, direction: int) -> None:
        if self._detail is None:
            return
        offset = max(0, self._combat_offset + direction * self._combat_limit)
        if offset < self._combat_total:
            self.combat_page_requested.emit(
                self._detail.character.character_id, offset, self._combat_limit,
            )

    def _update_growth_controls(self) -> None:
        loaded = self._growth_loaded_character_id is not None
        self.growth_previous.setEnabled(loaded and self._growth_offset > 0)
        self.growth_next.setEnabled(
            loaded and self._growth_offset + self._growth_limit < self._growth_total
        )
        self.growth_page_label.setText(self._page_text(
            self._growth_offset, self._growth_limit, self._growth_total, loaded,
        ))

    def _update_combat_controls(self) -> None:
        loaded = self._combat_loaded_character_id is not None
        self.combat_previous.setEnabled(loaded and self._combat_offset > 0)
        self.combat_next.setEnabled(
            loaded and self._combat_offset + self._combat_limit < self._combat_total
        )
        self.combat_page_label.setText(self._page_text(
            self._combat_offset, self._combat_limit, self._combat_total, loaded,
        ))

    @staticmethod
    def _page_text(offset: int, limit: int, total: int, loaded: bool) -> str:
        if not loaded:
            return f"未加载 · 共 {total} 条"
        if total == 0:
            return "0 条"
        return f"{offset + 1}–{min(offset + limit, total)} / {total}"

    @staticmethod
    def _section(title: str, source: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("staticCatalogCharacterSection")
        frame.setStyleSheet(themed_style(
            "QFrame#staticCatalogCharacterSection{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px}"
        ))
        layout = QVBoxLayout(frame)
        heading = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setStyleSheet(themed_style("font-size:15px;font-weight:800;color:#f0f6fc"))
        source_label = QLabel(source)
        source_label.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        heading.addWidget(title_label)
        heading.addStretch()
        heading.addWidget(source_label)
        layout.addLayout(heading)
        return frame

    @staticmethod
    def _copy_row(label: str, value: str) -> QHBoxLayout:
        row = QHBoxLayout()
        label_widget = QLabel(label)
        label_widget.setMinimumWidth(145)
        label_widget.setStyleSheet(themed_style("color:#8b949e"))
        value_widget = QLabel(value)
        value_widget.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        value_widget.setWordWrap(True)
        button = QPushButton("复制")
        button.setMaximumWidth(58)
        button.clicked.connect(
            lambda _checked=False, text=value: QApplication.clipboard().setText(text)
        )
        row.addWidget(label_widget)
        row.addWidget(value_widget, 1)
        row.addWidget(button)
        return row

    @staticmethod
    def _copy_selected(table: QTableWidget) -> None:
        item = table.currentItem()
        if item is not None:
            QApplication.clipboard().setText(item.text())

    @staticmethod
    def _set_row(table: QTableWidget, row: int, values: Iterable[object]) -> None:
        for column, value in enumerate(values):
            item = QTableWidgetItem(_text(value))
            item.setToolTip(item.text())
            table.setItem(row, column, item)

    @staticmethod
    def _empty_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(themed_style("color:#8b949e"))
        return label

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                while child.count():
                    nested = child.takeAt(0)
                    nested_widget = nested.widget()
                    if nested_widget is not None:
                        nested_widget.deleteLater()
