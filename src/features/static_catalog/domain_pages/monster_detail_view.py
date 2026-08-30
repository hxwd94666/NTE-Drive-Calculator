# 怪物与玩法页面的紧凑详情视图。
"""Compact, progressive-disclosure detail view for monster catalog records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.static_catalog.domain_pages.monster_widgets import (
    BuffCard,
    DropCard,
    MetricCard,
    ResistanceCard,
    clear_layout,
    section_title,
    set_art,
    source_color,
)
from src.services.static_catalog_monster_service import CatalogDetail, CatalogSection


_SUMMARY_FIELDS = {
    "中文名",
    "地区 / 位置",
    "挑战名",
    "Boss 中文名",
    "难度",
    "怪物等级",
    "基础分",
    "得分倍率",
    "特殊高难",
    "层",
    "上/下半场",
    "数量",
    "等级",
    "推荐角色等级",
    "难度等级",
    "队伍等级",
    "体力",
    "击杀时限",
}


@dataclass(frozen=True, slots=True)
class MonsterContext:
    play: str
    scene: str
    level: str = ""
    half: str = ""
    slot: str = ""


class MonsterDetailView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stat_cards: list[MetricCard] = []
        self.resistance_cards: list[ResistanceCard] = []
        self.buff_cards: list[BuffCard] = []
        self.drop_cards: list[DropCard] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.crumb = QLabel("怪物与玩法", self)
        self.crumb.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        top.addWidget(self.crumb)
        top.addStretch(1)
        root.addLayout(top)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(themed_style(
            "QScrollArea{background:#0d1117;border:0;}"
            "QScrollArea>QWidget>QWidget{background:#0d1117;}"
        ))
        self.host = QWidget(scroll)
        self.body = QVBoxLayout(self.host)
        self.body.setContentsMargins(4, 4, 12, 24)
        self.body.setSpacing(10)
        scroll.setWidget(self.host)
        root.addWidget(scroll, 1)

    def set_detail(
        self,
        detail: CatalogDetail,
        *,
        icon: Path | None,
        context: MonsterContext | None = None,
    ) -> None:
        clear_layout(self.body)
        self.stat_cards = []
        self.resistance_cards = []
        self.buff_cards = []
        self.drop_cards = []
        self.crumb.setText(
            "怪物与玩法 / " + (context.play if context else _clean_subtitle(detail))
        )
        self.body.addWidget(self._hero(detail, icon, context))
        profile_sections = tuple(
            section for section in detail.sections if "画像" in section.title
        )
        if profile_sections:
            self.body.addWidget(section_title("战斗画像", "生命、防御、倾陷与抗性"))
            self.body.addWidget(self._profile(profile_sections[0]))
            if len(profile_sections) > 1:
                variants = QWidget(self.host)
                layout = QVBoxLayout(variants)
                layout.setContentsMargins(0, 0, 0, 0)
                for section in profile_sections[1:]:
                    layout.addWidget(self._profile(section))
                self.body.addWidget(_disclosure(
                    f"其他难度 / 档位（{len(profile_sections) - 1}）", variants, self.host,
                ))
        self._add_scene_options(detail)
        self._add_drop_projection(detail)
        self._add_summary(detail)
        self._add_relations(detail)
        if detail.notices:
            note = QLabel("部分名称或数值暂不可用，页面未进行补猜。", self.host)
            note.setWordWrap(True)
            note.setStyleSheet(themed_style(
                "background:#3a2f13;color:#ff9b72;border:1px solid #e3b341;"
                "border-radius:10px;padding:8px;font-size:10px"
            ))
            self.body.addWidget(note)
        self.body.addStretch(1)

    def _add_scene_options(self, detail: CatalogDetail) -> None:
        options = next(
            (
                section for section in detail.sections
                if section.title in {
                    "官方加成选项", "魔女赐福", "轨外赛季 Buff",
                }
            ),
            None,
        )
        values = tuple(
            value for value in (options.values if options else ())
            if "路径" not in value.label and "资源" not in value.label
        )
        if not values:
            return
        heading = {
            "魔女赐福": "战前赐福选择",
            "轨外赛季 Buff": "本期赛季规则",
        }.get(options.title, "场景增益 / 限制")
        self.body.addWidget(section_title(
            heading, f"{len(values)} 项规则，效果说明直接在当前页面展开",
        ))
        if options.note:
            description = QLabel(options.note, self.host)
            description.setWordWrap(True)
            description.setStyleSheet(themed_style(
                "background:#161b22;color:#c9d1d9;border:1px solid #30363d;"
                "border-radius:10px;padding:9px;font-size:10px"
            ))
            self.body.addWidget(description)
        preview = QWidget(self.host)
        preview_grid = QGridLayout(preview)
        preview_grid.setContentsMargins(0, 0, 0, 0)
        for index, value in enumerate(values[:4]):
            card = BuffCard(
                value.display_label or "规则名称暂未提供",
                value.display_value or "规则说明暂未提供",
                preview,
                catalog_link=value.catalog_link,
            )
            self.buff_cards.append(card)
            preview_grid.addWidget(card, index // 2, index % 2)
        self.body.addWidget(preview)
        if len(values) > 4:
            remainder = QWidget(self.host)
            grid = QGridLayout(remainder)
            grid.setContentsMargins(0, 0, 0, 0)
            for index, value in enumerate(values[4:]):
                card = BuffCard(
                    value.display_label or "规则名称暂未提供",
                    value.display_value or "规则说明暂未提供",
                    remainder,
                    catalog_link=value.catalog_link,
                )
                self.buff_cards.append(card)
                grid.addWidget(card, index // 2, index % 2)
            self.body.addWidget(_disclosure(
                f"展开其余 {len(values) - 4} 项", remainder, self.host,
            ))

    def _add_drop_projection(self, detail: CatalogDetail) -> None:
        drops = next(
            (section for section in detail.sections if section.title == "正式掉落"),
            None,
        )
        if drops is None:
            return
        self.body.addWidget(section_title(
            "正式掉落", drops.note or "只展示已确认的掉落物与数量",
        ))
        values = tuple(drops.values)
        preview = QWidget(self.host)
        grid = QGridLayout(preview)
        grid.setContentsMargins(0, 0, 0, 0)
        columns = 1 if self.width() < 700 else 2
        initial = values[:8]
        for index, value in enumerate(initial):
            card = DropCard(
                value.display_label or "名称暂未提供",
                value.display_value or "暂无可确认数量",
                preview,
                warning=value.provenance == "unavailable",
            )
            self.drop_cards.append(card)
            grid.addWidget(card, index // columns, index % columns)
        self.body.addWidget(preview)
        if len(values) > len(initial):
            remainder = QWidget(self.host)
            remainder_grid = QGridLayout(remainder)
            remainder_grid.setContentsMargins(0, 0, 0, 0)
            for index, value in enumerate(values[len(initial):]):
                card = DropCard(
                    value.display_label or "名称暂未提供",
                    value.display_value or "暂无可确认数量",
                    remainder,
                    warning=value.provenance == "unavailable",
                )
                self.drop_cards.append(card)
                remainder_grid.addWidget(card, index // columns, index % columns)
            self.body.addWidget(_disclosure(
                f"展开其余掉落（{len(values) - len(initial)}）",
                remainder,
                self.host,
            ))

    def _add_summary(self, detail: CatalogDetail) -> None:
        values = [
            value
            for section in detail.sections
            if "画像" not in section.title and section.title != "来源追溯"
            for value in section.values
            if value.label in _SUMMARY_FIELDS
        ]
        if not values:
            return
        self.body.addWidget(section_title("玩法摘要", "常用信息"))
        host = QWidget(self.host)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        for index, value in enumerate(values[:9]):
            grid.addWidget(
                MetricCard(
                    value.display_label or value.label,
                    _friendly_value(value.display_value or value.value),
                    source_color(value.provenance),
                    host,
                ),
                index // 3,
                index % 3,
            )
        self.body.addWidget(host)

    def _add_relations(self, detail: CatalogDetail) -> None:
        if not detail.relations:
            return
        self.body.addWidget(section_title(
            "相关画像", "关联内容直接列在本页，不改变当前浏览位置",
        ))
        host = QWidget(self.host)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        columns = 1 if self.width() < 700 else 2
        for index, relation in enumerate(detail.relations):
            card = QFrame(host)
            card.setStyleSheet(themed_style(
                "QFrame{background:#161b22;border:1px solid #30363d;"
                "border-radius:10px;}"
            ))
            copy = QVBoxLayout(card)
            copy.setContentsMargins(10, 7, 10, 7)
            label = QLabel(relation.label or "相关画像", card)
            label.setWordWrap(True)
            label.setStyleSheet(themed_style(
                "color:#f0f6fc;font-size:11px;font-weight:800"
            ))
            copy.addWidget(label)
            if relation.note:
                note = QLabel(relation.note, card)
                note.setWordWrap(True)
                note.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
                copy.addWidget(note)
            grid.addWidget(card, index // columns, index % columns)
        self.body.addWidget(host)

    def _hero(
        self, detail: CatalogDetail, icon: Path | None, context: MonsterContext | None,
    ) -> QFrame:
        hero = QFrame(self.host)
        hero.setObjectName("monsterDetailHero")
        hero.setFixedHeight(156)
        hero.setStyleSheet(themed_style(
            "QFrame#monsterDetailHero{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #10243f,stop:.72 #161b22,stop:1 #0d1117);"
            "border:1px solid #1f6feb;border-radius:16px;}"
        ))
        layout = QHBoxLayout(hero)
        layout.setContentsMargins(14, 10, 16, 10)
        art = QLabel(hero)
        art.setFixedSize(132, 132)
        art.setAlignment(Qt.AlignCenter)
        set_art(art, icon, 126, unavailable=icon is None)
        layout.addWidget(art)
        copy = QVBoxLayout()
        label = QLabel(
            "玩法规则" if detail.entry.key.startswith(("witch_buff|", "outer_buff|"))
            else "敌方档案",
            hero,
        )
        label.setStyleSheet(themed_style("color:#58a6ff;font-size:10px;font-weight:900"))
        title = QLabel(_display_title(detail, context), hero)
        title.setWordWrap(True)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:23px;font-weight:900"))
        copy.addWidget(label)
        copy.addWidget(title)
        values = (
            (context.scene, context.level, context.half, context.slot)
            if context else (_clean_subtitle(detail),)
        )
        summary = QLabel("  •  ".join(value for value in values if value), hero)
        summary.setWordWrap(True)
        summary.setStyleSheet(themed_style("color:#e3b341;font-size:10px;font-weight:800"))
        copy.addWidget(summary)
        copy.addStretch(1)
        layout.addLayout(copy, 1)
        return hero

    def _profile(self, section: CatalogSection) -> QFrame:
        frame = QFrame(self.host)
        frame.setStyleSheet(themed_style(
            "QFrame{background:#161b22;border:1px solid #30363d;border-radius:14px;}"
        ))
        layout = QVBoxLayout(frame)
        title = QLabel(_profile_title(section.title), frame)
        title.setStyleSheet(themed_style("color:#f0f6fc;font-size:12px;font-weight:900"))
        layout.addWidget(title)
        values = {value.label: value for value in section.values}
        stats = (
            ("生命", _join_profile(values, "生命基础", "生命加成", "生命固定值"), "#39d0d8"),
            ("防御", _join_profile(values, "防御基础", "防御加成", "防御固定值", "防御忽略"), "#58a6ff"),
            ("倾陷", _join_profile(values, "倾陷上限", "倾陷恢复"), "#e3b341"),
            ("等级 / 难度", _value_text(values.get("怪物等级")), "#a371f7"),
        )
        grid = QGridLayout()
        for index, (label, value, accent) in enumerate(stats):
            card = MetricCard(label, value, accent, frame)
            self.stat_cards.append(card)
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)
        resistance_grid = QGridLayout()
        resistances = [
            (value.display_label or "抗性名称暂未提供", _value_text(value))
            for label, value in values.items()
            if label.startswith("抗性 ")
        ]
        for index, (label, value) in enumerate(resistances):
            card = ResistanceCard(label, value, frame)
            self.resistance_cards.append(card)
            resistance_grid.addWidget(card, index // 4, index % 4)
        layout.addLayout(resistance_grid)
        penetration = QLabel(
            f"防御忽略 {_value_text(values.get('防御忽略'))}  ·  "
            f"攻击档 {_value_text(values.get('攻击档'))}",
            frame,
        )
        penetration.setWordWrap(True)
        penetration.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
        layout.addWidget(penetration)
        return frame


def _disclosure(title: str, content: QWidget, parent: QWidget) -> QWidget:
    host = QWidget(parent)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    toggle = QToolButton(host)
    toggle.setText(title)
    toggle.setCheckable(True)
    toggle.setChecked(False)
    toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
    toggle.setStyleSheet(themed_style(
        "QToolButton{background:#161b22;color:#8b949e;border:1px solid #30363d;"
        "border-radius:8px;padding:7px 10px;text-align:left;font-weight:700;}"
        "QToolButton:checked{color:#58a6ff;border-color:#58a6ff;}"
    ))
    content.setVisible(False)
    toggle.toggled.connect(content.setVisible)
    layout.addWidget(toggle)
    layout.addWidget(content)
    return host


def _clean_subtitle(detail: CatalogDetail) -> str:
    parts = [part.strip() for part in detail.entry.subtitle.split("·")]
    readable = [part for part in parts if part and "ID" not in part and "_" not in part]
    return " · ".join(readable) or "正式战斗画像"


def _display_title(detail: CatalogDetail, context: MonsterContext | None) -> str:
    if detail.entry.title != detail.entry.primary_id:
        return detail.entry.title
    if context and context.scene:
        return context.scene
    return "怪物画像"


def _profile_title(title: str) -> str:
    return title.replace("等价公式", "").replace("公式", "").strip(" ·") or "战斗画像"


def _join_profile(values: dict[str, object], *labels: str) -> str:
    return "  ·  ".join(
        f"{label.removeprefix('生命').removeprefix('防御').removeprefix('倾陷') or label} "
        f"{_value_text(values.get(label))}" for label in labels
    )


def _value_text(value: object | None) -> str:
    if value is None:
        return "暂无数据"
    display_value = str(getattr(value, "display_value", "") or "")
    raw_value = str(getattr(value, "value", "") or "")
    return _friendly_value(display_value or raw_value)


def _friendly_value(value: str) -> str:
    text = str(value)
    if text.startswith("不可用"):
        return "暂无数据"
    try:
        number = float(text)
    except ValueError:
        return text
    if "e" in text.casefold() or abs(number) >= 10_000:
        return f"{number:,.3f}".rstrip("0").rstrip(".")
    return text
