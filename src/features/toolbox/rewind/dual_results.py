# 展示共同形状的双组缺口、份数与费用；技术来源默认折叠，八槽编辑仍复用原控件。
from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget

from src.domain.equipment.geometry.shapes import OFFICIAL_SHAPE_LABELS
from src.ui.common.text_catalog import plain_label, plain_tooltip


class RewindDualResultsMixin:
    def _render_dual_results(self, analysis, *, preserve_edits=False):
        result, text = analysis.dual_target, self.text_catalog.render
        self._dual_manual_draft = False
        if not preserve_edits:
            self._set_replacement_inventory_counts(dict(analysis.owned_shape_counts))
            self._apply_recommendations(analysis.recommendations)
        self._render_rewind_slots(text('rewind.dual.result_title'), text('rewind.dual.heuristic'))
        slot_page = self._result_tabs.widget(0)
        self._result_tabs.removeTab(0)
        page = QWidget()
        layout = QVBoxLayout(page)
        from collections import Counter
        quantities = Counter(row.shape.shape_id for row in self._editable_slots if row is not None)
        total_cost = sum(analysis.pricing_rule.cost_for_quantity(count) for count in quantities.values())
        status = (text('rewind.dual.no_shared') if not result.shared and not preserve_edits else
                  text('rewind.dual.pool_status', count=len(result.shared),
                       empty=8 - sum(quantities.values()), cost=f'{total_cost:g}'))
        if preserve_edits:
            status = text('rewind.dual.manually_edited', status=status)
        heading = plain_label(status)
        heading.setWordWrap(True)
        heading.setObjectName('rewindDualStatus')
        layout.addWidget(heading)
        for key, group in zip(('improve', 'push'), result.groups):
            summary = plain_label(text('rewind.dual.group_status', group=text('rewind.dual.' + key),
                examined=group.examined_count, missing=len(group.items),
                state=text('rewind.dual.satisfied' if group.satisfied else 'rewind.dual.has_shortfall')))
            summary.setWordWrap(True)
            layout.addWidget(summary)
        if result.shared:
            table = QTableWidget(len(result.shared), 5)
            table.setObjectName('rewindDualDemands')
            table.setHorizontalHeaderLabels([text('rewind.dual.shape'), text('rewind.dual.improve_demand'),
                text('rewind.dual.push_demand'), text('rewind.dual.quantity'), text('rewind.dual.cost')])
            table.setEditTriggers(QTableWidget.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.verticalHeader().hide()
            for index, demand in enumerate(result.shared):
                count = quantities.get(demand.shape_id, 0)
                values = [OFFICIAL_SHAPE_LABELS.get(demand.shape_id, demand.shape_id),
                    text('rewind.dual.demand', count=demand.improve.count, gap=f'{demand.improve.normalized_gap:.1%}'),
                    text('rewind.dual.demand', count=demand.push.count, gap=f'{demand.push.normalized_gap:.1%}'),
                    str(count), f'{analysis.pricing_rule.cost_for_quantity(count):g}']
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    ids = demand.improve.character_ids if column == 1 else demand.push.character_ids if column == 2 else ()
                    item.setToolTip(plain_tooltip('、'.join(self._role_names.get(key, str(key)) for key in ids) or value))
                    table.setItem(index, column, item)
            header = table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            header.setSectionResizeMode(2, QHeaderView.Stretch)
            table.resizeRowsToContents()
            # 少量共同形状按实际行高收紧，避免单行表格占据整片空白；较多行留在表内滚动。
            table.setFixedHeight(min(190, header.sizeHint().height() + table.verticalHeader().length()
                + table.frameWidth() * 2 + table.horizontalScrollBar().sizeHint().height()))
            layout.addWidget(table)
        if result.uncovered_shapes:
            uncovered = plain_label(text('rewind.dual.uncovered', shapes='、'.join(
                OFFICIAL_SHAPE_LABELS.get(value, value) for value in result.uncovered_shapes)))
            uncovered.setWordWrap(True)
            layout.addWidget(uncovered)
        layout.addWidget(slot_page)
        # removeTab 会保留旧页的显式隐藏状态；重挂到滚动区后必须恢复，否则有八槽数据却看不到卡片。
        slot_page.show()
        details = plain_label(self._dual_diagnostic_text(analysis))
        details.setWordWrap(True)
        details.hide()
        toggle = QToolButton()
        toggle.setObjectName('rewindDualDetails')
        toggle.setText(text('rewind.dual.details'))
        toggle.setCheckable(True)
        toggle.toggled.connect(details.setVisible)
        layout.addWidget(toggle)
        layout.addWidget(details)
        # 外层结果视口是唯一滚动 owner，展开来源和八槽编辑共用它，避免内外两根滑块。
        self._result_tabs.addTab(page, text('rewind.results.tab'))

    def _dual_diagnostic_text(self, analysis):
        result, text = analysis.dual_target, self.text_catalog.render
        lines = [text('rewind.dual.source', snapshot=analysis.snapshot_id), text('rewind.dual.limitations')]
        for key, group in zip(('improve', 'push'), result.groups):
            lines.append(text('rewind.dual.' + key))
            for item in group.items:
                lines.append(text('rewind.dual.item', name=self._role_names.get(item.reference.character_id, str(item.reference.character_id)),
                    slot=item.reference.slot_id, plan=item.reference.plan_id, source=item.source_snapshot_id,
                    shape=OFFICIAL_SHAPE_LABELS.get(item.shape_id, item.shape_id), saved=f'{item.saved_score:g}',
                    target=f'{item.target_score:g}', gap=f'{item.normalized_gap:.1%}'))
        return '\n'.join(lines)
