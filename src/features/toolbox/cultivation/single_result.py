# 渲染单角色养成合计及折叠明细，保持页面编辑与结果展示分离。
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.features.toolbox.cultivation.owned_materials import build_material_grid, remaining_materials
from src.features.toolbox.cultivation.stamina_ui import stamina_runs_text, stamina_summary_text, style_stamina_badge
from src.services.progression.character_requirements import MaterialSummaryStatus
from src.services.progression.models import CultivationPlan, CultivationStaminaPlan


class CultivationSingleResultMixin:
    def _render_plan(self, plan: CultivationPlan) -> None:
        self._clear_result()
        self._owned_materials.set_materials(self._input_options(plan))
        complete = plan.status == MaterialSummaryStatus.COMPLETE
        if not complete:
            summary = QLabel(
                "材料数据不完整，以下为已识别的材料",
                self._result_body,
            )
            summary.setStyleSheet(themed_style(
                "color:#d29922;font-weight:800"
            ))
            self._result_layout.addWidget(summary)
        if plan.required_experience:
            overflow = f"，经验书最小溢出 {plan.experience_overflow:,}" if plan.experience_overflow else ""
            self._result_layout.addWidget(QLabel(
                f"角色升级经验 {plan.required_experience:,}{overflow}", self._result_body
            ))
        if plan.fork_required_experience:
            overflow = (
                f"，材料最小溢出 {plan.fork_experience_overflow:,}"
                if plan.fork_experience_overflow else ""
            )
            self._result_layout.addWidget(QLabel(
                f"弧盘升级经验 {plan.fork_required_experience:,}{overflow}",
                self._result_body,
            ))
        total = QFrame(self._result_body)
        total.setObjectName("cultivationCalculatorTotals")
        total.setStyleSheet(themed_style(
            "QFrame#cultivationCalculatorTotals{background:#0d1117;border:1px solid #58a6ff;border-radius:8px;}"
        ))
        total_layout = QVBoxLayout(total)
        total_layout.setContentsMargins(10, 8, 10, 8)
        owned = self._owned_materials.quantities()
        visible_totals = self._visible(plan.totals)
        remaining = remaining_materials(visible_totals, owned)
        total_header = QHBoxLayout()
        total_heading = QLabel("仍需合计", total)
        total_heading.setStyleSheet(themed_style("color:#58a6ff;font-size:14px;font-weight:900"))
        total_header.addWidget(total_heading)
        total_header.addStretch(1)
        total_stamina = QLabel(
            stamina_summary_text(
                self._last_stamina_plan.total if self._last_stamina_plan else None
            ),
            total,
        )
        style_stamina_badge(total_stamina)
        total_header.addWidget(total_stamina)
        total_layout.addLayout(total_header)
        if remaining:
            total_grid = build_material_grid(
                remaining,
                icon_lookup=self._asset_catalog.progression_item_icon,
                parent=total,
            )
            total_grid.layout_changed.connect(self.layout_changed)
            total_layout.addWidget(total_grid)
        elif visible_totals:
            total_layout.addWidget(QLabel("已有材料已覆盖全部需求", total))
        elif self._material_scope == "stamina":
            total_layout.addWidget(QLabel("本次目标没有需消耗体力刷取的材料", total))
        else:
            total_layout.addWidget(QLabel("本次目标没有新增材料", total))
        total_runs = stamina_runs_text(
            self._last_stamina_plan.total if self._last_stamina_plan else None
        )
        if total_runs:
            run_label = QLabel(total_runs, total)
            run_label.setWordWrap(True)
            run_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
            total_layout.addWidget(run_label)
        self._result_layout.addWidget(total, 0, Qt.AlignmentFlag.AlignTop)
        if plan.gaps:
            self._result_layout.addWidget(QLabel(
                "部分正式材料数量尚未提供，合计只包含已识别条目。", self._result_body
            ))
        if plan.sections:
            self._result_layout.addWidget(
                self._details_panel(plan, self._last_stamina_plan),
                0,
                Qt.AlignmentFlag.AlignTop,
            )
        self._result_layout.addStretch()
        self.layout_changed.emit()

    def _details_panel(
        self,
        plan: CultivationPlan,
        stamina_plan: CultivationStaminaPlan | None,
    ) -> QFrame:
        panel = QFrame(self._result_body)
        panel.setObjectName("cultivationCalculatorDetailsPanel")
        panel.setStyleSheet(themed_style(
            "QFrame#cultivationCalculatorDetailsPanel{background:#0d1117;"
            "border:1px solid #30363d;border-radius:8px;}"
            "QToolButton#cultivationCalculatorDetailsToggle{border:0;"
            "padding:9px;text-align:left;color:#c9d1d9;font-weight:800;}"
        ))
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        toggle = QToolButton(panel)
        toggle.setObjectName("cultivationCalculatorDetailsToggle")
        toggle.setText(f"计算明细 · {len(plan.sections)}项")
        toggle.setCheckable(True)
        toggle.setChecked(self._details_expanded)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(toggle)
        content = QWidget(panel)
        content.setObjectName("cultivationCalculatorDetailsContent")
        content.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 4, 10, 10)
        content_layout.setSpacing(8)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        for index, section in enumerate(plan.sections):
            card = QFrame(content)
            card.setObjectName("cultivationCalculatorResultSection")
            card.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Maximum,
            )
            card.setStyleSheet(themed_style(
                "QFrame#cultivationCalculatorResultSection{background:#161b22;"
                "border:1px solid #30363d;border-radius:8px;}"
            ))
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            heading_row = QHBoxLayout()
            heading = QLabel(section.label, card)
            heading.setStyleSheet(themed_style("color:#58a6ff;font-weight:800"))
            heading_row.addWidget(heading)
            heading_row.addStretch(1)
            section_stamina = (
                stamina_plan.sections[index].result
                if stamina_plan is not None and index < len(stamina_plan.sections)
                else None
            )
            stamina = QLabel(stamina_summary_text(section_stamina), card)
            style_stamina_badge(stamina)
            heading_row.addWidget(stamina)
            card_layout.addLayout(heading_row)
            section_materials = self._visible(section.materials)
            if section_materials:
                grid = build_material_grid(
                    section_materials,
                    icon_lookup=self._asset_catalog.progression_item_icon,
                    parent=card,
                    minimum_card_width=118,
                )
                grid.layout_changed.connect(self.layout_changed)
                card_layout.addWidget(grid)
            else:
                values = QLabel(
                    "本模块没有需消耗体力刷取的材料"
                    if self._material_scope == "stamina" else "无额外材料",
                    card,
                )
                values.setStyleSheet(themed_style("color:#8b949e"))
                card_layout.addWidget(values)
            if section.description:
                description = QLabel(section.description, card)
                description.setWordWrap(True)
                description.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
                card_layout.addWidget(description)
            runs = stamina_runs_text(section_stamina)
            if runs:
                run_label = QLabel(runs, card)
                run_label.setWordWrap(True)
                run_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
                card_layout.addWidget(run_label)
            content_layout.addWidget(card)
        layout.addWidget(content)
        toggle.toggled.connect(
            lambda expanded: self._set_details_expanded(expanded, toggle, content)
        )
        self._set_details_expanded(self._details_expanded, toggle, content)
        return panel

    def _set_details_expanded(
        self,
        expanded: bool,
        toggle: QToolButton,
        content: QWidget,
    ) -> None:
        self._details_expanded = bool(expanded)
        toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        content.setVisible(expanded)
        content.updateGeometry()
        toggle.parentWidget().updateGeometry()
        self.layout_changed.emit()
