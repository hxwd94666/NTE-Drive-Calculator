# 将会改变固定轴事件或不可恢复状态的觉醒差异标记为未量化缺口。
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
_SHINKU_UNRESOLVED_BOUNDARIES = {
    "Effect3": (
        "shinku_effect3_watch_growth_unquantified",
        "shinku_awaken_effect3_watch_growth",
        "真红第3项觉醒改变离场凝视保留与后台增长；固定轴缺少逐击凝视层数，不能量化因此改变的触发时点和伤害。",
    ),
    "Effect4": (
        "shinku_effect4_watch_cap_unquantified",
        "shinku_awaken_effect4_watch_cap",
        "真红第4项觉醒改变凝视层数上限与追加伤害触发；固定轴不能生成或删除相应命中，也不能假定原击层数不变。",
    ),
}
_CHARACTER_BOUNDARIES = {
    1072: _LINKO_UNGENERATED_BOUNDARIES,
    1076: _SHINKU_UNRESOLVED_BOUNDARIES,
}


def awakening_change_gaps(
    original: Mapping[int, BattleCharacterBaseline],
    candidate: Mapping[int, BattleCharacterBaseline],
) -> tuple[BattleQuantificationGap, ...]:
    """Retain changed mechanics whose event or state axis cannot be rebuilt."""

    return tuple(
        BattleQuantificationGap(
            code=code,
            dimension_id=dimension_id,
            dependency_scope="mechanic_specific",
            property_ids=(),
            explanation=explanation,
        )
        for character_id, boundaries in _CHARACTER_BOUNDARIES.items()
        if character_id in original and character_id in candidate
        for effect_id in sorted(
            set(original[character_id].selected_awaken_effect_ids)
            ^ set(candidate[character_id].selected_awaken_effect_ids)
        )
        if effect_id in boundaries
        for code, dimension_id, explanation in (
            boundaries[effect_id],
        )
    )


def awakening_gaps_for_character(
    gaps: Sequence[BattleQuantificationGap],
    character_id: int,
) -> tuple[BattleQuantificationGap, ...]:
    """Scope team-level awakening gaps to their actual character owner."""

    dimensions = {
        boundary[1]
        for boundary in _CHARACTER_BOUNDARIES.get(character_id, {}).values()
    }
    return tuple(gap for gap in gaps if gap.dimension_id in dimensions)


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


def mark_awakening_roles_partial(
    rows: Sequence[BattleBuildRoleCounterfactual],
    gaps: Sequence[BattleQuantificationGap],
) -> tuple[BattleBuildRoleCounterfactual, ...]:
    """Keep unrelated roles outside each missing-mechanic boundary."""

    if not gaps:
        return tuple(rows)
    return tuple(
        replace(
            row,
            quantification=with_awakening_gaps(row.quantification, role_gaps),
            candidate_damage=None,
            gain_percent=None,
            team_gain_percent=None,
        )
        if role_gaps else row
        for row in rows
        for role_gaps in (awakening_gaps_for_character(gaps, row.character_id),)
    )


__all__ = [
    "awakening_change_gaps",
    "awakening_gaps_for_character",
    "mark_awakening_roles_partial",
    "with_awakening_gaps",
]
