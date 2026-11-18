# 兼顾模式“共同缺口”只读弹窗：以大图形卡片展示两组都缺的驱动形状；不计算、不改八槽。
from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QScrollArea,
                               QVBoxLayout, QWidget)

from src.app.theme import themed_style
from src.app import window_geometry
from src.domain.rewind.shape_overview import GROUP_KEYS, summarize_shape_margins
from src.features.inventory.warehouse.display.cards import warehouse_shape_pixmap
from src.features.toolbox.rewind.execution_dialog import score_amount
from src.ui.common.text_catalog import plain_label, resolve_text_catalog

_COLUMNS = 5
_CARD_SIZE = QSize(132, 170)
# 形状素材均为 128 像素方图且同一格子比例；统一放大到同一尺寸，不同形状之间仍可按格子大小对比。
_ICON_SIZE = 84
# 窗口最多占屏幕可用高度的六成，卡片多时只滚动卡片区。
_MAX_HEIGHT_RATIO = 0.6
_MIN_WIDTH = 760
_TILE = "QFrame#rewindDualMetric{background:#161b22;border:1px solid #30363d;border-radius:10px;}"
_CARD = "QFrame#rewindDualOverlapCard{background:#161b22;border:1px solid #30363d;border-radius:12px;}"
_CARD_SINGLE = "QFrame#rewindDualOverlapCard{background:transparent;border:1px dashed #30363d;border-radius:12px;}"


def overlap_rows(analysis):
    """共同缺口按推荐算法的共同权重顺序在前，仅一组缺的形状按归一缺口随后；两组都达标的形状不列出。

    返回 (形状余量行, 需求类别)；需求类别为 shared / uncovered / improve / push。
    """
    result = analysis.dual_target
    rows = {row.shape_id: row for row in summarize_shape_margins(analysis.drive_margins, normalized=True)}
    uncovered = set(result.uncovered_shapes)
    ordered = [(rows[demand.shape_id], 'uncovered' if demand.shape_id in uncovered else 'shared')
               for demand in result.shared if demand.shape_id in rows]
    shared = {demand.shape_id for demand in result.shared}
    ordered += [(row, row.demand) for row in rows.values()
                if row.shape_id not in shared and row.demand in ('improve', 'push')]
    return ordered


class RewindDualOverlapDialog(QDialog):
    """quantities 是打开时八槽编辑区的份数；edited 表示八槽已在推荐后手动调整，费用按当前编辑重算。"""

    def __init__(self, parent, *, analysis, quantities: Mapping[str, int], edited: bool, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        text = self.text_catalog.render
        self.setWindowTitle(text('rewind.dual.overlap.title'))
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)
        root.addLayout(self._metrics(analysis, quantities))
        notes = []
        if edited:
            notes.append(text('rewind.dual.overlap.edited'))
        empty = 8 - sum(quantities.values())
        if empty > 0:
            notes.append(text('rewind.dual.overlap.empty_slots', count=empty))
        if notes:
            note = plain_label(' '.join(notes))
            note.setObjectName('rewindDualEdited')
            note.setWordWrap(True)
            note.setStyleSheet(themed_style('color:#d29922'))
            root.addWidget(note)

        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        rows = overlap_rows(analysis)
        shared = [(row, demand) for row, demand in rows if demand in ('shared', 'uncovered')]
        single = [(row, demand) for row, demand in rows if demand in ('improve', 'push')]
        if not rows:
            body.addWidget(self._message(text('rewind.dual.overlap.satisfied')))
        else:
            if shared:
                self._section(body, text('rewind.dual.overlap.section.shared'), shared, quantities)
            else:
                body.addWidget(self._message(text('rewind.dual.no_shared')))
            if single:
                self._section(body, text('rewind.dual.overlap.section.single'), single, quantities)
        body.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName('rewindDualOverlapScroll')
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        # 内容区是唯一滚动 owner：先按全部卡片的自然高度请求，屏幕不足时由限位收缩后在此滚动。
        hint = content.sizeHint()
        scroll.setMinimumWidth(hint.width() + scroll.verticalScrollBar().sizeHint().width())
        scroll.setMinimumHeight(hint.height())
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        requested = self.sizeHint().expandedTo(QSize(_MIN_WIDTH, 0))
        scroll.setMinimumHeight(min(hint.height(), 120))
        available = window_geometry._available_geometry(self)
        if not available.isEmpty():
            requested.setHeight(min(requested.height(), int(available.height() * _MAX_HEIGHT_RATIO)))
        window_geometry.fit_dialog_to_available_screen(self, requested)

    def _metrics(self, analysis, quantities):
        result, text = analysis.dual_target, self.text_catalog.render
        cost = sum(analysis.pricing_rule.cost_for_quantity(count) for count in quantities.values())
        tiles = [(text('rewind.dual.overlap.metric.shared'), text('rewind.dual.overlap.metric.shared_value', count=len(result.shared)))]
        for key, group in zip(GROUP_KEYS, result.groups):
            tiles.append((text('rewind.dual.overlap.metric.group', group=text('rewind.dual.' + key)),
                          text('rewind.dual.overlap.metric.group_value', missing=len(group.items), examined=group.examined_count)))
        tiles.append((text('rewind.dual.overlap.metric.cost'), text('rewind.dual.overlap.metric.cost_value', cost=f'{cost:g}')))
        row = QHBoxLayout()
        row.setSpacing(10)
        for caption, value in tiles:
            tile = QFrame()
            tile.setObjectName('rewindDualMetric')
            tile.setStyleSheet(themed_style(_TILE))
            layout = QVBoxLayout(tile)
            layout.setContentsMargins(14, 10, 14, 10)
            layout.setSpacing(2)
            label = plain_label(caption)
            label.setStyleSheet(themed_style('color:#8b949e;font-size:12px'))
            number = plain_label(value)
            number.setObjectName('rewindDualMetricValue')
            number.setStyleSheet(themed_style('color:#f0f6fc;font-size:20px;font-weight:700'))
            layout.addWidget(label)
            layout.addWidget(number)
            row.addWidget(tile, 1)
        return row

    def _message(self, value):
        label = plain_label(value)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(themed_style('color:#8b949e;font-size:14px;padding:24px'))
        return label

    def _section(self, body, title, rows, quantities):
        heading = plain_label(title)
        heading.setStyleSheet(themed_style('color:#f0f6fc;font-size:15px;font-weight:700;padding-top:4px'))
        body.addWidget(heading)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for index, (row, demand) in enumerate(rows):
            grid.addWidget(self._card(row, demand, quantities), index // _COLUMNS, index % _COLUMNS)
        body.addLayout(grid)

    def _card(self, row, demand, quantities):
        text = self.text_catalog.render
        single = demand in ('improve', 'push')
        card = QFrame()
        card.setObjectName('rewindDualOverlapCard')
        card.setProperty('shapeId', row.shape_id)
        card.setProperty('demand', demand)
        card.setFixedSize(_CARD_SIZE)
        card.setStyleSheet(themed_style(_CARD_SINGLE if single else _CARD))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 6, 8, 8)
        layout.setSpacing(2)
        if not single:
            # 角标说明该形状在当前八槽中的份数；受八槽限制未选入时明确标出。
            badge = plain_label(text('rewind.dual.overlap.uncovered') if demand == 'uncovered'
                                else text('rewind.dual.overlap.slots', count=int(quantities.get(row.shape_id, 0))))
            badge.setObjectName('rewindDualOverlapSlots')
            badge.setAlignment(Qt.AlignRight)
            badge.setStyleSheet(themed_style('color:#8b949e;font-size:11px' if demand == 'uncovered'
                                             else 'color:#58a6ff;font-size:11px;font-weight:700'))
            layout.addWidget(badge)
        icon = plain_label()
        icon.setObjectName('rewindDualOverlapIcon')
        icon.setAlignment(Qt.AlignCenter)
        pixmap = warehouse_shape_pixmap(row.shape_id, 'Gold', text_catalog=self.text_catalog)
        if not pixmap.isNull():
            icon.setPixmap(pixmap.scaled(_ICON_SIZE, _ICON_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(icon, 1)
        title = plain_label(text('rewind.slots.populate_rewind_slot_grid.shape_label.state', cell_count=f'{row.area}'))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(themed_style('color:#f0f6fc;font-size:12px;font-weight:700'))
        layout.addWidget(title)
        for key, totals in zip(GROUP_KEYS, row.groups):
            group = text('rewind.dual.' + key)
            short = totals.shortfall_count > 0
            line = plain_label(text('rewind.dual.overlap.group_short', group=group,
                                    score=score_amount(totals.shortfall, ceiling=True)) if short
                               else text('rewind.dual.overlap.group_met', group=group))
            line.setObjectName('rewindDualOverlapGroup')
            line.setAlignment(Qt.AlignCenter)
            line.setStyleSheet(themed_style('color:#d29922;font-size:11px' if short else 'color:#8b949e;font-size:11px'))
            layout.addWidget(line)
        return card
