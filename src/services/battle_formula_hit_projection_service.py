# 按派生公式证据构造逐击计算副本，同时保留 Core 原始逐击身份。
"""Formula-only hit projection shared by replay and Buff projection."""

from __future__ import annotations

from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitReplayResult,
    BattleSkillDamageEvidence,
)


def project_formula_hit(
    hit: BattleAnalysisHit,
    evidence: BattleSkillDamageEvidence | None,
) -> BattleAnalysisHit:
    """Return a formula consumer view without mutating raw attribution."""

    if evidence is None:
        return hit
    panel_character_id = (
        evidence.panel_character_id
        if evidence.panel_character_id is not None
        else evidence.source_character_id
    )
    return replace(
        hit,
        character_id=(
            panel_character_id
            if panel_character_id is not None
            else hit.character_id
        ),
        damage_attribute=(evidence.damage_attribute or hit.damage_attribute),
        is_formal_follow_up=evidence.is_formal_follow_up,
        target_has_weave=evidence.target_has_weave,
        formula_context_kind=evidence.formula_context_kind,
        formula_context_confidence=evidence.formula_context_confidence,
        formula_context_basis=evidence.formula_context_basis,
    )


def project_replay_formula_context(
    replay: BattleHitReplayResult,
    hit: BattleAnalysisHit,
    evidence: BattleSkillDamageEvidence | None,
) -> BattleHitReplayResult:
    """Persist mixed formula identities in the derived per-hit replay result."""

    if evidence is None or not evidence.formula_context_kind:
        return replay
    return replace(
        replay,
        formula_damage_attribute=(
            replay.formula_damage_attribute or hit.damage_attribute
        ),
        formula_action_character_id=evidence.action_character_id,
        formula_definition_owner_character_id=(
            evidence.definition_owner_character_id
        ),
        formula_panel_character_id=evidence.panel_character_id,
        formula_skill_level_character_id=evidence.skill_level_character_id,
        formula_skill_level_ability_id=evidence.ability_id,
        formula_damage_attribute_source=evidence.damage_attribute_source,
        formula_context_kind=evidence.formula_context_kind,
        formula_context_confidence=evidence.formula_context_confidence,
        formula_context_basis=evidence.formula_context_basis,
        formula_is_formal_follow_up=evidence.is_formal_follow_up,
        formula_target_has_weave=evidence.target_has_weave,
    )


__all__ = ["project_formula_hit", "project_replay_formula_context"]
