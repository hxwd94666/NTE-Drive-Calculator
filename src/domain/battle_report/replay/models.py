# 保存技能公式证据和候选重放结果，不覆盖实测伤害或公式消费者身份。
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BattleSkillDamageEvidence:
    """One level-resolved static damage row used by hit replay."""

    event_id: str
    damage_id: str
    ability_id: str
    damage_attribute: str
    damage_source_category: str
    fixed_crit_rate: float
    scaling_property_id: str
    scaling_multiplier: float
    multiplier_coefficient: float
    effective_skill_level: int
    evidence_basis: str
    source_character_id: int | None = None
    formula_kind: str = "skill"
    level_multiplier: float | None = None
    state_multiplier: float = 1.0
    state_multiplier_label: str = ""
    state_multiplier_basis: str = ""
    state_confidence: str = ""
    dot_final_multiplier: float = 1.0
    dot_final_multiplier_basis: str = ""
    critical_policy: Literal["character", "fixed", "disabled", "unknown"] = "character"
    skill_final_multiplier: float = 1.0
    skill_final_multiplier_basis: str = ""
    action_character_id: int | None = None
    definition_owner_character_id: int | None = None
    panel_character_id: int | None = None
    skill_level_character_id: int | None = None
    damage_attribute_source_character_id: int | None = None
    damage_attribute_source: str = "static_gameplay_effect"
    formula_context_kind: str = ""
    formula_context_confidence: str = ""
    formula_context_basis: str = ""
    is_formal_follow_up: bool = False
    target_has_weave: bool = False


@dataclass(frozen=True, slots=True)
class BattleHitReplayFactor:
    """Named factor retained for explaining one replay candidate."""

    factor_id: str
    label: str
    value: float
    evidence_basis: str
    formula: str = ""
    terms: tuple["BattleHitReplayTerm", ...] = ()


@dataclass(frozen=True, slots=True)
class BattleHitReplayTerm:
    """One source-addressable value inside a replay factor."""

    term_id: str
    property_id: str
    label: str
    value: float
    source_group: str
    source_name: str
    is_percent: bool
    evidence_basis: str


@dataclass(frozen=True, slots=True)
class BattleHitReplayResult:
    """实测逐击与非暴击、暴击候选并列；歧义、未知和不可重放单独标记。"""

    event_id: str
    observed_damage: float
    non_critical_damage: float | None
    critical_damage: float | None
    selected_damage: float | None
    selected_error_percent: float | None
    critical_state: Literal[
        "critical",
        "non_critical",
        "not_applicable",
        "ambiguous",
        "unreplayable",
    ]
    confidence: str
    factors: tuple[BattleHitReplayFactor, ...]
    missing_evidence: tuple[str, ...] = ()
    formula_type: str = "未分类"
    critical_rate: float | None = None
    expected_damage: float | None = None
    corrected_expected_damage: float | None = None
    signed_error_percent: float | None = None
    critical_policy: Literal["character", "fixed", "disabled", "unknown"] = "unknown"
    reported_damage: float | None = None
    observed_damage_source: Literal[
        "reported_hit",
        "reported_hit_before_overkill",
        "target_hp_transition_remainder",
    ] = "reported_hit"
    observed_damage_basis: str = ""
    # 正式重放解析出的公式属性；用于原始逐击属性缺失或被通用标签污染时。
    formula_damage_attribute: str = ""
    formula_action_character_id: int | None = None
    formula_definition_owner_character_id: int | None = None
    formula_panel_character_id: int | None = None
    formula_skill_level_character_id: int | None = None
    formula_skill_level_ability_id: str = ""
    formula_damage_attribute_source: str = ""
    formula_context_kind: str = ""
    formula_context_confidence: str = ""
    formula_context_basis: str = ""
    formula_is_formal_follow_up: bool = False
    formula_target_has_weave: bool = False
