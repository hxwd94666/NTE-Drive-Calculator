# 保存冻结 Buff 规则、区间和逐击投影决策，不执行公式或补读活动状态。
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BattleBuffModifierEvidence:
    """One normalized modifier carried by an inferred Buff interval."""

    property_id: str
    modifier_operation: str
    magnitude_kind: str
    magnitude_value: float | None
    calculation_asset_path: str
    value_confidence: str
    modifier_group_ordinal: int = 0
    application_requirement_asset_path: str = ""
    source_require_tags: tuple[str, ...] = ()
    source_ignore_tags: tuple[str, ...] = ()
    target_require_tags: tuple[str, ...] = ()
    target_ignore_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleInferredBuffInterval:
    """可重算和删除的 Buff 状态推断，始终区别于实际采集事实。"""

    interval_id: str
    buff_asset_path: str
    buff_name: str
    source_effect_definition_id: str
    source_kind: str
    source_character_id: int
    source_character_name: str
    target_scope: str
    start_us: int
    end_us: int
    stacks: int
    duration_policy: str
    state_confidence: str
    value_confidence: str
    inference_basis: str
    trigger_event_type: str
    evidence_action_ids: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    modifiers: tuple[BattleBuffModifierEvidence, ...]
    stacking_type: str = ""
    stack_limit_count: int = 1
    target_id: str = ""
    native_window_end_us: int | None = None
    scope_half: str = ""


@dataclass(frozen=True, slots=True)
class BattleProjectedBuffModifier:
    """One safe additive modifier projected onto a concrete damage hit."""

    property_id: str
    additive_value: float
    interval_ids: tuple[str, ...]
    buff_names: tuple[str, ...]
    confidence: str
    target_scope: str = "self"


@dataclass(frozen=True, slots=True)
class BattleBuffProjectionDecision:
    """Per-interval decision explaining whether one hit consumed the evidence."""

    interval_id: str
    buff_name: str
    status: Literal["applied", "not_applied", "unresolved"]
    applied_property_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    observed_stacks: int | None = None
    state_confidence: str = ""


@dataclass(frozen=True, slots=True)
class BattleHitBuffProjection:
    """Derived per-hit Buff input; excluded evidence remains explainable."""

    event_id: str
    modifiers: tuple[BattleProjectedBuffModifier, ...]
    applied_interval_ids: tuple[str, ...]
    excluded_interval_ids: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    confidence: str
    decisions: tuple[BattleBuffProjectionDecision, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleStaticBuffRule:
    """冻结静态 Buff 的条件、叠层与时长；缺失时长保留 None，不推成常驻。"""

    rule_id: str
    source_effect_definition_id: str
    source_kind: str
    source_character_id: int
    source_character_name: str
    source_asset_path: str
    target_asset_path: str
    target_name: str
    target_scope: str
    event_type: str
    effect_type: str
    duration_policy: str
    duration_seconds: float | None
    stack_count: int
    modifiers: tuple[BattleBuffModifierEvidence, ...]
    stacking_type: str = ""
    stack_limit_count: int = 1
    cooldown_seconds: float | None = None
    application_requirement_asset_path: str = ""
