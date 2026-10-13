# 渲染多角色养成合计及惰性创建的角色明细。
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.features.toolbox.cultivation.owned_materials import build_material_grid
from src.features.toolbox.cultivation.stamina_ui import stamina_runs_text, stamina_summary_text, style_stamina_badge
from src.services.progression.batch_planner import CultivationBatchPlan, CultivationMaterialSource, CultivationTargetPlan
from src.services.progression.models import CultivationMaterial
from src.utils.cultivation_trace import trace_cultivation


class CultivationBatchResultMixin:
    def _render_plan(self, plan: CultivationBatchPlan) -> None:
        trace_cultivation(plan.trace_id, "ui.render_begin")
        retired = self._clear_result()
        self._result_layout.addWidget(self._combined_panel(plan))
        ledger = _ledger_index(plan.source_ledger)
        for target in plan.target_plans:
            self._result_layout.addWidget(self._target_result(target, ledger))
        trace_cultivation(plan.trace_id, "ui.result_widgets_attached", targets=len(plan.target_plans))
        self._result_layout.addStretch()
        self.result_replaced.emit(plan.trace_id, retired)
        self.layout_changed.emit()

    def _combined_panel(self, plan: CultivationBatchPlan) -> QFrame:
        panel = QFrame(self._result)
        panel.setObjectName("cultivationBatchCombinedTotals")
        panel.setStyleSheet(themed_style(
            "QFrame#cultivationBatchCombinedTotals{background:#0d1117;"
            "border:1px solid #58a6ff;border-radius:8px;}"
        ))
        layout = QVBoxLayout(panel)
        header = QHBoxLayout()
        title = QLabel("跨角色仍需合计", panel)
        title.setStyleSheet(themed_style("color:#58a6ff;font-size:15px;font-weight:900"))
        header.addWidget(title)
        header.addWidget(QLabel("合并副本掉落已去重", panel))
        header.addStretch(1)
        badge = QLabel(stamina_summary_text(plan.combined_stamina), panel)
        style_stamina_badge(badge)
        header.addWidget(badge)
        layout.addLayout(header)
        if plan.gaps:
            warning = QLabel(
                f"存在 {len(plan.gaps)} 项正式数据缺口，合计仅包含已识别材料。",
                panel,
            )
            warning.setStyleSheet(themed_style("color:#d29922;font-weight:800"))
            layout.addWidget(warning)
        if plan.saved_stamina:
            saved = QLabel(f"相比分角色分别刷取，合并方案节省 {plan.saved_stamina:,} 体力", panel)
            saved.setStyleSheet(themed_style("color:#3fb950;font-weight:800"))
            layout.addWidget(saved)
        remaining_totals = self._visible(plan.remaining_totals)
        if remaining_totals:
            grid = build_material_grid(
                remaining_totals,
                icon_lookup=self._asset_catalog.progression_item_icon,
                parent=panel,
            )
            grid.layout_changed.connect(self.layout_changed)
            layout.addWidget(grid)
        else:
            message = (
                "本次目标没有需消耗体力刷取的材料"
                if self._material_scope == "stamina" and not self._visible(plan.merged_totals)
                else "已有材料已覆盖全部角色需求"
            )
            layout.addWidget(QLabel(message, panel))
        runs = stamina_runs_text(plan.combined_stamina)
        if runs:
            layout.addWidget(_muted_label(runs, panel))
        return panel

    def _target_result(
        self,
        target: CultivationTargetPlan,
        ledger: dict[tuple[str, int], tuple[CultivationMaterialSource, ...]],
    ) -> QFrame:
        panel = QFrame(self._result)
        panel.setObjectName("cultivationBatchTargetResult")
        panel.setStyleSheet(themed_style(
            "QFrame#cultivationBatchTargetResult{background:#0d1117;"
            "border:1px solid #30363d;border-radius:8px;}"
        ))
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        toggle = QToolButton(panel)
        toggle.setObjectName("cultivationBatchTargetResultToggle")
        toggle.setText(f"{target.plan.character_name} · {len(target.plan.sections)} 项明细")
        toggle.setCheckable(True)
        toggle.setChecked(target.line_id in self._expanded_results)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        root.addWidget(toggle)
        content = QWidget(panel)
        content.setObjectName("cultivationBatchTargetResultContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(10, 4, 10, 10)
        layout.setSpacing(8)
        summary = QHBoxLayout()
        summary.addWidget(QLabel("该角色仍需", content))
        summary.addStretch(1)
        badge = QLabel(stamina_summary_text(target.stamina.total), content)
        style_stamina_badge(badge)
        summary.addWidget(badge)
        layout.addLayout(summary)
        root.addWidget(content)
        details_built = False

        def show_details(expanded: bool) -> None:
            nonlocal details_built
            if expanded and not details_built:
                details_built = True
                self._populate_target_details(content, layout, target, ledger)
            self._toggle_result(target.line_id, toggle, content, expanded)

        toggle.toggled.connect(show_details)
        show_details(toggle.isChecked())
        return panel

    def _populate_target_details(
        self,
        content: QWidget,
        layout: QVBoxLayout,
        target: CultivationTargetPlan,
        ledger: dict[tuple[str, int], tuple[CultivationMaterialSource, ...]],
    ) -> None:
        """Build large material grids only when this result is opened."""

        remaining_totals = self._visible(target.remaining_totals)
        if remaining_totals:
            grid = build_material_grid(
                remaining_totals,
                icon_lookup=self._asset_catalog.progression_item_icon,
                parent=content,
            )
            grid.layout_changed.connect(self.layout_changed)
            layout.addWidget(grid)
        for index, section in enumerate(target.plan.sections):
            layout.addWidget(self._section_result(
                target,
                index,
                section.label,
                section.materials,
                ledger.get((target.line_id, index), ()),
            ))

    def _section_result(
        self,
        target: CultivationTargetPlan,
        index: int,
        label: str,
        materials: tuple[CultivationMaterial, ...],
        rows: tuple[CultivationMaterialSource, ...],
    ) -> QFrame:
        card = QFrame(self._result)
        card.setObjectName("cultivationBatchSectionResult")
        card.setStyleSheet(themed_style(
            "QFrame#cultivationBatchSectionResult{background:#161b22;"
            "border:1px solid #30363d;border-radius:7px;}"
        ))
        layout = QVBoxLayout(card)
        header = QHBoxLayout()
        title = QLabel(label, card)
        title.setStyleSheet(themed_style("color:#58a6ff;font-weight:800"))
        header.addWidget(title)
        header.addStretch(1)
        stamina = (
            target.stamina.sections[index].result
            if index < len(target.stamina.sections) else None
        )
        badge = QLabel(stamina_summary_text(stamina), card)
        style_stamina_badge(badge)
        header.addWidget(badge)
        layout.addLayout(header)
        materials = self._visible(materials)
        remaining_by_id = {row.item_id: row.remaining_quantity for row in rows}
        remaining = tuple(
            _quantity(material, remaining_by_id.get(material.item_id, material.quantity))
            for material in materials
            if remaining_by_id.get(material.item_id, material.quantity) > 0
        )
        if remaining:
            grid = build_material_grid(
                remaining,
                icon_lookup=self._asset_catalog.progression_item_icon,
                parent=card,
                minimum_card_width=118,
            )
            grid.layout_changed.connect(self.layout_changed)
            layout.addWidget(grid)
        else:
            message = (
                "本模块没有需消耗体力刷取的材料"
                if self._material_scope == "stamina" and not materials
                else "已有材料已覆盖该模块"
            )
            layout.addWidget(_muted_label(message, card))
        allocated = [
            f"{material.name} × {row.allocated_owned:,}"
            for material in materials
            for row in rows
            if material.item_id == row.item_id and row.allocated_owned
        ]
        if allocated:
            layout.addWidget(_muted_label("已有分配：" + "；".join(allocated), card))
        runs = stamina_runs_text(stamina)
        if runs:
            layout.addWidget(_muted_label(runs, card))
        return card

    def _toggle_result(
        self,
        line_id: str,
        toggle: QToolButton,
        content: QWidget,
        expanded: bool,
    ) -> None:
        if expanded:
            self._expanded_results.add(line_id)
        else:
            self._expanded_results.discard(line_id)
        toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        content.setVisible(expanded)
        self.layout_changed.emit()


def _ledger_index(
    rows: tuple[CultivationMaterialSource, ...],
) -> dict[tuple[str, int], tuple[CultivationMaterialSource, ...]]:
    result: dict[tuple[str, int], list[CultivationMaterialSource]] = {}
    for row in rows:
        result.setdefault((row.line_id, row.section_index), []).append(row)
    return {key: tuple(value) for key, value in result.items()}


def _quantity(material: CultivationMaterial, quantity: int) -> CultivationMaterial:
    return CultivationMaterial(
        material.item_id,
        material.name,
        quantity,
        material.quality,
        material.icon_path,
    )


def _muted_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
    return label
