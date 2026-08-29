# 为主全轴重放生成严格等价的无状态直伤公式缓存键。
"""Exact raw-formula cache keys for full-axis direct-hit replay."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleSkillDamageEvidence,
)


_CACHEABLE_CHANNELS = frozenset({"direct", "direct_follow_up"})


def full_replay_formula_cache_key(
    *,
    channel_id: str,
    formula_label: str,
    hit: BattleAnalysisHit,
    evidence: BattleSkillDamageEvidence,
    baseline: BattleCharacterBaseline,
    projection: BattleHitBuffProjection,
    values: Mapping[str, float],
    analysis: BattleAnalysisSnapshot,
) -> object | None:
    """Return a key only when formula output is independent of prior axis state."""

    if channel_id not in _CACHEABLE_CHANNELS:
        return None
    if (
        evidence.state_multiplier_label
        or abs(float(evidence.state_multiplier) - 1.0) > 1e-12
    ):
        return None
    resolved_target = any(
        str(row.resolved_monster_id or "").strip()
        for row in getattr(analysis, "target_instance_resolutions", ())
    )
    hit_identity = (
        hit.character_id,
        hit.damage_component,
        hit.classification,
        hit.is_follow_up,
        hit.attack_type,
        hit.damage_attribute,
        hit.ability_id,
        hit.gameplay_effect_id,
        hit.skill_name,
        hit.damage_name,
        hit.scope_half.casefold(),
        hit.target_id,
    )
    return (
        channel_id,
        formula_label,
        hit_identity,
        baseline,
        replace(evidence, event_id=""),
        replace(projection, event_id=""),
        tuple(sorted((property_id, float(value)) for property_id, value in values.items())),
        analysis.target_condition,
        resolved_target,
    )


__all__ = ["full_replay_formula_cache_key"]
