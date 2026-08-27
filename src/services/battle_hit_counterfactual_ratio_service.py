# 比较同一真实逐击在基准与候选输入下可安全量化的乘区变化。
"""Shared component-aware ratios for fixed-axis hit counterfactuals."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
    DependencyScope,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_replay_formula_ratio_service import paired_replay_formula
from src.services.damage_calculation_service import (
    DamageScene,
    EnemyDefenseProfileInput,
    calculate_attribute_value,
    calculate_critical_multiplier,
    calculate_defense_multiplier,
    calculate_enemy_defense,
    calculate_enemy_defense_from_profile,
    calculate_resistance_multiplier,
    calculate_weave_followup_damage,
)


_ELEMENT_PROPERTY = {
    "chaos": "DamageUpChaosBase",
    "cosmos": "DamageUpCosmosBase",
    "incantation": "DamageUpIncantationBase",
    "lakshana": "DamageUpLakshanaBase",
    "nature": "DamageUpNatureBase",
    "psyche": "DamageUpPsycheBase",
    "psychically": "DamageUpPsychicallyBase",
}
_PENETRATION_PROPERTY = {
    "chaos": "DamagePenetrateChaos",
    "cosmos": "DamagePenetrateCosmos",
    "incantation": "DamagePenetrateIncantation",
    "lakshana": "DamagePenetrateLakshana",
    "nature": "DamagePenetrateNature",
    "psyche": "DamagePenetratePsyche",
    "psychically": "DamagePenetratePsychically",
}
_SCALING_PROPERTIES = {
    "Atk": ("AtkBase", "AtkUp", "AtkAdd"),
    "HPMax": ("HPMaxBase", "HPMaxUp", "HPMaxAdd"),
    "Def": ("DefBase", "DefUp", "DefAdd"),
}
_ALL_ELEMENT_PROPERTIES = frozenset(_ELEMENT_PROPERTY.values())
_ALL_PENETRATION_PROPERTIES = frozenset(_PENETRATION_PROPERTY.values())
_ALL_SCALING_PROPERTIES = frozenset(
    property_id
    for group in _SCALING_PROPERTIES.values()
    for property_id in group
)
_CRITICAL_PROPERTIES = frozenset({"CritBase", "CritDamageBase"})
_SUPPORTED_CLASSIFICATIONS = frozenset({"direct", "direct_follow_up", "weave"})


def _stats(baseline: BattleCharacterBaseline | None) -> dict[str, float]:
    if baseline is None:
        return {}
    return {row.property_id: float(row.value) for row in baseline.stats}


def _changed(
    original: Mapping[str, float],
    candidate: Mapping[str, float],
    property_ids: set[str] | frozenset[str] | tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(sorted(
        property_id
        for property_id in property_ids
        if abs(
            float(candidate.get(property_id, 0.0))
            - float(original.get(property_id, 0.0))
        ) > 1e-12
    ))


def _safe_ratio(candidate: float, original: float) -> float | None:
    if original <= 0.0 or candidate < 0.0:
        return None
    ratio = candidate / original
    return ratio if isfinite(ratio) and ratio >= 0.0 else None


def _gap(
    code: str,
    dimension_id: str,
    scope: DependencyScope,
    property_ids: tuple[str, ...],
    explanation: str,
) -> BattleQuantificationGap:
    return BattleQuantificationGap(
        code=code,
        dimension_id=dimension_id,
        dependency_scope=scope,
        property_ids=property_ids,
        explanation=explanation,
    )


class BattleHitCounterfactualRatioService:
    """Quantify only changed dimensions and cancel unchanged shared factors."""

    @classmethod
    def compare(
        cls,
        *,
        hit: BattleAnalysisHit,
        original_baseline: BattleCharacterBaseline | None,
        candidate_baseline: BattleCharacterBaseline | None,
        original_projection: BattleHitBuffProjection | None = None,
        candidate_projection: BattleHitBuffProjection | None = None,
        skill_evidence: BattleSkillDamageEvidence | None = None,
        original_replay: BattleHitReplayResult | None = None,
        candidate_replay: BattleHitReplayResult | None = None,
        target_condition: BattleTargetCondition | None = None,
    ) -> BattleCounterfactualRatio:
        """Compare one hit using its already-resolved frozen target condition.

        The caller owns target routing and must pass the result of
        ``BattleTargetInstanceMappingService.analysis_for_hit``. This Service
        never looks up a primary target or display-only monster identity.
        """

        pair = paired_replay_formula(original_replay, candidate_replay)
        if pair is not None:
            ratio = _safe_ratio(pair.candidate_damage, pair.baseline_damage)
            if ratio is not None:
                return BattleCounterfactualRatio.complete(
                    ratio,
                    method=pair.method,
                    confidence="高",
                    dependency_scope="target_sensitive",
                    included_dimension_ids=("structured_formula",),
                    explanation=pair.explanation,
                )

        if hit.classification not in _SUPPORTED_CLASSIFICATIONS:
            gap = _gap(
                "formula_family_unsupported",
                "formula_family",
                "mechanic_specific",
                (),
                "当前逐击公式家族尚未接入公共乘区比较。",
            )
            return BattleCounterfactualRatio.unavailable(
                method="component_ratio_unavailable",
                confidence="低",
                dependency_scope="mechanic_specific",
                cancelled_dimension_ids=(),
                gaps=(gap,),
                explanation=gap.explanation,
            )

        original = cls._projected_values(original_baseline, original_projection)
        candidate = cls._projected_values(candidate_baseline, candidate_projection)
        if not original or not candidate:
            gap = _gap(
                "scaling_dependency_unresolved",
                "character_panel",
                "character_only",
                (),
                "原始或候选角色面板缺失，无法比较角色侧乘区。",
            )
            return BattleCounterfactualRatio.unavailable(
                method="component_ratio_unavailable",
                confidence="低",
                dependency_scope="character_only",
                cancelled_dimension_ids=(),
                gaps=(gap,),
                explanation=gap.explanation,
            )

        included: list[str] = []
        cancelled: list[str] = []
        gaps: list[BattleQuantificationGap] = []
        component_ratio = 1.0
        handled_properties: set[str] = set()
        target_sensitive_change = False
        mechanic_specific_change = False

        scaling_id = cls._scaling_id(
            skill_evidence,
            original_replay,
            candidate_replay,
        )
        handled_properties.update(_ALL_SCALING_PROPERTIES)
        all_scaling_changes = _changed(
            original,
            candidate,
            _ALL_SCALING_PROPERTIES,
        )
        scaling_properties = (
            () if scaling_id is None else _SCALING_PROPERTIES[scaling_id]
        )
        scaling_changes = _changed(original, candidate, scaling_properties)
        if scaling_id is None and all_scaling_changes:
            gaps.append(_gap(
                "scaling_dependency_unresolved",
                "scaling",
                "character_only",
                all_scaling_changes,
                "角色缩放属性发生变化，但该击缺少正式缩放属性证据。",
            ))
        elif scaling_changes:
            ratio = cls._scaling_ratio(
                original,
                candidate,
                scaling_properties,
            )
            if ratio is None:
                gaps.append(_gap(
                    "scaling_dependency_unresolved",
                    "scaling",
                    "character_only",
                    scaling_changes,
                    "角色缩放属性缺少有效基准值。",
                ))
            else:
                component_ratio *= ratio
                included.append("scaling")
        else:
            cancelled.append("scaling")

        critical_changes = _changed(original, candidate, _CRITICAL_PROPERTIES)
        handled_properties.update(_CRITICAL_PROPERTIES)
        if critical_changes:
            ratio = cls._critical_ratio(original, candidate, original_replay)
            if ratio is None:
                gaps.append(_gap(
                    "critical_policy_unknown",
                    "critical",
                    "character_only",
                    critical_changes,
                    "暴击乘区发生变化，但原击暴击分支或正式策略未知。",
                ))
            else:
                component_ratio *= ratio
                included.append("critical")
        else:
            cancelled.append("critical")

        attribute = hit.damage_attribute.casefold()
        increase_properties = {
            "DamageUpGeneralBase",
            _ELEMENT_PROPERTY.get(attribute, ""),
        } - {""}
        increase_changes = _changed(original, candidate, increase_properties)
        handled_properties.update(_ALL_ELEMENT_PROPERTIES)
        handled_properties.add("DamageUpGeneralBase")
        if increase_changes:
            ratio = _safe_ratio(
                cls._increase_factor(candidate, attribute),
                cls._increase_factor(original, attribute),
            )
            if ratio is None:
                gaps.append(_gap(
                    "damage_increase_dependency_unresolved",
                    "damage_increase",
                    "character_only",
                    increase_changes,
                    "角色增伤乘区缺少有效基准值。",
                ))
            else:
                component_ratio *= ratio
                included.append("damage_increase")
        else:
            cancelled.append("damage_increase")

        weave_changes = _changed(original, candidate, ("MagBase",))
        handled_properties.add("MagBase")
        if weave_changes and hit.classification == "weave":
            ratio = _safe_ratio(
                calculate_weave_followup_damage(
                    1.0,
                    max(0.0, candidate.get("MagBase", 0.0)),
                ),
                calculate_weave_followup_damage(
                    1.0,
                    max(0.0, original.get("MagBase", 0.0)),
                ),
            )
            if ratio is None:
                gaps.append(_gap(
                    "scaling_dependency_unresolved",
                    "weave",
                    "character_only",
                    weave_changes,
                    "环合强度乘区缺少有效基准值。",
                ))
            else:
                component_ratio *= ratio
                included.append("weave")
        elif weave_changes:
            cancelled.append("weave")
        else:
            cancelled.append("weave")

        level_changed = cls._level_changed(original_baseline, candidate_baseline)
        defense_changes = _changed(original, candidate, ("DefIgnore",))
        handled_properties.add("DefIgnore")
        if defense_changes or level_changed:
            if attribute == "psychically":
                cancelled.append("target_defense")
            elif target_condition is None:
                target_sensitive_change = True
                property_ids = tuple((
                    *defense_changes,
                    *(("character_level",) if level_changed else ()),
                ))
                gaps.append(_gap(
                    "target_defense_dependency_changed",
                    "target_defense",
                    "target_sensitive",
                    property_ids,
                    "防御乘区发生变化，但该击缺少冻结敌方防御画像。",
                ))
            else:
                target_sensitive_change = True
                ratio = cls._defense_ratio(
                    original,
                    candidate,
                    original_baseline,
                    candidate_baseline,
                    target_condition,
                )
                if ratio is None:
                    gaps.append(_gap(
                        "target_defense_dependency_changed",
                        "target_defense",
                        "target_sensitive",
                        defense_changes,
                        "防御乘区缺少有效基准值。",
                    ))
                else:
                    component_ratio *= ratio
                    included.append("target_defense")
        else:
            cancelled.append("target_defense")

        penetration_property = _PENETRATION_PROPERTY.get(attribute, "")
        penetration_changes = _changed(
            original,
            candidate,
            (() if not penetration_property else (penetration_property,)),
        )
        handled_properties.update(_ALL_PENETRATION_PROPERTIES)
        original_target_delta = cls._target_resistance_delta(
            original_projection,
            attribute,
        )
        candidate_target_delta = cls._target_resistance_delta(
            candidate_projection,
            attribute,
        )
        target_delta_changed = abs(candidate_target_delta - original_target_delta) > 1e-12
        if penetration_changes or target_delta_changed:
            target_sensitive_change = True
            resistance_properties = tuple((
                *penetration_changes,
                *(("target_resistance_modifier",) if target_delta_changed else ()),
            ))
            if target_condition is None:
                gaps.append(_gap(
                    "target_resistance_dependency_changed",
                    "target_resistance",
                    "target_sensitive",
                    resistance_properties,
                    "抗性乘区发生变化，但该击缺少冻结分属性抗性画像。",
                ))
            else:
                ratio = cls._resistance_ratio(
                    original,
                    candidate,
                    attribute,
                    penetration_property,
                    original_target_delta,
                    candidate_target_delta,
                    target_condition,
                )
                if ratio is None:
                    gaps.append(_gap(
                        "target_resistance_dependency_changed",
                        "target_resistance",
                        "target_sensitive",
                        resistance_properties,
                        "抗性乘区缺少有效基准值。",
                    ))
                else:
                    component_ratio *= ratio
                    included.append("target_resistance")
        else:
            cancelled.append("target_resistance")
        cancelled.append("target_vulnerability")

        changed_properties = set(_changed(
            original,
            candidate,
            set(original) | set(candidate),
        ))
        unhandled = tuple(sorted(changed_properties - handled_properties))
        if unhandled:
            mechanic_specific_change = True
            gaps.append(_gap(
                "formula_family_unsupported",
                "unmapped_change",
                "mechanic_specific",
                unhandled,
                "候选改变了尚未映射到公共逐击公式的属性。",
            ))

        included_ids = tuple(dict.fromkeys(included))
        cancelled_ids = tuple(
            dimension_id
            for dimension_id in dict.fromkeys(cancelled)
            if dimension_id not in included_ids
        )
        gap_rows = tuple(gaps)
        scope: DependencyScope = "character_only"
        if mechanic_specific_change:
            scope = "mechanic_specific"
        elif target_sensitive_change:
            scope = "target_sensitive"

        if gap_rows and included_ids:
            return BattleCounterfactualRatio.partial(
                component_ratio,
                method="component_ratio_partial",
                confidence="低",
                dependency_scope=scope,
                included_dimension_ids=included_ids,
                cancelled_dimension_ids=cancelled_ids,
                gaps=gap_rows,
                explanation=(
                    "仅计算输入完整的变化乘区；缺口分量未进入该比值，"
                    "结果不代表完整收益或收益下限。"
                ),
            )
        if gap_rows:
            return BattleCounterfactualRatio.unavailable(
                method="component_ratio_unavailable",
                confidence="低",
                dependency_scope=scope,
                cancelled_dimension_ids=cancelled_ids,
                gaps=gap_rows,
                explanation="本次相关变化缺少必要输入，不能生成候选比值。",
            )
        if not included_ids:
            return BattleCounterfactualRatio.not_applicable(
                method="component_ratio_not_applicable",
                dependency_scope=scope,
                cancelled_dimension_ids=cancelled_ids,
                explanation="已证明本次变化不作用于该击，精确保持原值。",
            )
        return BattleCounterfactualRatio.complete(
            component_ratio,
            method="component_ratio",
            confidence="中",
            dependency_scope=scope,
            included_dimension_ids=included_ids,
            cancelled_dimension_ids=cancelled_ids,
            explanation="已量化全部变化乘区；其余共同乘区在前后比值中相消。",
        )

    @staticmethod
    def _projected_values(
        baseline: BattleCharacterBaseline | None,
        projection: BattleHitBuffProjection | None,
    ) -> dict[str, float]:
        values = _stats(baseline)
        if projection is None:
            return values
        return BattleBuffAttributeProjectionService.apply_additive(
            values,
            projection,
        )

    @staticmethod
    def _scaling_id(
        evidence: BattleSkillDamageEvidence | None,
        original_replay: BattleHitReplayResult | None,
        candidate_replay: BattleHitReplayResult | None,
    ) -> str | None:
        value = "" if evidence is None else str(evidence.scaling_property_id)
        if value in _SCALING_PROPERTIES:
            return value

        for replay in (original_replay, candidate_replay):
            if replay is None:
                continue
            candidates: set[str] = set()
            for factor in replay.factors:
                if factor.factor_id != "scaling":
                    continue
                term_properties = {term.property_id for term in factor.terms}
                for scaling_id, property_ids in _SCALING_PROPERTIES.items():
                    if term_properties & set((*property_ids, scaling_id)):
                        candidates.add(scaling_id)
                label_id = factor.label.partition(" ")[0]
                if label_id in _SCALING_PROPERTIES:
                    candidates.add(label_id)
            if len(candidates) == 1:
                return next(iter(candidates))
        return None

    @staticmethod
    def _scaling_ratio(
        original: Mapping[str, float],
        candidate: Mapping[str, float],
        properties: tuple[str, str, str],
    ) -> float | None:
        base_id, up_id, add_id = properties
        original_value = calculate_attribute_value(
            original.get(base_id, 0.0),
            original.get(up_id, 0.0),
            original.get(add_id, 0.0),
        )
        candidate_value = calculate_attribute_value(
            candidate.get(base_id, 0.0),
            candidate.get(up_id, 0.0),
            candidate.get(add_id, 0.0),
        )
        return _safe_ratio(candidate_value, original_value)

    @staticmethod
    def _critical_ratio(
        original: Mapping[str, float],
        candidate: Mapping[str, float],
        replay: BattleHitReplayResult | None,
    ) -> float | None:
        if replay is None or replay.critical_policy == "unknown":
            return None
        state = replay.critical_state
        if state == "critical":
            original_factor = 1.0 + max(0.0, original.get("CritDamageBase", 0.5))
            candidate_factor = 1.0 + max(0.0, candidate.get("CritDamageBase", 0.5))
        elif state in {"non_critical", "not_applicable"}:
            return 1.0
        elif state == "ambiguous":
            if replay.critical_policy == "disabled":
                return 1.0
            if replay.critical_policy == "fixed":
                rate = min(1.0, max(0.0, float(replay.critical_rate or 0.0)))
                original_rate = candidate_rate = rate
            else:
                original_rate = min(1.0, max(0.0, original.get("CritBase", 0.05)))
                candidate_rate = min(1.0, max(0.0, candidate.get("CritBase", 0.05)))
            original_factor = calculate_critical_multiplier(
                original_rate,
                max(0.0, original.get("CritDamageBase", 0.5)),
            )
            candidate_factor = calculate_critical_multiplier(
                candidate_rate,
                max(0.0, candidate.get("CritDamageBase", 0.5)),
            )
        else:
            return None
        return _safe_ratio(candidate_factor, original_factor)

    @staticmethod
    def _increase_factor(values: Mapping[str, float], attribute: str) -> float:
        property_id = _ELEMENT_PROPERTY.get(attribute, "")
        return max(
            0.0,
            1.0
            + values.get("DamageUpGeneralBase", 0.0)
            + values.get(property_id, 0.0),
        )

    @staticmethod
    def _level_changed(
        original: BattleCharacterBaseline | None,
        candidate: BattleCharacterBaseline | None,
    ) -> bool:
        if original is None or candidate is None:
            return False
        return abs(original.character_level - candidate.character_level) > 1e-12

    @staticmethod
    def _defense_ratio(
        original: Mapping[str, float],
        candidate: Mapping[str, float],
        original_baseline: BattleCharacterBaseline | None,
        candidate_baseline: BattleCharacterBaseline | None,
        condition: BattleTargetCondition,
    ) -> float | None:
        if original_baseline is None or candidate_baseline is None:
            return None
        scene = (
            DamageScene.OPEN_WORLD
            if condition.scene in {"open_world", "big_world"}
            else DamageScene.OUTER_REALM
        )

        def factor(values: Mapping[str, float], character_level: float) -> float:
            penetration = min(1.0, max(-1.0, values.get("DefIgnore", 0.0)))
            if condition.enemy_defense_base is not None:
                enemy_defense = calculate_enemy_defense_from_profile(
                    EnemyDefenseProfileInput(
                        defense_base=condition.enemy_defense_base,
                        defense_up=condition.enemy_defense_up,
                        defense_add=condition.enemy_defense_add,
                    ),
                    penetration,
                    condition.defense_reduction,
                )
            else:
                enemy_defense = calculate_enemy_defense(
                    condition.enemy_level,
                    penetration,
                    condition.defense_reduction,
                    scene,
                )
            return calculate_defense_multiplier(character_level, enemy_defense)

        return _safe_ratio(
            factor(candidate, candidate_baseline.character_level),
            factor(original, original_baseline.character_level),
        )

    @staticmethod
    def _target_resistance_delta(
        projection: BattleHitBuffProjection | None,
        attribute: str,
    ) -> float:
        if projection is None:
            return 0.0
        suffix = attribute.casefold()
        return sum(
            modifier.additive_value
            for modifier in projection.modifiers
            if modifier.target_scope == "target"
            and modifier.property_id.casefold().startswith("damageresist")
            and suffix in modifier.property_id.casefold()
        )

    @staticmethod
    def _resistance_ratio(
        original: Mapping[str, float],
        candidate: Mapping[str, float],
        attribute: str,
        penetration_property: str,
        original_target_delta: float,
        candidate_target_delta: float,
        condition: BattleTargetCondition,
    ) -> float | None:
        base_resistance = dict(condition.resistances).get(attribute, 0.20)
        original_resistance = (
            base_resistance
            + original_target_delta
            - original.get(penetration_property, 0.0)
        )
        candidate_resistance = (
            base_resistance
            + candidate_target_delta
            - candidate.get(penetration_property, 0.0)
        )
        return _safe_ratio(
            max(0.0, calculate_resistance_multiplier(candidate_resistance)),
            max(0.0, calculate_resistance_multiplier(original_resistance)),
        )


__all__ = ["BattleHitCounterfactualRatioService"]
