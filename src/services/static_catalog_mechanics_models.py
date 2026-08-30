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
    MechanicsFamily("damage", "伤害基础", "面板、技能倍率与直伤", "✦", "#58a6ff"),
    MechanicsFamily("multipliers", "增益与减益", "增伤、易伤、暴击、防御与抗性", "◈", "#bc8cff"),
    MechanicsFamily("states", "持续与倾陷", "持续伤害、倾陷与覆纹追加", "◌", "#ff7b72"),
    MechanicsFamily("settlement", "特殊结算", "最终取整、生命上限与独立增伤", "◇", "#39d0d8"),
)
FAMILY_BY_KEY = {family.key: family for family in FAMILIES}

FORMULA_FAMILY_BY_KEY = {
    "panel_attribute": "damage",
    "skill_multiplier": "damage",
    "direct_damage": "damage",
    "damage_increase": "multipliers",
    "vulnerability": "multipliers",
    "critical": "multipliers",
    "defense": "multipliers",
    "resistance": "multipliers",
    "dot_damage": "states",
    "topple_damage": "states",
    "weave_followup": "states",
    "independent_final_damage": "settlement",
    "settlement_rounding": "settlement",
    "max_hp_settlement": "settlement",
}

FORMULA_CHAPTER_BY_KEY = {
    "panel_attribute": "面板",
    "skill_multiplier": "技能倍率",
    "direct_damage": "直伤",
    "damage_increase": "增伤",
    "vulnerability": "易伤",
    "critical": "暴击",
    "defense": "防御",
    "resistance": "抗性",
    "independent_final_damage": "独立增伤",
    "dot_damage": "持续伤害",
    "topple_damage": "倾陷",
    "weave_followup": "覆纹",
    "settlement_rounding": "最终取整",
    "max_hp_settlement": "生命结算",
}
FORMULA_CHAPTER_ORDER = {
    chapter: index
    for index, chapter in enumerate((
        "面板", "技能倍率", "直伤", "增伤", "易伤", "防御", "抗性",
        "暴击", "持续伤害", "倾陷", "覆纹", "独立增伤", "最终取整",
        "生命结算",
    ))
}
# 反事实模型仍供仓库审计使用，但不属于玩家图鉴的分类或卡墙。
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
