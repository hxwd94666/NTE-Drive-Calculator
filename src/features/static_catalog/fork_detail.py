# 游戏资料库弧盘目录、筛选和详情视图。
"""UI-only fork catalog panel; public navigation is wired by integration."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.services.static_catalog_fork_service import (
    CatalogOrigin,
    CatalogRelation,
    CatalogSourceTrace,
    ForkBreakthrough,
    ForkBuffDefinition,
    ForkCatalogDetail,
    ForkCatalogSummary,
    ForkModifier,
    ForkRefinementLevel,
    StaticCatalogForkService,
)


_ORIGIN_LABELS = {
    CatalogOrigin.OFFICIAL_STATIC: "正式静态字段",
    CatalogOrigin.PROJECT_PROJECTION: "项目投影",
    CatalogOrigin.DERIVED_DISPLAY: "派生展示值",
}


class ForkCatalogWidget(QWidget):
    """Browse 49+ forks without eagerly creating one widget per detail row."""

    relation_jump_requested = Signal(str, str)

    def __init__(
        self,
        service: StaticCatalogForkService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._page = 1
        self._page_size = 50
        self._pending_fork_id: str | None = None
        self._build_ui()
        self._load_filters()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self.refresh)
        self._wire_events()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.metadata_label = QLabel()
        self.metadata_label.setObjectName("forkCatalogMetadata")
        self.metadata_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.metadata_label)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "名称 / 弧盘 ID / 角色 / 参数 / Buff / 资源路径"
        )
        self.search_edit.setClearButtonEnabled(True)
        self.type_combo = QComboBox()
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("全部品质", None)
        for value, label in (("ORANGE", "橙色"), ("PURPLE", "紫色"), ("BLUE", "蓝色")):
            self.quality_combo.addItem(label, value)
        self.character_edit = QLineEdit()
        self.character_edit.setPlaceholderText("角色 ID")
        self.character_edit.setValidator(QIntValidator(1, 999999, self))
        self.character_edit.setMaximumWidth(110)
        self.reset_button = QPushButton("重置")
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.type_combo)
        filters.addWidget(self.quality_combo)
        filters.addWidget(self.character_edit)
        filters.addWidget(self.reset_button)
        root.addLayout(filters)

        splitter = QSplitter(Qt.Horizontal)
        catalog = QWidget()
        catalog_layout = QVBoxLayout(catalog)
        catalog_layout.setContentsMargins(0, 0, 0, 0)
        self.catalog_list = QListWidget()
        self.catalog_list.setUniformItemSizes(True)
        catalog_layout.addWidget(self.catalog_list, 1)
        pager = QHBoxLayout()
        self.previous_button = QPushButton("上一页")
        self.page_label = QLabel()
        self.next_button = QPushButton("下一页")
        pager.addWidget(self.previous_button)
        pager.addWidget(self.page_label, 1, Qt.AlignCenter)
        pager.addWidget(self.next_button)
        catalog_layout.addLayout(pager)
        splitter.addWidget(catalog)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_actions = QHBoxLayout()
        self.detail_title = QLabel("选择弧盘查看详情")
        self.detail_title.setObjectName("forkCatalogDetailTitle")
        self.copy_button = QPushButton("复制当前字段")
        self.copy_button.setEnabled(False)
        detail_actions.addWidget(self.detail_title, 1)
        detail_actions.addWidget(self.copy_button)
        detail_layout.addLayout(detail_actions)
        self.detail_tree = QTreeWidget()
        self.detail_tree.setColumnCount(3)
        self.detail_tree.setHeaderLabels(("字段 / 关系", "值", "来源"))
        self.detail_tree.setAlternatingRowColors(True)
        self.detail_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.detail_tree.setUniformRowHeights(True)
        detail_layout.addWidget(self.detail_tree, 1)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        root.addWidget(splitter, 1)

    def _load_filters(self) -> None:
        metadata = self._service.metadata()
        self.metadata_label.setText(
            f"dataset {metadata.dataset_id} · schema {metadata.schema_version} · "
            f"importer {metadata.importer_version} · 只读 · "
            f"source payload {'已保留' if metadata.source_payloads_preserved else '已省略'}"
        )
        self.type_combo.clear()
        self.type_combo.addItem("全部类型", None)
        for fork_type in self._service.list_types():
            self.type_combo.addItem(
                f"{fork_type.name_zh} ({fork_type.fork_count})",
                fork_type.fork_type_id,
            )

    def _wire_events(self) -> None:
        self.search_edit.textChanged.connect(self._schedule_refresh)
        self.character_edit.textChanged.connect(self._schedule_refresh)
        self.type_combo.currentIndexChanged.connect(self._reset_page_and_refresh)
        self.quality_combo.currentIndexChanged.connect(self._reset_page_and_refresh)
        self.reset_button.clicked.connect(self.reset_filters)
        self.previous_button.clicked.connect(self._previous_page)
        self.next_button.clicked.connect(self._next_page)
        self.catalog_list.currentItemChanged.connect(self._selection_changed)
        self.copy_button.clicked.connect(self.copy_current_field)
        self.detail_tree.currentItemChanged.connect(self._detail_selection_changed)
        self.detail_tree.itemDoubleClicked.connect(self._detail_activated)
        self.detail_tree.customContextMenuRequested.connect(self._show_detail_menu)

    def _schedule_refresh(self, _text: str = "") -> None:
        self._page = 1
        self._search_timer.start()

    def _reset_page_and_refresh(self, _index: int = 0) -> None:
        self._page = 1
        self.refresh()

    def reset_filters(self) -> None:
        self.search_edit.clear()
        self.character_edit.clear()
        self.type_combo.setCurrentIndex(0)
        self.quality_combo.setCurrentIndex(0)
        self._page = 1
        self.refresh()

    def _character_id(self) -> int | None:
        text = self.character_edit.text().strip()
        return int(text) if text else None

    def refresh(self) -> None:
        current_id = self._pending_fork_id
        current_item = self.catalog_list.currentItem()
        if current_id is None and current_item is not None:
            current_id = str(current_item.data(Qt.UserRole) or "")
        page = self._service.list_forks(
            query=self.search_edit.text(),
            quality=self.quality_combo.currentData(),
            fork_type_id=self.type_combo.currentData(),
            character_id=self._character_id(),
            page=self._page,
            page_size=self._page_size,
        )
        self._page = page.page
        self.catalog_list.blockSignals(True)
        self.catalog_list.clear()
        selected_row = -1
        for index, summary in enumerate(page.items):
            item = QListWidgetItem(self._summary_label(summary))
            item.setData(Qt.UserRole, summary.fork_id)
            item.setToolTip(summary.description_zh or summary.fork_id)
            self.catalog_list.addItem(item)
            if summary.fork_id == current_id:
                selected_row = index
        self.catalog_list.blockSignals(False)
        self.page_label.setText(
            f"第 {page.page} / {page.total_pages} 页 · {page.total_items} 条"
        )
        self.previous_button.setEnabled(page.page > 1)
        self.next_button.setEnabled(page.page < page.total_pages)
        if selected_row >= 0:
            self.catalog_list.setCurrentRow(selected_row)
            self._pending_fork_id = None
        elif page.items:
            self.catalog_list.setCurrentRow(0)
        else:
            self.detail_title.setText("没有匹配的弧盘")
            self.detail_tree.clear()

    @staticmethod
    def _summary_label(summary: ForkCatalogSummary) -> str:
        fork_type = summary.fork_type_name_zh or "未分类"
        return f"{summary.name_zh}  ·  {fork_type}\n{summary.fork_id}"

    def select_fork(self, fork_id: str) -> None:
        """Public integration hook for a relationship jump into this domain."""

        wanted = str(fork_id)
        for row in range(self.catalog_list.count()):
            item = self.catalog_list.item(row)
            if str(item.data(Qt.UserRole)) == wanted:
                self.catalog_list.setCurrentRow(row)
                return
        self.search_edit.setText(wanted)
        self._pending_fork_id = wanted

    def _previous_page(self) -> None:
        self._page = max(1, self._page - 1)
        self.refresh()

    def _next_page(self) -> None:
        self._page += 1
        self.refresh()

    def _selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        detail = self._service.get_fork(str(current.data(Qt.UserRole)))
        if detail is not None:
            self._render_detail(detail)

    def _root(self, label: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem((label, "", ""))
        item.setFirstColumnSpanned(True)
        self.detail_tree.addTopLevelItem(item)
        return item

    @staticmethod
    def _add(
        parent: QTreeWidgetItem,
        label: str,
        value: object,
        origin: CatalogOrigin,
        *,
        relation: CatalogRelation | None = None,
    ) -> QTreeWidgetItem:
        text = "" if value is None else str(value)
        item = QTreeWidgetItem((label, text, _ORIGIN_LABELS[origin]))
        item.setData(1, Qt.UserRole, relation.copy_value if relation else text)
        if relation is not None and relation.available:
            item.setData(1, Qt.UserRole + 1, (relation.kind, relation.target_id))
            item.setToolTip(0, "双击请求跳转；右键可复制正式标识")
        elif relation is not None:
            item.setToolTip(0, "关系目标未解析，仅可复制正式标识")
        parent.addChild(item)
        return item

    def _render_detail(self, detail: ForkCatalogDetail) -> None:
        self.detail_tree.setUpdatesEnabled(False)
        self.detail_tree.clear()
        self.detail_title.setText(f"{detail.summary.name_zh} · {detail.summary.fork_id}")
        overview = self._root("概览")
        for label, value in (
            ("弧盘 ID", detail.summary.fork_id),
            ("名称", detail.summary.name_zh),
            ("品质", detail.summary.quality),
            ("类型", detail.summary.fork_type_name_zh),
            ("原始组类型", detail.summary.raw_group_type),
            ("文本表", detail.name_text_table),
            ("文本键", detail.name_text_key),
            ("升级包", detail.upgrade_pack_id),
            ("突破包", detail.breakthrough_pack_id),
            ("混频数据包", detail.star_pack_id),
            ("描述", detail.summary.description_zh),
        ):
            self._add(overview, label, value, CatalogOrigin.OFFICIAL_STATIC)

        relations = self._root(f"关系 ({len(detail.relations)})")
        for relation in detail.relations:
            self._add(
                relations,
                relation.kind,
                relation.label,
                relation.origin,
                relation=relation,
            )

        critical = self._root("临界等级突破前/后")
        for state in detail.critical_level_states:
            node = self._add(
                critical,
                f"{state.level} 级 {state.state}",
                f"stage={state.stage}",
                CatalogOrigin.DERIVED_DISPLAY,
            )
            self._add_modifiers(node, state.growth.modifiers, "等级面板")
            self._add_modifiers(node, state.breakthrough.modifiers, "突破阶段")

        growth = self._root(f"等级面板与成长 ({len(detail.growth_levels)})")
        for level in detail.growth_levels:
            node = self._add(
                growth,
                f"{level.level} 级",
                f"NeedExp={level.need_exp} · {level.modify_pack_id}",
                CatalogOrigin.OFFICIAL_STATIC,
            )
            self._add_modifiers(node, level.modifiers, "属性修改")
            self._add_source(node, level.source)

        breakthroughs = self._root(f"突破与消耗 ({len(detail.breakthroughs)})")
        for stage in detail.breakthroughs:
            self._add_breakthrough(breakthroughs, stage)

        refinements = self._root(f"弧盘技能 / 混频 ({len(detail.refinement_levels)})")
        for refinement in detail.refinement_levels:
            self._add_refinement(refinements, refinement)

        effects = self._root(f"Buff / GE / 被动效果 ({len(detail.buff_definitions)})")
        for buff in detail.buff_definitions:
            self._add_buff(effects, buff)

        resources = self._root(f"资源路径 ({len(detail.resources)})")
        for resource in detail.resources:
            self._add(resources, resource.kind, resource.path, resource.origin)

        source = self._root("来源追溯")
        self._add_source(source, detail.source)
        notes = self._root("边界与缺失项")
        for note in detail.audit_notes:
            self._add(notes, "审计", note, CatalogOrigin.PROJECT_PROJECTION)
        self.detail_tree.expandItem(overview)
        self.detail_tree.expandItem(critical)
        self.detail_tree.resizeColumnToContents(0)
        self.detail_tree.setUpdatesEnabled(True)

    def _add_modifiers(
        self,
        parent: QTreeWidgetItem,
        modifiers: tuple[ForkModifier, ...],
        label: str,
    ) -> None:
        group = self._add(parent, label, len(modifiers), CatalogOrigin.OFFICIAL_STATIC)
        for modifier in modifiers:
            name = modifier.property_name_zh or modifier.property_id
            self._add(
                group,
                name,
                f"{modifier.display_value} · {modifier.operation} · raw={modifier.raw_value:g}",
                CatalogOrigin.OFFICIAL_STATIC,
            )

    def _add_breakthrough(
        self, parent: QTreeWidgetItem, stage: ForkBreakthrough,
    ) -> None:
        node = self._add(
            parent,
            f"stage {stage.stage}",
            f"上限 {stage.max_fork_level} · {stage.modify_pack_id or '-'}",
            CatalogOrigin.OFFICIAL_STATIC,
        )
        self._add(node, "材料消耗", stage.need_items_raw, CatalogOrigin.OFFICIAL_STATIC)
        self._add(node, "货币消耗", stage.need_gold_raw, CatalogOrigin.OFFICIAL_STATIC)
        self._add_modifiers(node, stage.modifiers, "突破属性")
        self._add_source(node, stage.source)

    def _add_refinement(
        self, parent: QTreeWidgetItem, refinement: ForkRefinementLevel,
    ) -> None:
        node = self._add(
            parent,
            f"混频 {refinement.level}",
            refinement.title_zh,
            CatalogOrigin.OFFICIAL_STATIC,
        )
        self._add(node, "描述", refinement.description_zh, CatalogOrigin.OFFICIAL_STATIC)
        self._add(node, "消耗", refinement.need_gold_raw, CatalogOrigin.OFFICIAL_STATIC)
        for parameter in refinement.parameters:
            self._add(
                node,
                parameter.name_id,
                f"{parameter.display_value} · raw={parameter.raw_value}",
                CatalogOrigin.OFFICIAL_STATIC,
            )
        for path in refinement.buff_asset_paths:
            self._add(node, "Buff 资产", path, CatalogOrigin.OFFICIAL_STATIC)
        if refinement.projected_effect_definition_id:
            relation = CatalogRelation(
                kind="effect_definition",
                target_id=refinement.projected_effect_definition_id,
                label=refinement.projected_effect_definition_id,
                copy_value=refinement.projected_effect_definition_id,
                origin=CatalogOrigin.PROJECT_PROJECTION,
            )
            self._add(
                node,
                "项目效果投影",
                relation.label,
                relation.origin,
                relation=relation,
            )
        self._add_source(node, refinement.source)

    def _add_buff(self, parent: QTreeWidgetItem, buff: ForkBuffDefinition) -> None:
        node = self._add(
            parent,
            f"混频 {buff.refinement_level}",
            buff.definition_id or buff.asset_path,
            CatalogOrigin.OFFICIAL_STATIC,
        )
        self._add(node, "Buff 路径", buff.asset_path, CatalogOrigin.OFFICIAL_STATIC)
        self._add(node, "GE ID", buff.gameplay_effect_id, CatalogOrigin.OFFICIAL_STATIC)
        self._add(node, "GE 路径", buff.gameplay_effect_class_path, CatalogOrigin.OFFICIAL_STATIC)
        self._add(node, "持续策略", buff.duration_policy, CatalogOrigin.OFFICIAL_STATIC)
        self._add(node, "叠层", buff.stacking_type, CatalogOrigin.OFFICIAL_STATIC)
        for modifier in buff.modifiers:
            child = self._add(
                node,
                f"属性 {modifier.property_name_zh or modifier.property_id}",
                f"{modifier.modifier_operation} · {modifier.magnitude_kind} · {modifier.magnitude_value}",
                CatalogOrigin.OFFICIAL_STATIC,
            )
            self._add(child, "Calculation", modifier.calculation_asset_path, CatalogOrigin.OFFICIAL_STATIC)
            for tag in modifier.gameplay_tags:
                relation = CatalogRelation(
                    "gameplay_tag", tag, tag, tag, CatalogOrigin.OFFICIAL_STATIC
                )
                self._add(child, "Gameplay Tag", tag, relation.origin, relation=relation)
        for trigger in buff.triggers:
            child = self._add(
                node,
                f"触发 {trigger.ordinal}",
                f"{trigger.event_type} → {trigger.effect_type}",
                CatalogOrigin.OFFICIAL_STATIC,
            )
            self._add(
                child,
                "目标效果",
                trigger.target_effect_asset_path,
                CatalogOrigin.OFFICIAL_STATIC,
            )
            self._add(
                child,
                "目标 GE",
                trigger.target_gameplay_effect_id,
                CatalogOrigin.OFFICIAL_STATIC,
            )

    def _add_source(self, parent: QTreeWidgetItem, source: CatalogSourceTrace) -> None:
        node = self._add(
            parent,
            "来源行",
            source.row_key,
            CatalogOrigin.OFFICIAL_STATIC,
        )
        self._add(node, "source_row_id", source.source_row_id, CatalogOrigin.OFFICIAL_STATIC)
        self._add(node, "相对路径", source.relative_path, CatalogOrigin.OFFICIAL_STATIC)
        self._add(node, "行 SHA-256", source.content_sha256, CatalogOrigin.OFFICIAL_STATIC)
        self._add(node, "文件 SHA-256", source.source_file_sha256, CatalogOrigin.OFFICIAL_STATIC)
        self._add(
            node,
            "原始 payload",
            "已保留" if source.payload_preserved else "发行库未保留",
            CatalogOrigin.OFFICIAL_STATIC,
        )

    def _detail_selection_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        self.copy_button.setEnabled(
            current is not None and bool(current.data(1, Qt.UserRole))
        )

    def copy_current_field(self) -> None:
        item = self.detail_tree.currentItem()
        if item is None:
            return
        value = str(item.data(1, Qt.UserRole) or "")
        if value:
            QApplication.clipboard().setText(value)

    def _detail_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        relation = item.data(1, Qt.UserRole + 1)
        if relation:
            kind, target_id = relation
            self.relation_jump_requested.emit(str(kind), str(target_id))

    def _show_detail_menu(self, position: Any) -> None:
        item = self.detail_tree.itemAt(position)
        if item is None:
            return
        menu = QMenu(self)
        copy_action = menu.addAction("复制字段值")
        relation = item.data(1, Qt.UserRole + 1)
        jump_action = menu.addAction("跳转到关联资料") if relation else None
        selected = menu.exec(self.detail_tree.viewport().mapToGlobal(position))
        if selected is copy_action:
            self.detail_tree.setCurrentItem(item)
            self.copy_current_field()
        elif jump_action is not None and selected is jump_action:
            self._detail_activated(item, 1)
