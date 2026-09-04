# 将会改变固定轴事件集合的灵可觉醒差异标记为机制级未量化缺口。
"""Awakening boundaries that fixed-axis build replay cannot synthesize."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Sequence

from src.domain.battle_counterfactual import BattleBuildRoleCounterfactual
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.domain.battle_report import BattleCharacterBaseline


LINKO_CHARACTER_ID = 1072
_LINKO_UNGENERATED_BOUNDARIES = {
    "Effect1": (
        "linko_effect1_attack_interval_unquantified",
        "linko_awaken_effect1_attack_interval",
        "灵可一觉会改变攻击间隔，固定轴未生成因此新增或减少的动作与命中。",
    ),
    "Effect3": (
        "linko_effect3_e_cooldown_reset_unquantified",
        "linko_awaken_effect3_e_cooldown_reset",
        "灵可三觉的攻击力和技能等级仍按已有公式量化，但 E 冷却重置可能改变动作与命中集合。",
    ),
    "Effect4": (
        "linko_effect4_added_hit_unquantified",
        "linko_awaken_effect4_added_hit",
        "灵可四觉会新增命中，固定轴没有可靠运行时事件证据，不能把变化当作零收益。",
    ),
    "Effect5": (
        "linko_effect5_added_reaction_unquantified",
        "linko_awaken_effect5_added_reaction",
        "灵可五觉会新增反应结算，固定轴没有可靠运行时事件证据，不能把变化当作零收益。",
    ),
    "Effect6": (
        "linko_effect6_resource_restore_unquantified",
        "linko_awaken_effect6_resource_restore",
        "灵可六觉的暴击和共鸣增伤仍按已有公式量化，但资源回复可能改变后续动作与命中集合。",
    ),
}


def linko_awakening_change_gaps(
    original: Mapping[int, BattleCharacterBaseline],
    candidate: Mapping[int, BattleCharacterBaseline],
) -> tuple[BattleQuantificationGap, ...]:
    """Return only changed Linko awakenings with ungenerated-axis semantics."""

    original_baseline = original.get(LINKO_CHARACTER_ID)
    candidate_baseline = candidate.get(LINKO_CHARACTER_ID)
    if original_baseline is None or candidate_baseline is None:
        return ()
    changed = set(original_baseline.selected_awaken_effect_ids) ^ set(
        candidate_baseline.selected_awaken_effect_ids
    )
    return tuple(
        BattleQuantificationGap(
            code=code,
            dimension_id=dimension_id,
            dependency_scope="mechanic_specific",
            property_ids=(),
            explanation=explanation,
        )
        for effect_id in sorted(changed)
        if effect_id in _LINKO_UNGENERATED_BOUNDARIES
        for code, dimension_id, explanation in (
            _LINKO_UNGENERATED_BOUNDARIES[effect_id],
        )
    )


def with_awakening_gaps(
    quantification: BattleDamageQuantification,
    gaps: Sequence[BattleQuantificationGap],
) -> BattleDamageQuantification:
    """Downgrade a comparison while retaining every already quantified delta."""

    unique_gaps = tuple(dict.fromkeys((*quantification.gaps, *gaps)))
    if not gaps:
        return quantification
    if quantification.status == "unavailable":
        return replace(quantification, gaps=unique_gaps)
    if quantification.status == "not_applicable":
        if quantification.basis_damage <= 0.0:
            return replace(
                quantification,
                status="unavailable",
                quantified_increment=None,
                gaps=unique_gaps,
            )
        return replace(
            quantification,
            status="partial",
            fully_quantified_damage=quantification.basis_damage,
            proven_unchanged_damage=0.0,
            gaps=unique_gaps,
        )
    return replace(quantification, status="partial", gaps=unique_gaps)


def mark_linko_role_partial(
    rows: Sequence[BattleBuildRoleCounterfactual],
    gaps: Sequence[BattleQuantificationGap],
) -> tuple[BattleBuildRoleCounterfactual, ...]:
    """Apply the same missing-event boundary to Linko's role projection."""

    if not gaps:
        return tuple(rows)
    return tuple(
        replace(
            row,
            quantification=with_awakening_gaps(row.quantification, gaps),
            candidate_damage=None,
            gain_percent=None,
            team_gain_percent=None,
        )
        if row.character_id == LINKO_CHARACTER_ID else row
        for row in rows
    )


__all__ = [
    "linko_awakening_change_gaps",
    "mark_linko_role_partial",
    "with_awakening_gaps",
]
