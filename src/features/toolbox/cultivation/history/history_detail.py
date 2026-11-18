# 用紧凑配置卡、五列材料图片卡和体力摘要展示冻结历史，次要信息按需展开。
from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import plain_label, resolve_text_catalog
import json

from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import (
    QFrame, QLabel, QScrollArea, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from src.app.theme import themed_style
from src.domain.progression.history import HistoryRecord
from src.features.toolbox.cultivation.history.history_materials import HistoryMaterialGrid, IconLookup


def _label(text: str, parent: QWidget, *, muted: bool = False) -> QLabel:
    label = plain_label(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
    if muted:
        label.setStyleSheet(themed_style("color:#8b949e"))
    return label


class _Section(QFrame):
    def __init__(self, title: str, parent: QWidget, *, expanded: bool = True) -> None:
        super().__init__(parent)
        self.setObjectName("cultivationHistorySection")
        self.setStyleSheet(themed_style(
            "QFrame#cultivationHistorySection{background:#161b22;border:1px solid #30363d;border-radius:8px;}"
            "QToolButton#cultivationHistorySectionTitle{background:transparent;border:none;"
            "color:#f0f6fc;font-size:14px;font-weight:700;text-align:left;padding:4px;}"
        ))
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 10)
        self.toggle = QToolButton(self)
        self.toggle.setObjectName("cultivationHistorySectionTitle")
        self.toggle.setText(title)
        self.toggle.setAccessibleName(title)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        root.addWidget(self.toggle)
        self.body = QWidget(self)
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(5, 4, 5, 4)
        self.content.setSpacing(8)
        root.addWidget(self.body)
        self.toggle.toggled.connect(self._set_expanded)
        self._set_expanded(expanded)

    def _set_expanded(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)


class CultivationHistoryDetail(QScrollArea):
    """只读历史预览；展开和显示范围切换不触发计算或写库。"""

    def __init__(self, parent: QWidget, *, icon_lookup: IconLookup | None = None, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.setObjectName("cultivationHistoryDetail")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._icon_lookup = icon_lookup
        self._record: HistoryRecord | None = None
        self._snapshot: dict = {}
        self.clear()

    @property
    def record(self) -> HistoryRecord | None:
        return self._record

    @property
    def stamina_text(self) -> str:
        if not self._snapshot:
            return ""
        stamina = self._snapshot["stamina"]
        total = stamina["total_stamina"]
        prefix = "估算" if self._snapshot["version"] >= 2 else ""
        return (localized_text(getattr(self, 'text_catalog', None), 'auto.features.toolbox.cultivation.history.history_detail.stamina_text.ff6e2998d', '合并{prefix}体力：{total}', prefix=f'{prefix}', total=f'{total:,}') if total is not None
                else localized_text(getattr(self, 'text_catalog', None), 'auto.features.toolbox.cultivation.history.history_detail.stamina_text.f28ca02eb', '合并已知{prefix}体力：{value} · 总体力未知', prefix=f'{prefix}', value=f"{stamina['known_stamina']:,}"))

    def _new_content(self) -> tuple[QWidget, QVBoxLayout]:
        old = self.takeWidget()
        if old is not None:
            old.hide()
            old.deleteLater()
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(10)
        self.setWidget(content)
        return content, layout

    def clear(self) -> None:
        self._record = None
        self._snapshot = {}
        content, layout = self._new_content()
        layout.addWidget(_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.clear.caption'), content, muted=True))
        layout.addStretch(1)

    def set_record(self, record: HistoryRecord, *, scope: str) -> None:
        if self._record == record:
            self._set_material_scope(scope)
            return
        self._record = record
        configuration = json.loads(record.payload.configuration_json)
        self._snapshot = json.loads(record.payload.result_snapshot_json)
        content, layout = self._new_content()
        self._result_notes(content, layout)
        for number, target in enumerate(configuration["targets"]):
            layout.addWidget(self._target_card(target, content, expanded=number == 0))
        self._materials_section = _Section("当时材料合计", content)
        self._material_grid = HistoryMaterialGrid(self._materials_section.body, icon_lookup=self._icon_lookup, text_catalog=getattr(self, 'text_catalog', None))
        self._material_grid.focus_item_requested.connect(self._ensure_material_visible)
        self._materials_section.content.addWidget(self._material_grid)
        self._empty_materials = _label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.set_record.empty_materials'), self._materials_section.body, muted=True)
        self._materials_section.content.addWidget(self._empty_materials)
        layout.addWidget(self._materials_section)
        self._set_material_scope(scope)
        self._farming_section(content, layout)
        layout.addStretch(1)
        self.verticalScrollBar().setValue(0)

    def _ensure_material_visible(self, rect: QRect) -> None:
        if self._record is None or self.sender() is not self._material_grid or not rect.isValid():
            return
        point = self._material_grid.viewport().mapTo(self.widget(), rect.center())
        self.ensureVisible(point.x(), point.y(), 0, rect.height() // 2 + 8)

    def _target_card(self, target: dict, content: QWidget, *, expanded: bool) -> _Section:
        fork = target["fork"]
        title = target["name"] + (f"（{fork['name']}）" if fork else "")
        section = _Section(title, content, expanded=expanded)
        parts = [self._config_text("人物", self._progress(target, getattr(self, 'text_catalog', None)), target["character_enabled"])]
        skills = target["skills"]
        pairs = {(skill["current_level"], skill["target_level"]) for skill in skills}
        if len(pairs) == 1:
            current, goal = next(iter(pairs))
            skill_text = localized_text(getattr(self, 'text_catalog', None), 'auto.features.toolbox.cultivation.history.history_detail.target_card.fbb58ebfa', '{current} → {goal} 级（{count} 项）', current=f'{current}', goal=f'{goal}', count=f'{len(skills)}')
        else:
            skill_text = "；".join(localized_text(getattr(self, 'text_catalog', None), 'auto.features.toolbox.cultivation.history.history_detail.target_card.feef8f9d0', '项目 {value}：{value_2} → {value_3} 级', value=f'{index + 1}', value_2=f"{skill['current_level']}", value_3=f"{skill['target_level']}")
                                   for index, skill in enumerate(skills)) or "无技能项目"
        parts.append(self._config_text("技能", skill_text, target["skills_enabled"]))
        if fork is not None:
            parts.append(self._config_text("弧盘", self._progress(fork, getattr(self, 'text_catalog', None)), fork["enabled"]))
        section.content.addWidget(_label("  |  ".join(parts), section.body))
        return section

    @staticmethod
    def _progress(values: dict, text_catalog=None) -> str:
        return localized_text(
            text_catalog, "cultivation.history.progress", "{current_level}级 / 突破{current_stage} → {target_level}级 / 突破{target_stage}",
            current_level=f"{values['current_level']}", current_stage=f"{values['current_stage']}",
            target_level=f"{values['target_level']}", target_stage=f"{values['target_stage']}")

    @staticmethod
    def _config_text(title: str, value: str, enabled: bool) -> str:
        return f"{title} {value}" + ("（未参与）" if not enabled else "")

    def _set_material_scope(self, scope: str) -> None:
        materials = [item for item in self._snapshot["materials"] if scope == "all" or item["stamina_eligible"]]
        self._material_grid.set_materials(materials)
        self._material_grid.setVisible(bool(materials))
        self._empty_materials.setVisible(not materials)

    def _result_notes(self, content: QWidget, layout: QVBoxLayout) -> None:
        if self._snapshot["version"] == 2:
            layout.addWidget(_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.result_notes.caption'), content, muted=True))
        if self._snapshot["stamina"]["total_stamina"] is None:
            layout.addWidget(_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.result_notes.caption_2'), content, muted=True))
        if self._snapshot["gaps"]:
            layout.addWidget(_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.result_notes.caption_3', value=f"{len(self._snapshot['gaps'])}"), content, muted=True))

    def _farming_section(self, content: QWidget, layout: QVBoxLayout) -> None:
        if self._snapshot["stamina"]["runs"]:
            runs = _Section("查看刷本建议", content, expanded=False)
            for run in self._snapshot["stamina"]["runs"]:
                runs.content.addWidget(_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.farming_section.caption', value=f"{run['label']}", value_2=f"{run['runs']:,}", value_3=f"{run['total_stamina']:,}"), runs.body))
            layout.addWidget(runs)
