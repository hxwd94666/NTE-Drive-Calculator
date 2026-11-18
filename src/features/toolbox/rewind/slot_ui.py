# 倒带方案的八槽编辑与十二种形状候选。
"""Reusable UI state for saved and manually editable rewind plans."""

from __future__ import annotations

from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.rewind.rewind_shape_recommendation import RewindPricingRule, RewindShape, RewindShapeRecommendation
from src.domain.inventory.warehouse_filter import warehouse_shape_order
from src.features.inventory.warehouse.display.cards import warehouse_shape_pixmap
from src.features.toolbox.rewind.execution_dialog import RewindShapeReplacementDialog
from src.domain.rewind.shape_overview import summarize_shape_margins
from src.domain.equipment.geometry.shapes import OFFICIAL_SHAPE_LABELS


REWIND_SLOT_COUNT = 8


def all_rewind_shape_candidates() -> tuple[RewindShapeRecommendation, ...]:
    """Return the game's complete twelve-shape custom-pool catalogue."""

    def cell_count(label: str) -> int:
        return int(label.split("_")[1])

    return tuple(
        RewindShapeRecommendation(
            shape=RewindShape(shape_id, cell_count(label)),
            suit_demand=0,
            owned_count=0,
            priority_score=0.0,
        )
        for shape_id, label in sorted(
            OFFICIAL_SHAPE_LABELS.items(),
            key=lambda item: warehouse_shape_order(item[0]),
        )
    )


def _format_quality_gap(value: float) -> str:
    """仅展示缺分向上保留两位小数，原始分数与保存值保持不变。"""
    # 按十进制文本取整，避免乘以 100 的浮点误差把整百分位再次抬高。
    rounded = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    return f"{rounded:.2f}"


class RewindSlotUiMixin:
    """Render, edit and persist the eight slots without waiting for analysis."""

    def _initialize_rewind_slots(
        self,
        saved_shape_ids: Iterable[str],
        saved_slots: Iterable[object] = (),
    ) -> None:
        candidates = all_rewind_shape_candidates()
        self._replacement_candidates = candidates
        by_id = {candidate.shape.shape_id: candidate for candidate in candidates}
        restored: list[RewindShapeRecommendation] = []
        for value in saved_slots:
            if not isinstance(value, Mapping):
                restored = []
                break
            shape_id = str(value.get("shape_id") or "")
            candidate = by_id.get(shape_id)
            try:
                quality_gap = max(0.0, float(value.get("quality_gap", 0.0)))
            except (TypeError, ValueError):
                restored = []
                break
            if candidate is None:
                restored = []
                break
            restored.append(replace(candidate, quality_gap=quality_gap))
        if len(restored) != REWIND_SLOT_COUNT:
            saved = tuple(str(value) for value in saved_shape_ids)
            if len(saved) != REWIND_SLOT_COUNT or any(
                shape_id not in by_id for shape_id in saved
            ):
                saved = ()
            restored = [by_id[shape_id] for shape_id in saved]
        self._editable_slots: list[RewindShapeRecommendation | None] = [
            *restored[:REWIND_SLOT_COUNT]
        ]
        self._editable_slots.extend([None] * (REWIND_SLOT_COUNT - len(self._editable_slots)))
        self._editable_pricing_rule = RewindPricingRule()
        self._render_rewind_slots(
            resolve_text_catalog(self.text_catalog).render('rewind.slots.saved') if self._slots_complete() else resolve_text_catalog(self.text_catalog).render('rewind.slots.custom'),
            resolve_text_catalog(self.text_catalog).render('rewind.slots.restored') if self._slots_complete() else resolve_text_catalog(self.text_catalog).render('rewind.slots.add'),
        )

    def _render_rewind_slots(self, title: str, detail: str) -> None:
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        while self._result_tabs.count():
            tab = self._result_tabs.widget(0)
            self._result_tabs.removeTab(0)
            if tab is not None:
                tab.setParent(None)
                tab.deleteLater()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 8, 4, 4)
        header = QHBoxLayout()
        label = plain_label(f"{title}：{detail}")
        label.setWordWrap(True)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:12px;padding:2px 4px"))
        header.addWidget(label, 1)
        if self._strategy_key == 'dual_target':
            header.addWidget(self._make_dual_overlap_button())
        clear_button = QPushButton(_ui_text('rewind.slots.render_rewind_slots.clear_button'))
        clear_button.setObjectName("rewindClearCandidates")
        clear_button.setEnabled(any(self._editable_slots))
        clear_button.clicked.connect(self._clear_rewind_slots)
        header.addWidget(clear_button)
        layout.addLayout(header)
        grid = QGridLayout()
        grid.setContentsMargins(2, 4, 2, 4)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._populate_rewind_slot_grid(grid)
        layout.addLayout(grid)
        layout.addStretch(1)
        self._result_tabs.addTab(page, resolve_text_catalog(self.text_catalog).render('rewind.results.tab'))
        complete = self._slots_complete()
        self._save_plan_button.setEnabled(complete and not getattr(self, "_recommendation_invalidated", False))
        self._start_rewind_button.setEnabled(True)
        self._save_plan_button.setText(_ui_text('rewind.slots.render_rewind_slots.save_plan_button.state') if complete else _ui_text('rewind.slots.render_rewind_slots.save_plan_button.state_2'))

    def _populate_rewind_slot_grid(self, grid: QGridLayout) -> None:
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        filled = [slot for slot in self._editable_slots if slot is not None]
        for index, recommendation in enumerate(self._editable_slots):
            card = QFrame()
            card.setObjectName("rewindShapeRecommendationCard")
            card.setFixedSize(176, 190)
            card.setCursor(Qt.PointingHandCursor)
            card.setStyleSheet(themed_style(
                "QFrame#rewindShapeRecommendationCard{background:#161b22;border:1px solid #30363d;border-radius:10px;}"
                "QFrame#rewindShapeRecommendationCard:hover{border-color:#58a6ff;background:#1f6feb18;}"
            ))
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 9, 10, 9)
            card_layout.setSpacing(4)
            slot_label = plain_label(_ui_text('rewind.slots.populate_rewind_slot_grid.slot_label.state', value=f'{index + 1}'))
            slot_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
            card_layout.addWidget(slot_label)
            if recommendation is None:
                empty = plain_label(_ui_text('rewind.slots.populate_rewind_slot_grid.empty'))
                empty.setAlignment(Qt.AlignCenter)
                empty.setStyleSheet(themed_style("font-size:15px;font-weight:700;color:#58a6ff"))
                card_layout.addWidget(empty, 1)
            else:
                icon = plain_label()
                icon.setAlignment(Qt.AlignCenter)
                pixmap = warehouse_shape_pixmap(recommendation.shape.shape_id, "Gold", text_catalog=getattr(self, 'text_catalog', None))
                if not pixmap.isNull():
                    icon.setPixmap(pixmap.scaled(86, 86, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    icon.setText(_ui_text('rewind.slots.populate_rewind_slot_grid.icon'))
                card_layout.addWidget(icon, 1)
                shape_label = plain_label(_ui_text('rewind.slots.populate_rewind_slot_grid.shape_label.state', cell_count=f'{recommendation.shape.cell_count}'))
                shape_label.setAlignment(Qt.AlignCenter)
                shape_label.setStyleSheet(themed_style("font-weight:700;color:#f0f6fc"))
                card_layout.addWidget(shape_label)
                quantity = sum(slot.shape.shape_id == recommendation.shape.shape_id for slot in filled)
                probability = self._editable_pricing_rule.probability_for_quantity(quantity)
                probability_label = plain_label(
                    self._rewind_metric_text(recommendation, probability)
                )
                probability_label.setObjectName("rewindShapeMetrics")
                probability_label.setProperty("slotIndex", index)
                probability_label.setAlignment(Qt.AlignCenter)
                probability_label.setWordWrap(False)
                probability_label.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
                card_layout.addWidget(probability_label)
            card.mousePressEvent = lambda _event, slot=index: self._edit_rewind_slot(slot)
            grid.addWidget(card, index // 4, index % 4)

    def _current_margin_analysis(self):
        """仅当前策略最近一次生成且未失效的结果带逐件余量；载入的已保存方案没有。"""
        analysis = getattr(self, '_generated_analysis', None)
        if analysis is None or getattr(self, '_recommendation_invalidated', False) or not analysis.drive_margins:
            return None
        return analysis

    def _shape_score_totals(self):
        """手选弹窗用的按形状缺分/多分；兼顾模式两组各按自己的目标计算后合计。"""
        analysis = self._current_margin_analysis()
        if analysis is None:
            return None
        return {row.shape_id: row.totals for row in summarize_shape_margins(analysis.drive_margins)}

    def _refresh_candidate_gaps(self, analysis, *, dual: bool) -> None:
        """生成后按本次冻结余量重置十二个候选的缺分/缺件，再由推荐结果覆盖推荐形状。

        手选非推荐形状时八槽卡片显示的缺分与手选弹窗一致；上一次生成留下的旧值不会沿用。
        兼顾模式的“缺件”只表示两组共同需求，非推荐形状一律清零。
        """
        totals = {} if dual else {row.shape_id: row.totals for row in summarize_shape_margins(analysis.drive_margins)}
        self._replacement_candidates = tuple(
            replace(candidate, suit_demand=totals[candidate.shape.shape_id].shortfall_count,
                    quality_gap=totals[candidate.shape.shape_id].shortfall)
            if candidate.shape.shape_id in totals else replace(candidate, suit_demand=0, quality_gap=0.0)
            for candidate in self._replacement_candidates)

    def _edit_rewind_slot(self, slot_index: int) -> None:
        if slot_index < 0 or slot_index >= REWIND_SLOT_COUNT:
            return
        dialog = RewindShapeReplacementDialog(
            self,
            candidates=self._replacement_candidates,
            current_shape_id=(
                self._editable_slots[slot_index].shape.shape_id
                if self._editable_slots[slot_index] is not None else None
            ),
            text_catalog=getattr(self, 'text_catalog', None),
            scores=self._shape_score_totals(),
            selected_shape_ids={slot.shape.shape_id for slot in self._editable_slots if slot is not None},
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected()
        if selected is None:
            return
        self._editable_slots[slot_index] = selected
        self._save_preferences()
        if self._strategy_key == 'dual_target' and self._generated_analysis is not None:
            self._render_dual_results(self._generated_analysis, preserve_edits=True)
            return
        self._render_rewind_slots(resolve_text_catalog(self.text_catalog).render('rewind.slots.custom'), resolve_text_catalog(self.text_catalog).render('rewind.slots.adjust'))

    def _serialize_rewind_slots(self) -> list[dict[str, object]]:
        """Serialize the durable plan fields; inventory remains snapshot-derived."""

        return [
            {
                "shape_id": slot.shape.shape_id,
                "quality_gap": float(slot.quality_gap),
            }
            for slot in self._editable_slots
            if slot is not None
        ]

    def _clear_rewind_slots(self) -> None:
        self._begin_manual_draft()
        self._editable_slots = [None] * REWIND_SLOT_COUNT
        self._save_preferences()
        self._render_rewind_slots(
            resolve_text_catalog(self.text_catalog).render('rewind.slots.custom'),
            resolve_text_catalog(self.text_catalog).render('rewind.slots.cleared'),
        )

    def _slots_complete(self) -> bool:
        return len(self._editable_slots) == REWIND_SLOT_COUNT and all(self._editable_slots)

    def _set_replacement_inventory_counts(self, counts: dict[str, int]) -> None:
        """Refresh all twelve picker candidates and existing slots from one snapshot."""

        self._replacement_candidates = tuple(
            replace(
                candidate,
                owned_count=int(counts.get(candidate.shape.shape_id, 0)),
            )
            for candidate in self._replacement_candidates
        )
        self._editable_slots = [
            replace(
                slot,
                owned_count=int(counts.get(slot.shape.shape_id, 0)),
            )
            if slot is not None
            else None
            for slot in self._editable_slots
        ]
        self._refresh_rewind_metric_labels()

    def _refresh_rewind_metric_labels(self) -> None:
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        filled = [slot for slot in self._editable_slots if slot is not None]
        for label in self.findChildren(QLabel, "rewindShapeMetrics"):
            index = int(label.property("slotIndex"))
            recommendation = self._editable_slots[index]
            if recommendation is None:
                continue
            quantity = sum(
                slot.shape.shape_id == recommendation.shape.shape_id
                for slot in filled
            )
            probability = self._editable_pricing_rule.probability_for_quantity(quantity)
            label.setText(
                self._rewind_metric_text(recommendation, probability)
            )

    def _rewind_metric_text(self, recommendation, probability):
        text = self.text_catalog.render
        if self._strategy_key == 'dual_target':
            # 已保存方案与手动添加的形状没有本次共同需求，不显示“缺件 0”冒充统计结果。
            if recommendation.suit_demand <= 0:
                return text('rewind.dual.slot_metrics_stock', owned=recommendation.owned_count,
                            share=f'{probability:.0%}')
            return text('rewind.dual.slot_metrics', count=recommendation.suit_demand,
                        owned=recommendation.owned_count, share=f'{probability:.0%}')
        return text('rewind.slots.populate_rewind_slot_grid.probability_label.state',
                    value=_format_quality_gap(recommendation.quality_gap),
                    owned_count=str(recommendation.owned_count), probability=f'{probability:.0%}')

    def _apply_recommendations(self, recommendations: Iterable[RewindShapeRecommendation]) -> None:
        """Replace the transient eight slots with the newly generated strategy result.

        A saved plan remains durable until the user explicitly saves again, but
        pressing Generate is an explicit request to discard the current transient
        proposal (including any previous strategy's result) and show this
        strategy's eight slots.
        """

        recommendations = tuple(recommendations)
        recommendations_by_id = {
            recommendation.shape.shape_id: recommendation
            for recommendation in recommendations
        }
        self._replacement_candidates = tuple(
            recommendations_by_id.get(candidate.shape.shape_id, candidate)
            for candidate in self._replacement_candidates
        )
        expanded = [
            recommendation
            for recommendation in recommendations
            for _ in range(recommendation.quantity)
        ][:REWIND_SLOT_COUNT]
        self._editable_slots = [
            *expanded,
            *([None] * (REWIND_SLOT_COUNT - len(expanded))),
        ]
