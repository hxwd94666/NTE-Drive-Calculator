# 定义战斗轴推断消费的不可变静态 Buff 规则。
"""Immutable static Buff rule consumed by battle-axis inference."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.battle_report import BattleBuffModifierEvidence


@dataclass(frozen=True, slots=True)
class BattleStaticBuffRule:
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
