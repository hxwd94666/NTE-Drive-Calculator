# 定义战斗机制图鉴的 Qt-free 公开投影契约与稳定分类。
"""Immutable contracts and taxonomy for the combat-mechanics catalog."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote

from src.domain.static_catalog import CatalogLink


@dataclass(frozen=True, slots=True)
class MechanicsFamily:
    key: str
    title: str
    subtitle: str
    glyph: str
    accent: str


@dataclass(frozen=True, slots=True)
class MechanicsCard:
    record_id: str
    family_key: str
    card_kind: str
    eyebrow: str
    title: str
    subtitle: str
    badges: tuple[str, ...]
    status: str | None = None
    owner_label: str = "公共机制"


@dataclass(frozen=True, slots=True)
class PlayerField:
    label: str
    value: str
    tone: str = "neutral"


@dataclass(frozen=True, slots=True)
class PlayerSection:
    title: str
    fields: tuple[PlayerField, ...]


@dataclass(frozen=True, slots=True)
class EvidenceStage:
    key: str
    label: str
    status: str
    summary: str


@dataclass(frozen=True, slots=True)
class MechanicsDetail:
    record_id: str
    card_kind: str
    title: str
    subtitle: str
    family_key: str
    badges: tuple[str, ...]
    status: str | None
    owner_label: str
    owner_link: CatalogLink | None
    redirect_only: bool
    sections: tuple[PlayerSection, ...]
    identity_fields: tuple[PlayerField, ...]
    evidence_stages: tuple[EvidenceStage, ...]
    related_links: tuple[tuple[str, CatalogLink], ...]
    audit_references: tuple[str, ...]
    notice: str = ""


FAMILIES = (
    MechanicsFamily("attributes", "属性与增益", "面板、Buff、增伤与目标减益", "✦", "#58a6ff"),
    MechanicsFamily("reactions", "异能环合与状态", "反应、Gameplay Tag 与状态消费", "◈", "#bc8cff"),
    MechanicsFamily("dot", "DOT 与持续结算", "持续伤害、层数、周期与专属最终乘区", "◌", "#ff7b72"),
    MechanicsFamily("topple", "倾陷与目标机制", "倾陷、目标画像、防御、抗性与生命结算", "◇", "#39d0d8"),
    MechanicsFamily("events", "召唤、附着物与事件", "派生命中、治疗、护盾、时停与资源缺口", "⌁", "#f2cc60"),
    MechanicsFamily("formula", "伤害公式与反事实覆盖", "公式章节、固定轴和生产覆盖边界", "ƒ", "#d29922"),
)
FAMILY_BY_KEY = {family.key: family for family in FAMILIES}

FORMULA_CHAPTER_BY_KEY = {
    "panel_attribute": "面板",
    "skill_multiplier": "倍率",
    "direct_damage": "属性伤",
    "damage_increase": "通伤",
    "vulnerability": "通伤",
    "critical": "暴击",
    "defense": "防御",
    "resistance": "抗性",
    "independent_final_damage": "特殊机制",
    "dot_damage": "DOT",
    "topple_damage": "倾陷",
    "weave_followup": "特殊机制",
    "settlement_rounding": "特殊机制",
    "max_hp_settlement": "特殊机制",
}
FORMULA_CHAPTER_ORDER = {
    chapter: index
    for index, chapter in enumerate((
        "面板", "倍率", "通伤", "属性伤", "防御", "抗性", "暴击",
        "DOT", "倾陷", "特殊机制",
    ))
}
STATUS_ORDER = {"complete": 0, "partial": 1, "unavailable": 2, "not_applicable": 3}
MODEL_FAMILY_BY_KEY = {
    "buff_ge_attributes": "attributes",
    "formal_dot_classification": "dot",
    "dot_state_replay": "dot",
    "topple_base_formula": "topple",
    "topple_special_states": "topple",
    "max_hp_settlement": "topple",
    "attachments": "events",
    "summon_lifecycle": "events",
    "healing_damage_coupling": "events",
    "healing_without_damage_consumer": "events",
    "shield_state": "events",
    "fixed_axis_replay": "formula",
    "native_counterfactual_core": "formula",
    "unknown_preservation": "formula",
}
PLACEHOLDER_NAME = "名称暂未提供"


def encode_record(kind: str, key: str) -> str:
    return f"{kind}|{quote(str(key), safe='')}"


def decode_record(record_id: str) -> tuple[str, str]:
    kind, separator, encoded = str(record_id).partition("|")
    if not separator or not kind or not encoded:
        raise ValueError("战斗机制记录键格式无效")
    return kind, unquote(encoded)
