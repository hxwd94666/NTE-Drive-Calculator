# 角色图鉴的等级面板、突破里程碑与正式材料汇总。
"""Game-styled level progression view without stamina conversion."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.characters.terminology import (
    project_character_term,
)
from src.services.progression.character_requirements import (
    CharacterMaterialRequirement,
    MaterialSummaryStatus,
    project_character_level_requirements,
)
from src.services.static_catalog.characters.models import (
    BreakthroughStage,
    CharacterDetail,
    GrowthPage,
    GrowthPoint,
)
from src.services.static_catalog.common.terminology import (
    StaticCatalogTerminologyService,
)


def _number(value: float) -> str:
    return f"{value:,.1f}".rstrip("0").rstrip(".")


class CharacterGrowthView(QWidget):
    def __init__(
        self,
        *,
        terminology: StaticCatalogTerminologyService | None = None,
        parent: QWidget | None = None,
        text_catalog=None,
    ) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._terminology = terminology
        self._detail: CharacterDetail | None = None
        self._points: tuple[GrowthPoint, ...] = ()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget(scroll)
        root = QVBoxLayout(host)
        root.setContentsMargins(4, 4, 4, 12)
        root.setSpacing(9)
        scroll.setWidget(host)
        outer.addWidget(scroll)

        calculator = QFrame(self)
        calculator.setObjectName("characterLevelCalculator")
        calculator.setStyleSheet(themed_style(
            "QFrame#characterLevelCalculator{background:#161b22;"
            "border:1px solid #30363d;border-radius:14px;}"
        ))
        layout = QVBoxLayout(calculator)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(5)
        title = plain_label(resolve_text_catalog(text_catalog).render('ui.static_catalog.init.title'), calculator)
        title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:15px;font-weight:900"
        ))
        hint = plain_label(
            resolve_text_catalog(text_catalog).render('ui.static_catalog.init.hint'),
            calculator,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        layout.addWidget(title)
        layout.addWidget(hint)
        controls = QHBoxLayout()
        controls.addWidget(plain_label(resolve_text_catalog(text_catalog).render('ui.static_catalog.init.caption'), calculator))
        self.start_level = QComboBox(calculator)
        self.end_level = QComboBox(calculator)
        for level in range(1, 81):
            self.start_level.addItem(f"Lv.{level}", level)
            self.end_level.addItem(f"Lv.{level}", level)
        self.start_level.setCurrentIndex(4)
        self.end_level.setCurrentIndex(79)
        self.include_breakthroughs = QCheckBox(resolve_text_catalog(text_catalog).render('ui.static_catalog.init.include_breakthroughs'), calculator)
        self.include_breakthroughs.setChecked(False)
        controls.addWidget(self.start_level)
        controls.addWidget(plain_label("→", calculator))
        controls.addWidget(self.end_level)
        controls.addWidget(self.include_breakthroughs)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.panel_preview = QFrame(calculator)
        self.panel_preview.setObjectName("characterPanelPreview")
        self.panel_preview.setStyleSheet(themed_style(
            "QFrame#characterPanelPreview{background:#10243f;"
            "border:1px solid #1f6feb;border-radius:12px;}"
        ))
        preview = QHBoxLayout(self.panel_preview)
        preview.setContentsMargins(14, 10, 14, 10)
        self.preview_level = self._metric(self._t("static_catalog.growth.metric.target_panel", "目标面板"), "—")
        self.preview_hp = self._metric(self._t("static_catalog.growth.metric.hp", "生命"), "—")
        self.preview_atk = self._metric(self._t("static_catalog.growth.metric.atk", "攻击"), "—")
        self.preview_def = self._metric(self._t("static_catalog.growth.metric.def", "防御"), "—")
        for widget in (
            self.preview_level, self.preview_hp, self.preview_atk, self.preview_def,
        ):
            preview.addWidget(widget, 1)
        layout.addWidget(self.panel_preview)

        self.progression_result = plain_label(
            resolve_text_catalog(text_catalog).render('ui.static_catalog.init.progression_result'),
            calculator,
        )
        self.progression_result.setObjectName("characterProgressionResult")
        self.progression_result.setWordWrap(True)
        self.progression_result.setStyleSheet(themed_style(
            "color:#d29922;background:#0d1117;border:1px solid #d29922;"
            "border-radius:8px;padding:9px"
        ))
        layout.addWidget(self.progression_result)
        root.addWidget(calculator)

        section_title = plain_label(resolve_text_catalog(text_catalog).render('ui.static_catalog.init.section_title'), self)
        section_title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:16px;font-weight:900"
        ))
        root.addWidget(section_title)
        self.milestone_grid = QGridLayout()
        self.milestone_grid.setHorizontalSpacing(10)
        self.milestone_grid.setVerticalSpacing(10)
        root.addLayout(self.milestone_grid)
        root.addStretch(1)
        self.start_level.currentIndexChanged.connect(self._refresh_all)
        self.end_level.currentIndexChanged.connect(self._refresh_all)
        self.include_breakthroughs.toggled.connect(self._refresh_all)

    @staticmethod
    def _metric(title: str, value: str) -> QFrame:
        card = QFrame()
        content = QVBoxLayout(card)
        content.setContentsMargins(6, 4, 6, 4)
        caption = plain_label(title, card)
        caption.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        metric = plain_label(value, card)
        metric.setObjectName("metricValue")
        metric.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:16px;font-weight:900"
        ))
        content.addWidget(caption)
        content.addWidget(metric)
        return card

    def set_data(self, detail: CharacterDetail, growth: GrowthPage) -> None:
        if detail.character.character_id != growth.character_id:
            return
        self._detail = detail
        self._points = growth.items
        self._clear_grid()
        for index, stage in enumerate(detail.breakthroughs):
            self.milestone_grid.addWidget(
                self._milestone(stage), index // 3, index % 3,
            )
        for column in range(3):
            self.milestone_grid.setColumnStretch(column, 1)
        self._refresh_all()

    def _milestone(self, stage: BreakthroughStage) -> QFrame:
        card = QFrame(self)
        card.setProperty("milestoneCard", True)
        card.setStyleSheet(themed_style(
            "QFrame[milestoneCard='true']{background:#161b22;"
            "border:1px solid #30363d;border-radius:12px;}"
        ))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        title = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.milestone.title', level=f'{stage.level}', stage=f'{stage.stage}'), card)
        title.setStyleSheet(themed_style(
            "color:#58a6ff;font-size:13px;font-weight:900"
        ))
        delta = plain_label(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.milestone.delta', value=f'{_number(stage.after.hp_base - stage.before.hp_base)}', value_2=f'{_number(stage.after.atk_base - stage.before.atk_base)}', value_3=f'{_number(stage.after.def_base - stage.before.def_base)}'),
            card,
        )
        delta.setWordWrap(True)
        delta.setStyleSheet(themed_style("color:#c9d1d9;font-size:11px"))
        requirement = None
        if self._detail is not None and self._detail.progression is not None:
            requirement = next((
                item for item in self._detail.progression.breakthrough_stages
                if item.stage == stage.stage
            ), None)
        if requirement is None:
            cost_text = self._t("static_catalog.growth.cost.missing", "材料 · 当前正式数据未提供")
        elif requirement.costs:
            costs = tuple(
                CharacterMaterialRequirement(cost.item_id, cost.quantity)
                for cost in requirement.costs
            )
            cost_text = self._t("static_catalog.growth.cost.prefix", "材料 · {items}",
                                items=self._format_requirements(costs))
        else:
            cost_text = self._t("static_catalog.growth.cost.none", "材料 · 无额外消耗")
        cost = plain_label(ui_enum_text(getattr(self, 'text_catalog', None), cost_text), card)
        cost.setWordWrap(True)
        cost.setStyleSheet(themed_style("color:#d29922;font-size:10px"))
        layout.addWidget(title)
        layout.addWidget(delta)
        layout.addWidget(cost)
        return card

    def _refresh_all(self) -> None:
        self._refresh_preview()
        self._refresh_materials()

    def _refresh_preview(self) -> None:
        if not self._points:
            self._set_metric(self.preview_level, self._t("static_catalog.growth.preview.missing", "当前正式数据未提供"))
            for widget in (self.preview_hp, self.preview_atk, self.preview_def):
                self._set_metric(widget, "—")
            return
        target = int(self.end_level.currentData())
        candidates = tuple(point for point in self._points if point.level == target)
        point = self._select_target(candidates)
        if point is None:
            self._set_metric(self.preview_level,
                             self._t("static_catalog.growth.preview.no_panel", "Lv.{level} · 无正式面板", level=target))
            return
        state = (
            self._t("static_catalog.growth.preview.state.after", "突破后")
            if point.state == "breakthrough_after"
            else self._t("static_catalog.growth.preview.state.before", "突破前")
        )
        if point.state not in {"breakthrough_before", "breakthrough_after"}:
            state = self._t("static_catalog.growth.preview.state.level", "等级面板")
        self._set_metric(self.preview_level, f"Lv.{target} · {state}")
        self._set_metric(self.preview_hp, _number(point.hp_base))
        self._set_metric(self.preview_atk, _number(point.atk_base))
        self._set_metric(self.preview_def, _number(point.def_base))

    def _select_target(self, points: tuple[GrowthPoint, ...]) -> GrowthPoint | None:
        if not points:
            return None
        preferred = (
            "breakthrough_after" if self.include_breakthroughs.isChecked()
            else "breakthrough_before"
        )
        return next((point for point in points if point.state == preferred), points[0])

    def _refresh_materials(self) -> None:
        detail = self._detail
        if detail is None or detail.progression is None:
            self.progression_result.setText(
                resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.refresh_materials.progression_result')
            )
            return
        projection = project_character_level_requirements(
            detail.progression,
            from_level=int(self.start_level.currentData()),
            to_level=int(self.end_level.currentData()),
            include_breakthroughs=self.include_breakthroughs.isChecked(),
        )
        lines = [self._t("static_catalog.growth.materials.experience", "升级经验 · {experience}",
                         experience=f"{projection.required_experience:,}")]
        if projection.experience_books:
            book_line = self._format_requirements(projection.experience_books)
            overflow = (
                self._t("static_catalog.growth.materials.overflow", "（溢出 {value} 经验）",
                        value=f"{projection.experience_overflow:,}")
                if projection.experience_overflow
                else self._t("static_catalog.growth.materials.no_overflow", "（无溢出）")
            )
            lines.append(self._t("static_catalog.growth.materials.books", "经验书 · {books} {overflow}",
                                 books=book_line, overflow=overflow))
        if self.include_breakthroughs.isChecked():
            materials = (
                self._format_requirements(projection.breakthrough_materials)
                if projection.breakthrough_materials
                else self._t("static_catalog.growth.materials.none", "无额外材料")
            )
            lines.append(self._t("static_catalog.growth.materials.breakthrough", "突破材料 · {items}", items=materials))
        else:
            lines.append(self._t("static_catalog.growth.materials.breakthrough_excluded", "突破材料 · 未计入沿途突破"))
        if projection.additional_costs:
            lines.append(self._t("static_catalog.growth.materials.extra", "额外消耗 · {items}",
                                 items=self._format_requirements(projection.additional_costs)))
        if projection.gaps:
            lines.append(self._t("static_catalog.growth.materials.gaps", "部分正式数量尚未提供，以上仅为已知汇总。"))
        self.progression_result.setText("\n".join(lines))
        available = projection.status == MaterialSummaryStatus.COMPLETE
        self.progression_result.setStyleSheet(themed_style(
            "color:#3fb950;background:#0d1117;border:1px solid #3fb950;"
            "border-radius:8px;padding:9px"
            if available else
            "color:#d29922;background:#0d1117;border:1px solid #d29922;"
            "border-radius:8px;padding:9px"
        ))

    def _t(self, key: str, fallback: str, **values) -> str:
        """界面文案走冻结目录；未注入目录时保持原中文。"""
        return localized_text(getattr(self, "text_catalog", None), key, fallback, **values)

    def _format_requirements(
        self,
        requirements: tuple[CharacterMaterialRequirement, ...],
    ) -> str:
        projections = tuple(
            project_character_term(
                self._terminology,
                entity_kind="item",
                stable_id=item.item_id,
                identity_label=self._t("static_catalog.growth.materials.identity", "材料 {index}", index=index),
                context="progression_cost",
                text_catalog=getattr(self, "text_catalog", None),
            )
            for index, item in enumerate(requirements, start=1)
        )
        return "、".join(
            f"{term.display_name} × {item.required_quantity:,}"
            for item, term in zip(requirements, projections)
        )

    @staticmethod
    def _set_metric(card: QFrame, value: str) -> None:
        label = card.findChild(QLabel, "metricValue")
        if label is not None:
            label.setText(value)

    def _clear_grid(self) -> None:
        while self.milestone_grid.count():
            item = self.milestone_grid.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
