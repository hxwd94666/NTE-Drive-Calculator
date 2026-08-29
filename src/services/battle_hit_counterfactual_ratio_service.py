# 计算固定轴逐击反事实的组件感知倍率。
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
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
)
from src.services.battle_fixed_critical_ratio_service import (
    CONTINUOUS_DIRECT_CHANNEL_IDS,
    FIXED_HALF_CRIT_CHANNEL_IDS,
    continuous_direct_attribute,
    fixed_half_critical_counterfactual,
    fixed_half_critical_ratio,
)
from src.services.battle_replay_formula_ratio_service import paired_replay_formula
from src.services.battle_reaction_counterfactual_ratio_service import (
    compare_standard_reaction,
)
from src.services.battle_hit_counterfactual_target_support import (
    defense_ratio,
    level_changed,
    resistance_ratio,
    target_resistance_delta,
)
from src.services.damage_calculation_service import (
    calculate_attribute_value,
    calculate_critical_multiplier,
    calculate_weave_strength_multiplier,
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
_SUPPORTED_CHANNELS = frozenset({
    "direct",
    "direct_follow_up",
    "reaction_hexed",
    "special_kuhara_formula",
    *CONTINUOUS_DIRECT_CHANNEL_IDS,
})
_KUHARA_FORMULA_EFFECTS = frozenset({
    "ge_player_kuhara_seed_damage",
    "ge_player_kuhara_budboom_damage",
    "ge_player_kuhara_budend_damage",
    "ge_player_kuhara_seedreaction_damage",
})
_STANDARD_RING_CHANNELS = frozenset({
    "reaction_creation",
    "reaction_nova",
    "reaction_scorch",
})


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

        channel_id, _channel_label = classify_battle_hit_channel(hit)
        if cls.is_kuhara_formula_hit(hit):
            channel_id = "special_kuhara_formula"
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

        changed_properties = set(_changed(
            original,
            candidate,
            set(original) | set(candidate),
        ))
        fixed_critical = fixed_half_critical_counterfactual(
            channel_id=channel_id,
            changed_properties=changed_properties,
            original=original,
            candidate=candidate,
            replay=original_replay,
        )
        if fixed_critical is not None:
            return fixed_critical
        if channel_id in _STANDARD_RING_CHANNELS:
            return compare_standard_reaction(
                channel_id=channel_id,
                hit=hit,
                original=original,
                candidate=candidate,
                changed_properties=changed_properties,
                original_baseline=original_baseline,
                candidate_baseline=candidate_baseline,
                original_projection=original_projection,
                candidate_projection=candidate_projection,
                replay=original_replay,
                target_condition=target_condition,
                penetration_properties=_PENETRATION_PROPERTY,
                ring_strength_ratio=cls._ring_strength_ratio,
            )
        if changed_properties == {"MagBase"} and cls.supports_ring_strength(
            hit,
            original_replay,
        ):
            ratio = cls._ring_strength_ratio(
                channel_id=channel_id,
                original_strength=max(0.0, original.get("MagBase", 0.0)),
                candidate_strength=max(0.0, candidate.get("MagBase", 0.0)),
                replay=original_replay,
            )
            if ratio is not None:
                return BattleCounterfactualRatio.complete(
                    ratio,
                    method="structured_ring_ratio",
                    confidence="高",
                    dependency_scope="character_only",
                    included_dimension_ids=("ring_strength",),
                    explanation=(
                        "按该逐击已保存的正式环合公式分支，仅替换环合强度乘区。"
                    ),
                )
            gap = _gap(
                "ring_strength_dependency_unresolved",
                "ring_strength",
                "mechanic_specific",
                ("MagBase",),
                "逐击虽有环合公式标记，但缺少可比较的原始环合强度因子。",
            )
            return BattleCounterfactualRatio.unavailable(
                method="structured_ring_ratio_unavailable",
                confidence="低",
                dependency_scope="mechanic_specific",
                cancelled_dimension_ids=(),
                gaps=(gap,),
                explanation=gap.explanation,
            )

        if channel_id in {
            "other_reflected_projectile",
            "special_fadia_shared_damage",
        }:
            explanation = (
                "飞弹反射明确排除在属性边际之外，不猜测其来源联动。"
                if channel_id == "other_reflected_projectile"
                else (
                    "法帝娅共享伤害虽有 300%/600% 与 MAXHP 上限事实，"
                    "但缺少逐击承伤来源联动证据。"
                )
            )
            gap = _gap(
                (
                    "reflected_projectile_unsupported"
                    if channel_id == "other_reflected_projectile"
                    else "fadia_shared_source_unresolved"
                ),
                "source_linkage",
                "mechanic_specific",
                tuple(sorted(changed_properties)),
                explanation,
            )
            return BattleCounterfactualRatio.unavailable(
                method="unsupported_source_linkage",
                confidence="低",
                dependency_scope="mechanic_specific",
                cancelled_dimension_ids=(),
                gaps=(gap,),
                explanation=explanation,
            )
        if channel_id not in _SUPPORTED_CHANNELS:
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
            channel_id=channel_id,
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
            ratio = cls._critical_ratio(
                original,
                candidate,
                original_replay,
                channel_id=channel_id,
            )
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

        attribute = (
            "nature"
            if channel_id == "special_kuhara_formula"
            else continuous_direct_attribute(hit)
            or hit.damage_attribute.casefold()
        )
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
        if weave_changes:
            mechanic_specific_change = True
            gaps.append(_gap(
                "ring_strength_dependency_unresolved",
                "ring_strength",
                "mechanic_specific",
                weave_changes,
                "该击没有保存可确认的环合强度公式因子，不能按名称猜测收益。",
            ))
        else:
            cancelled.append("weave")

        has_level_change = level_changed(original_baseline, candidate_baseline)
        defense_changes = _changed(original, candidate, ("DefIgnore",))
        handled_properties.add("DefIgnore")
        if defense_changes or has_level_change:
            if attribute == "true":
                mechanic_specific_change = True
                gaps.append(_gap(
                    "true_attribute_override_missing",
                    "target_defense",
                    "mechanic_specific",
                    tuple((
                        *defense_changes,
                        *(("character_level",) if has_level_change else ()),
                    )),
                    (
                        "静态 TRUE 没有独立抗性；该渠道缺少将 TRUE 外壳"
                        "还原为正式角色属性的专用公式适配器。"
                    ),
                ))
            elif attribute == "psychically":
                cancelled.append("target_defense")
            elif target_condition is None:
                target_sensitive_change = True
                property_ids = tuple((
                    *defense_changes,
                    *(("character_level",) if has_level_change else ()),
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
                ratio = defense_ratio(
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
        original_target_delta = target_resistance_delta(
            original_projection,
            attribute,
        )
        candidate_target_delta = target_resistance_delta(
            candidate_projection,
            attribute,
        )
        target_delta_changed = abs(candidate_target_delta - original_target_delta) > 1e-12
        if penetration_changes or target_delta_changed:
            resistance_properties = tuple((
                *penetration_changes,
                *(("target_resistance_modifier",) if target_delta_changed else ()),
            ))
            if attribute == "true":
                mechanic_specific_change = True
                gaps.append(_gap(
                    "true_attribute_override_missing",
                    "target_resistance",
                    "mechanic_specific",
                    resistance_properties,
                    "静态 TRUE 没有可供读取或穿透的独立属性抗性。",
                ))
            elif target_condition is None:
                target_sensitive_change = True
                gaps.append(_gap(
                    "target_resistance_dependency_changed",
                    "target_resistance",
                    "target_sensitive",
                    resistance_properties,
                    "抗性乘区发生变化，但该击缺少冻结分属性抗性画像。",
                ))
            else:
                target_sensitive_change = True
                ratio = resistance_ratio(
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
    def supports_ring_strength(
        hit: BattleAnalysisHit,
        replay: BattleHitReplayResult | None,
    ) -> bool:
        """Accept only replay-proven ring consumers; stain remains unsupported."""

        if replay is None or replay.critical_state == "unreplayable":
            return False
        channel_id, _channel_label = classify_battle_hit_channel(hit)
        factor_ids = {factor.factor_id for factor in replay.factors}
        if channel_id == "reaction_hexed":
            return {"weave_strength", "weave_followup"} <= factor_ids
        return channel_id in _STANDARD_RING_CHANNELS and "scaling" in factor_ids

    @staticmethod
    def is_kuhara_formula_hit(hit: BattleAnalysisHit) -> bool:
        identity = hit.gameplay_effect_id.replace("\\", "/").rsplit("/", 1)[-1]
        normalized = identity.casefold().removesuffix("_c")
        return normalized in _KUHARA_FORMULA_EFFECTS

    @staticmethod
    def _ring_strength_ratio(
        *,
        channel_id: str,
        original_strength: float,
        candidate_strength: float,
        replay: BattleHitReplayResult | None,
    ) -> float | None:
        if replay is None:
            return None
        factors = {factor.factor_id: float(factor.value) for factor in replay.factors}
        if channel_id in _STANDARD_RING_CHANNELS:
            if "scaling" not in factors:
                return None
            return _safe_ratio(
                1.0 + candidate_strength / 600.0,
                1.0 + original_strength / 600.0,
            )
        if channel_id != "reaction_hexed":
            return None
        original_zone = factors.get("weave_strength")
        original_followup = factors.get("weave_followup")
        if (
            original_zone is None
            or original_followup is None
            or original_zone <= 0.0
        ):
            return None
        base_multiplier = (original_followup + 1.0) / original_zone
        candidate_followup = (
            base_multiplier * calculate_weave_strength_multiplier(candidate_strength)
            - 1.0
        )
        return _safe_ratio(candidate_followup, original_followup)

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
        *,
        channel_id: str = "",
    ) -> str | None:
        value = "" if evidence is None else str(evidence.scaling_property_id)
        if value in _SCALING_PROPERTIES:
            return value
        if channel_id in CONTINUOUS_DIRECT_CHANNEL_IDS:
            return "Atk"
        if channel_id == "special_kuhara_formula":
            return "Atk"

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
        *,
        channel_id: str = "",
    ) -> float | None:
        fixed_half = channel_id in FIXED_HALF_CRIT_CHANNEL_IDS
        if fixed_half:
            return fixed_half_critical_ratio(original, candidate, replay)
        if (replay is None or replay.critical_policy == "unknown") and not fixed_half:
            return None
        state = "unreplayable" if replay is None else replay.critical_state
        if state == "critical":
            original_factor = 1.0 + max(0.0, original.get("CritDamageBase", 0.5))
            candidate_factor = 1.0 + max(0.0, candidate.get("CritDamageBase", 0.5))
        elif state == "non_critical":
            return 1.0
        elif state in {"ambiguous", "unreplayable", "not_applicable"}:
            if replay is not None and replay.critical_policy == "disabled":
                return 1.0
            if replay is not None and replay.critical_policy == "fixed":
                saved_rate = float(replay.critical_rate or 0.5)
                rate = min(1.0, max(0.0, saved_rate))
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


__all__ = ["BattleHitCounterfactualRatioService"]
