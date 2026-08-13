# 倒带方案的八槽编辑与十二种形状候选。
"""Reusable UI state for saved and manually editable rewind plans."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

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
from src.domain.rewind_shape_recommendation import RewindPricingRule, RewindShape, RewindShapeRecommendation
from src.domain.warehouse_filter import warehouse_shape_order
from src.features.inventory.warehouse import warehouse_shape_pixmap
from src.features.toolbox.rewind_execution_dialog import RewindShapeReplacementDialog
from src.services.blueprint_service import OFFICIAL_SHAPE_LABELS


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
            "已保存方案" if self._slots_complete() else "自定义方案",
            "已载入上次保存的八槽方案。" if self._slots_complete() else "点击空槽，从 12 种驱动中逐个添加。",
        )

    def _render_rewind_slots(self, title: str, detail: str) -> None:
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
        label = QLabel(f"{title}：{detail}")
        label.setWordWrap(True)
        label.setStyleSheet(themed_style("color:#8b949e;font-size:12px;padding:2px 4px"))
        header.addWidget(label, 1)
        clear_button = QPushButton("清空候选")
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
        self._result_tabs.addTab(page, "推荐结果")
        complete = self._slots_complete()
        self._save_plan_button.setEnabled(complete)
        self._start_rewind_button.setEnabled(True)
        self._save_plan_button.setText("保存方案" if complete else "填满八槽后保存")

    def _populate_rewind_slot_grid(self, grid: QGridLayout) -> None:
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
            slot_label = QLabel(f"第 {index + 1} 槽")
            slot_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
            card_layout.addWidget(slot_label)
            if recommendation is None:
                empty = QLabel("+\n点击添加驱动")
                empty.setAlignment(Qt.AlignCenter)
                empty.setStyleSheet(themed_style("font-size:15px;font-weight:700;color:#58a6ff"))
                card_layout.addWidget(empty, 1)
            else:
                icon = QLabel()
                icon.setAlignment(Qt.AlignCenter)
                pixmap = warehouse_shape_pixmap(recommendation.shape.shape_id, "Gold")
                if not pixmap.isNull():
                    icon.setPixmap(pixmap.scaled(86, 86, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    icon.setText("驱动")
                card_layout.addWidget(icon, 1)
                shape_label = QLabel(f"{recommendation.shape.cell_count} 型驱动")
                shape_label.setAlignment(Qt.AlignCenter)
                shape_label.setStyleSheet(themed_style("font-weight:700;color:#f0f6fc"))
                card_layout.addWidget(shape_label)
                quantity = sum(slot.shape.shape_id == recommendation.shape.shape_id for slot in filled)
                probability = self._editable_pricing_rule.probability_for_quantity(quantity)
                probability_label = QLabel(
                    f"缺分 {recommendation.quality_gap:g} · "
                    f"库存 {recommendation.owned_count} · 概率 {probability:.0%}"
                )
                probability_label.setObjectName("rewindShapeMetrics")
                probability_label.setProperty("slotIndex", index)
                probability_label.setAlignment(Qt.AlignCenter)
                probability_label.setWordWrap(False)
                probability_label.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
                card_layout.addWidget(probability_label)
            card.mousePressEvent = lambda _event, slot=index: self._edit_rewind_slot(slot)
            grid.addWidget(card, index // 4, index % 4)

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
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected()
        if selected is None:
            return
        self._editable_slots[slot_index] = selected
        self._render_rewind_slots("自定义方案", "点击槽位可继续逐个调整形状。")

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
        self._editable_slots = [None] * REWIND_SLOT_COUNT
        self._render_rewind_slots(
            "自定义方案",
            "当前页面候选已清空；已保存方案保持不变，重新打开后会再次载入。",
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
                f"缺分 {recommendation.quality_gap:g} · "
                f"库存 {recommendation.owned_count} · 概率 {probability:.0%}"
            )

    def _apply_recommendations(self, recommendations: Iterable[RewindShapeRecommendation]) -> None:
        """Fill only an entirely empty transient plan; saved/manual edits take priority."""

        recommendations = tuple(recommendations)
        recommendations_by_id = {
            recommendation.shape.shape_id: recommendation
            for recommendation in recommendations
        }
        self._replacement_candidates = tuple(
            recommendations_by_id.get(candidate.shape.shape_id, candidate)
            for candidate in self._replacement_candidates
        )
        self._editable_slots = [
            replace(
                slot,
                suit_demand=source.suit_demand,
                owned_count=source.owned_count,
                priority_score=source.priority_score,
                quality_gap=source.quality_gap,
            )
            if slot is not None
            and (source := recommendations_by_id.get(slot.shape.shape_id)) is not None
            else slot
            for slot in self._editable_slots
        ]
        if any(self._editable_slots):
            return
        expanded = [
            recommendation
            for recommendation in recommendations
            for _ in range(recommendation.quantity)
        ][:REWIND_SLOT_COUNT]
        self._editable_slots[:len(expanded)] = expanded
