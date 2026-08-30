# 创生、浊燃与黯星使用各自正式目标侧公式族。
"""Dedicated fixed-axis ratios for standard reaction formula families."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleTargetCondition,
)
from src.services.battle_fixed_critical_ratio_service import (
    fixed_half_critical_ratio,
)
from src.services.battle_hit_counterfactual_target_support import (
    defense_ratio,
    level_changed,
    resistance_ratio,
    target_resistance_delta,
)


def _gap(
    code: str,
    dimension_id: str,
    property_ids: tuple[str, ...],
    explanation: str,
) -> BattleQuantificationGap:
    return BattleQuantificationGap(
        code=code,
        dimension_id=dimension_id,
        dependency_scope="target_sensitive",
        property_ids=property_ids,
        explanation=explanation,
    )


def compare_standard_reaction(
    *,
    channel_id: str,
    hit: BattleAnalysisHit,
    original: Mapping[str, float],
    candidate: Mapping[str, float],
    changed_properties: set[str],
    original_baseline: BattleCharacterBaseline | None,
    candidate_baseline: BattleCharacterBaseline | None,
    original_projection: BattleHitBuffProjection | None,
    candidate_projection: BattleHitBuffProjection | None,
    replay: BattleHitReplayResult | None,
    target_condition: BattleTargetCondition | None,
    penetration_properties: Mapping[str, str],
    ring_strength_ratio: Callable[..., float | None],
) -> BattleCounterfactualRatio:
    """Apply only the factors consumed by one formal reaction family."""

    replay_attribute = str(
        getattr(replay, "formula_damage_attribute", "") or ""
    ).casefold()
    if channel_id == "reaction_nova":
        attribute = "psyche"
    elif (
        channel_id == "reaction_scorch"
        and hit.gameplay_effect_id.casefold() == "buff_reaction_5_new_1036"
    ):
        attribute = "incantation"
    elif replay_attribute in penetration_properties:
        attribute = replay_attribute
    else:
        hit_attribute = hit.damage_attribute.casefold()
        attribute = (
            hit_attribute if hit_attribute in penetration_properties else ""
        )
    penetration_property = penetration_properties.get(attribute, "")
    consumes_defense = channel_id in {"reaction_creation", "reaction_scorch"}
    permitted = {"MagBase"}
    if consumes_defense:
        permitted.add("DefIgnore")
    if penetration_property:
        permitted.add(penetration_property)
    if channel_id == "reaction_scorch":
        permitted.add("CritDamageBase")
    relevant = changed_properties & permitted
    irrelevant = tuple(sorted(changed_properties - permitted))
    included: list[str] = []
    cancelled: list[str] = []
    gaps: list[BattleQuantificationGap] = []
    ratio = 1.0

    if "MagBase" in relevant:
        ring_ratio = ring_strength_ratio(
            channel_id=channel_id,
            original_strength=max(0.0, original.get("MagBase", 0.0)),
            candidate_strength=max(0.0, candidate.get("MagBase", 0.0)),
            replay=replay,
        )
        if ring_ratio is None:
            gaps.append(_gap(
                "ring_strength_dependency_unresolved",
                "ring_strength",
                ("MagBase",),
                "环合逐击缺少正式环合强度公式因子。",
            ))
        else:
            ratio *= ring_ratio
            included.append("ring_strength")
    else:
        cancelled.append("ring_strength")

    changed_level = level_changed(original_baseline, candidate_baseline)
    if "DefIgnore" in relevant or (consumes_defense and changed_level):
        if target_condition is None:
            gaps.append(_gap(
                "target_defense_dependency_changed",
                "target_defense",
                tuple((
                    *(("DefIgnore",) if "DefIgnore" in relevant else ()),
                    *(("character_level",) if changed_level else ()),
                )),
                "该环合公式消费防御，但缺少冻结敌方防御画像。",
            ))
        else:
            target_ratio = defense_ratio(
                original,
                candidate,
                original_baseline,
                candidate_baseline,
                target_condition,
            )
            if target_ratio is None:
                gaps.append(_gap(
                    "target_defense_dependency_changed",
                    "target_defense",
                    ("DefIgnore",),
                    "该环合公式的防御乘区缺少有效基准值。",
                ))
            else:
                ratio *= target_ratio
                included.append("target_defense")
    else:
        cancelled.append("target_defense")

    original_delta = target_resistance_delta(original_projection, attribute)
    candidate_delta = target_resistance_delta(candidate_projection, attribute)
    resistance_changed = (
        bool(penetration_property and penetration_property in relevant)
        or abs(candidate_delta - original_delta) > 1e-12
    )
    if resistance_changed:
        if target_condition is None or not penetration_property:
            gaps.append(_gap(
                "target_resistance_dependency_changed",
                "target_resistance",
                tuple((penetration_property,) if penetration_property else ()),
                "该环合公式消费正式属性抗性，但缺少冻结目标画像。",
            ))
        else:
            target_ratio = resistance_ratio(
                original,
                candidate,
                attribute,
                penetration_property,
                original_delta,
                candidate_delta,
                target_condition,
            )
            if target_ratio is None:
                gaps.append(_gap(
                    "target_resistance_dependency_changed",
                    "target_resistance",
                    (penetration_property,),
                    "该环合公式的属性抗性乘区缺少有效基准值。",
                ))
            else:
                ratio *= target_ratio
                included.append("target_resistance")
    else:
        cancelled.append("target_resistance")

    if channel_id == "reaction_scorch" and "CritDamageBase" in relevant:
        critical_ratio = fixed_half_critical_ratio(original, candidate, replay)
        if critical_ratio is None:
            gaps.append(_gap(
                "critical_policy_unknown",
                "critical_damage",
                ("CritDamageBase",),
                "浊燃固定 50% 暴击乘区缺少有效暴伤基准。",
            ))
        else:
            ratio *= critical_ratio
            included.append("critical_damage")
    else:
        cancelled.append("critical_damage")
    cancelled.extend((
        "scaling",
        "general_damage_increase",
        "element_damage_increase",
        "character_critical_rate",
        "target_vulnerability",
    ))
    if irrelevant and not relevant and not changed_level:
        return BattleCounterfactualRatio.not_applicable(
            method="reaction_formula_not_applicable",
            dependency_scope="mechanic_specific",
            cancelled_dimension_ids=tuple(dict.fromkeys((
                *cancelled,
                "irrelevant_character_properties",
            ))),
            explanation="正式环合公式不消费本次变化的角色属性。",
        )
    included_ids = tuple(dict.fromkeys(included))
    cancelled_ids = tuple(
        item for item in dict.fromkeys(cancelled) if item not in included_ids
    )
    gap_rows = tuple(gaps)
    if gap_rows and included_ids:
        return BattleCounterfactualRatio.partial(
            ratio,
            method="reaction_formula_partial",
            confidence="低",
            dependency_scope="target_sensitive",
            included_dimension_ids=included_ids,
            cancelled_dimension_ids=cancelled_ids,
            gaps=gap_rows,
            explanation="仅量化该环合正式公式中输入完整的变化乘区。",
        )
    if gap_rows:
        return BattleCounterfactualRatio.unavailable(
            method="reaction_formula_unavailable",
            confidence="低",
            dependency_scope="target_sensitive",
            cancelled_dimension_ids=cancelled_ids,
            gaps=gap_rows,
            explanation="该环合正式公式的目标侧输入不完整。",
        )
    if not included_ids:
        return BattleCounterfactualRatio.not_applicable(
            method="reaction_formula_not_applicable",
            dependency_scope="mechanic_specific",
            cancelled_dimension_ids=cancelled_ids,
            explanation="本次变化不作用于该环合正式公式。",
        )
    return BattleCounterfactualRatio.complete(
        ratio,
        method="reaction_formula_ratio",
        confidence="高",
        dependency_scope=(
            "target_sensitive"
            if {"target_defense", "target_resistance"} & set(included_ids)
            else "mechanic_specific"
        ),
        included_dimension_ids=included_ids,
        cancelled_dimension_ids=cancelled_ids,
        explanation="按该环合渠道的正式目标侧公式族重放全部变化乘区。",
    )
