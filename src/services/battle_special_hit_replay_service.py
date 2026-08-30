# 重放已由真实战报和静态等级曲线共同验证的特殊伤害。
"""Narrow per-hit adapters for non-direct battle damage channels."""

from __future__ import annotations

from collections.abc import Mapping

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitBuffProjection,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleHitReplayTerm,
    BattleSkillDamageEvidence,
)
from src.services.battle_hit_replay_support import (
    ceil_replay_damage,
    dot_final_replay_factors,
    literal_replay_term,
)
from src.services.battle_weave_source_service import find_paired_weave_source_hit
from src.services.damage_calculation_service import (
    DamageScene,
    EnemyDefenseProfileInput,
    calculate_defense_multiplier,
    calculate_enemy_defense,
    calculate_enemy_defense_from_profile,
    calculate_resistance_multiplier,
    calculate_ring_strength_multiplier,
    calculate_weave_strength_multiplier,
)


_ELEMENT_PENETRATION_PROPERTIES = {
    "chaos": "DamagePenetrateChaos",
    "cosmos": "DamagePenetrateCosmos",
    "incantation": "DamagePenetrateIncantation",
    "lakshana": "DamagePenetrateLakshana",
    "nature": "DamagePenetrateNature",
    "psyche": "DamagePenetratePsyche",
    "psychically": "DamagePenetratePsychically",
}

_ELEMENT_DAMAGE_PROPERTIES = {
    "chaos": "DamageUpChaosBase",
    "cosmos": "DamageUpCosmosBase",
    "incantation": "DamageUpIncantationBase",
    "lakshana": "DamageUpLakshanaBase",
    "nature": "DamageUpNatureBase",
    "psyche": "DamageUpPsycheBase",
    "psychically": "DamageUpPsychicallyBase",
}

_ELEMENT_RESISTANCE_PROPERTIES = {
    element: (
        f"DamageResist{element.title()}Base",
        f"DamageResist{element.title()}Add",
    )
    for element in _ELEMENT_PENETRATION_PROPERTIES
}
_ORDINARY_SCORCH_DAMAGE_ID = "buff_reaction_5_new"
_ZANKOU_SCORCH_DAMAGE_ID = "buff_reaction_5_new_1036"


def _factor(
    factor_id: str,
    label: str,
    value: float,
    basis: str,
    formula: str,
    *,
    terms: tuple[BattleHitReplayTerm, ...] = (),
) -> BattleHitReplayFactor:
    return BattleHitReplayFactor(
        factor_id=factor_id,
        label=label,
        value=float(value),
        evidence_basis=basis,
        formula=formula,
        terms=terms,
    )


def _signed_error(observed: float, predicted: float) -> float | None:
    if observed <= 0.0:
        return None
    return (predicted - observed) / observed * 100.0


class BattleSpecialHitReplayService:
    """Replay only special channels whose current formula is fully bounded."""

    @classmethod
    def replay(
        cls,
        *,
        channel_id: str,
        formula_label: str,
        hit: BattleAnalysisHit,
        evidence: BattleSkillDamageEvidence | None,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        analysis: BattleAnalysisSnapshot,
    ) -> BattleHitReplayResult | None:
        if channel_id == "reaction_hexed":
            return cls._replay_weave(
                hit=hit,
                projection=projection,
                values=values,
                analysis=analysis,
                formula_label=formula_label,
            )
        if evidence is None:
            return None
        if channel_id == "reaction_nova":
            return cls._replay_dark_star(
                hit=hit,
                evidence=evidence,
                projection=projection,
                values=values,
                analysis=analysis,
                formula_label=formula_label,
            )
        if channel_id in {"reaction_creation", "reaction_scorch"}:
            return cls._replay_standard_reaction(
                channel_id=channel_id,
                hit=hit,
                evidence=evidence,
                projection=projection,
                values=values,
                analysis=analysis,
                formula_label=formula_label,
            )
        return None

    @staticmethod
    def _replay_weave(
        *,
        hit: BattleAnalysisHit,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        analysis: BattleAnalysisSnapshot,
        formula_label: str,
    ) -> BattleHitReplayResult:
        triggering_hit = find_paired_weave_source_hit(hit, analysis.hits)
        if triggering_hit is None:
            return BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
                missing_evidence=("缺少与覆纹同一正式事件的原伤害",),
                formula_type=formula_label,
            )
        baseline = next(
            (
                row
                for row in analysis.baselines
                if row.character_id == hit.character_id
            ),
            None,
        )
        lingke_passive = bool(
            baseline is not None
            and "PASSIVE-1072-GA_Radio072_Passive_1"
            in baseline.enabled_team_passive_ids
        )
        ring_strength = max(0.0, float(values.get("MagBase", 0.0)))
        strength_multiplier = calculate_weave_strength_multiplier(ring_strength)
        base_extra_ratio = 0.30 if lingke_passive else 0.20
        followup_multiplier = (1.0 + base_extra_ratio) * strength_multiplier - 1.0

        attribute_property = _ELEMENT_DAMAGE_PROPERTIES.get(
            str(hit.damage_attribute).casefold(), ""
        )
        existing_damage_bonus = float(values.get("DamageUpGeneralBase", 0.0))
        if attribute_property:
            existing_damage_bonus += float(values.get(attribute_property, 0.0))
        existing_damage_zone = max(0.000001, 1.0 + existing_damage_bonus)
        passive_damage_zone = (
            (existing_damage_zone + 0.10) / existing_damage_zone
            if lingke_passive
            else 1.0
        )
        predicted = ceil_replay_damage(
            triggering_hit.damage * followup_multiplier * passive_damage_zone
        )
        signed_error = _signed_error(hit.damage, predicted)
        absolute_error = None if signed_error is None else abs(signed_error)
        confidence = (
            "高" if absolute_error is not None and absolute_error <= 2.0
            else "中" if absolute_error is not None and absolute_error <= 5.0
            else "低"
        )
        unresolved = sum(row.status == "unresolved" for row in projection.decisions)
        missing = (
            ()
            if unresolved == 0
            else (f"{unresolved} 个覆纹相关 Buff 仍待结构化",)
        )
        factors = (
            _factor(
                "recorded_direct_damage",
                "原伤害实际值",
                triggering_hit.damage,
                f"正式逐击 {triggering_hit.event_id}",
                "直接使用同一事件记录的有效原伤害",
            ),
            _factor(
                "weave_strength",
                "覆纹环合强度区",
                strength_multiplier,
                f"原伤害来源角色环合强度 {ring_strength:g}",
                "1 + 20% × 环合强度 / (环合强度 + 180)",
            ),
            _factor(
                "weave_followup",
                "覆纹追加倍率",
                followup_multiplier,
                (
                    "灵可突破被动「弱点感应」已解锁"
                    if lingke_passive
                    else "基础覆纹规则"
                ),
                f"(1 + {base_extra_ratio:.0%}) × 覆纹环合强度区 - 1",
            ),
            _factor(
                "lingke_damage_up",
                "覆纹限定增伤补正",
                passive_damage_zone,
                (
                    "弱点感应：覆纹追加攻击通伤 +10%，与原通伤相加"
                    if lingke_passive
                    else "队伍未启用弱点感应"
                ),
                (
                    "(原增伤区 + 10%) / 原增伤区"
                    if lingke_passive
                    else "固定为 1"
                ),
            ),
        )
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=predicted,
            critical_damage=None,
            selected_damage=predicted,
            selected_error_percent=absolute_error,
            critical_state="not_applicable",
            confidence=confidence,
            factors=factors,
            missing_evidence=missing,
            formula_type=formula_label,
            critical_rate=0.0,
            expected_damage=predicted,
            corrected_expected_damage=(hit.damage if predicted > 0.0 else None),
            signed_error_percent=signed_error,
            critical_policy="disabled",
        )

    @staticmethod
    def _reaction_attribute(
        hit: BattleAnalysisHit,
        analysis: BattleAnalysisSnapshot,
        evidence_attribute: str = "",
    ) -> str:
        evidence_value = str(evidence_attribute).casefold()
        if evidence_value in _ELEMENT_PENETRATION_PROPERTIES:
            return evidence_value
        attribute = str(hit.damage_attribute).casefold()
        if attribute in _ELEMENT_PENETRATION_PROPERTIES:
            return attribute
        counts: dict[str, int] = {}
        for row in analysis.hits:
            candidate = str(row.damage_attribute).casefold()
            if (
                row.character_id == hit.character_id
                and row.direction == "outgoing"
                and candidate in _ELEMENT_PENETRATION_PROPERTIES
            ):
                counts[candidate] = counts.get(candidate, 0) + 1
        if counts:
            return max(counts, key=lambda key: (counts[key], key))
        return attribute if attribute else "normal"

    @staticmethod
    def _mitigation(
        *,
        hit: BattleAnalysisHit,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        analysis: BattleAnalysisSnapshot,
        evidence_attribute: str = "",
    ) -> tuple[
        str,
        float,
        float,
        float,
        str,
        tuple[BattleHitReplayTerm, ...],
    ]:
        condition = analysis.target_condition
        assert condition is not None
        attribute = BattleSpecialHitReplayService._reaction_attribute(
            hit,
            analysis,
            evidence_attribute,
        )
        defense_penetration = max(0.0, float(values.get("DefIgnore", 0.0)))
        if condition.enemy_defense_base is None:
            scene = (
                DamageScene.OPEN_WORLD
                if condition.scene == "open_world"
                else DamageScene.OUTER_REALM
            )
            enemy_defense = calculate_enemy_defense(
                condition.enemy_level,
                defense_penetration,
                condition.defense_reduction,
                scene,
            )
            defense_basis = "用户场景与显示等级近似"
        else:
            enemy_defense = calculate_enemy_defense_from_profile(
                EnemyDefenseProfileInput(
                    defense_base=condition.enemy_defense_base,
                    defense_up=condition.enemy_defense_up,
                    defense_add=condition.enemy_defense_add,
                ),
                defense_penetration,
                condition.defense_reduction,
            )
            defense_basis = "目标属性包 DefBase/6"
        baseline = next(
            (
                row
                for row in analysis.baselines
                if row.character_id == hit.character_id
            ),
            None,
        )
        character_level = 80.0 if baseline is None else baseline.character_level
        defense = calculate_defense_multiplier(character_level, enemy_defense)
        defense_terms = (
            ()
            if condition.enemy_defense_base is None
            else (
                literal_replay_term(
                    "character:level", "CharacterLevel", "角色等级",
                    character_level, "character", "人物",
                    is_percent=False, basis="冻结角色等级",
                ),
                literal_replay_term(
                    "target:DefBase", "DefBase", "DefBase",
                    condition.enemy_defense_base, "target", "敌方",
                    is_percent=False, basis=defense_basis,
                ),
                literal_replay_term(
                    "target:DefUp", "DefUp", "防御提升",
                    condition.enemy_defense_up, "target", "敌方",
                    is_percent=True, basis=defense_basis,
                ),
                literal_replay_term(
                    "target:DefAdd", "DefAdd", "额外防御",
                    condition.enemy_defense_add, "target", "敌方",
                    is_percent=False, basis=defense_basis,
                ),
                literal_replay_term(
                    "attacker:DefIgnore", "DefIgnore", "防御穿透",
                    defense_penetration, "resolved", "攻击者",
                    is_percent=True, basis="命中时角色属性",
                ),
                literal_replay_term(
                    "target:DefReduction", "DefReduction", "防御降低",
                    condition.defense_reduction, "target", "敌方",
                    is_percent=True, basis=defense_basis,
                ),
            )
        )
        base_resistance = dict(condition.resistances).get(attribute, 0.20)
        resistance_properties = _ELEMENT_RESISTANCE_PROPERTIES.get(attribute, ())
        dynamic_resistance = sum(
            modifier.additive_value
            for modifier in projection.modifiers
            if modifier.target_scope == "target"
            and modifier.property_id in resistance_properties
        )
        penetration_property = _ELEMENT_PENETRATION_PROPERTIES.get(attribute)
        penetration = (
            0.0
            if penetration_property is None
            else float(values.get(penetration_property, 0.0))
        )
        resistance = calculate_resistance_multiplier(
            base_resistance + dynamic_resistance - penetration
        )
        vulnerability = 1.0 + condition.vulnerability
        return (
            attribute,
            defense,
            resistance,
            vulnerability,
            defense_basis,
            defense_terms,
        )

    @classmethod
    def _replay_standard_reaction(
        cls,
        *,
        channel_id: str,
        hit: BattleAnalysisHit,
        evidence: BattleSkillDamageEvidence,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        analysis: BattleAnalysisSnapshot,
        formula_label: str,
    ) -> BattleHitReplayResult:
        ordinary_scorch = (
            channel_id == "reaction_scorch"
            and evidence.damage_id.casefold() == _ORDINARY_SCORCH_DAMAGE_ID
        )
        zankou_scorch = (
            channel_id == "reaction_scorch"
            and evidence.damage_id.casefold() == _ZANKOU_SCORCH_DAMAGE_ID
        )
        if zankou_scorch and evidence.state_multiplier <= 0.0:
            return BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
                missing_evidence=(
                    "缺少残虹被动逐层持续伤害施加事件及触发时点的浊燃伤害、"
                    "元素与持续时间快照；周期结算 hit 不能替代施加事件",
                ),
                formula_type=formula_label,
                formula_damage_attribute=hit.damage_attribute,
            )
        if ordinary_scorch and (
            hit.damage_attribute.casefold() not in _ELEMENT_PENETRATION_PROPERTIES
        ):
            return BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
                missing_evidence=(
                    "普通浊燃正式伤害项未固定元素属性，本击也未提供可确认的伤害属性；"
                    "不能猜测目标抗性与角色穿透",
                ),
                formula_type=formula_label,
                formula_damage_attribute="",
            )
        level_multiplier = evidence.level_multiplier
        if level_multiplier is None:
            return BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
                missing_evidence=(f"缺少{formula_label}的官方 16 档等级基础值",),
                formula_type=formula_label,
            )
        ring_strength = max(0.0, float(values.get("MagBase", 0.0)))
        ring_multiplier = calculate_ring_strength_multiplier(ring_strength)
        (
            attribute,
            defense,
            resistance,
            vulnerability,
            defense_basis,
            defense_terms,
        ) = cls._mitigation(
            hit=hit,
            projection=projection,
            values=values,
            analysis=analysis,
            evidence_attribute=evidence.damage_attribute,
        )
        stack_multiplier = (
            evidence.state_multiplier
            if channel_id == "reaction_scorch"
            else 1.0
        )
        dot_final_multiplier = (
            max(1.0, evidence.dot_final_multiplier)
            if channel_id == "reaction_scorch"
            else 1.0
        )
        raw_non_critical = (
            level_multiplier
            * stack_multiplier
            * ring_multiplier
            * defense
            * resistance
            * vulnerability
            * dot_final_multiplier
        )
        non_critical = ceil_replay_damage(raw_non_critical)
        crit_damage = max(0.0, float(values.get("CritDamageBase", 0.50)))
        if channel_id == "reaction_scorch":
            critical = ceil_replay_damage(raw_non_critical * (1.0 + crit_damage))
            noncrit_error = abs(_signed_error(hit.damage, non_critical) or 0.0)
            crit_error = abs(_signed_error(hit.damage, critical) or 0.0)
            is_critical = crit_error < noncrit_error
            selected = critical if is_critical else non_critical
            critical_state = "critical" if is_critical else "non_critical"
            critical_rate = 0.50
            expected = non_critical * (1.0 - critical_rate) + critical * critical_rate
        else:
            critical = None
            selected = non_critical
            critical_state = "not_applicable"
            critical_rate = 0.0
            expected = non_critical
        signed_error = _signed_error(hit.damage, selected)
        absolute_error = None if signed_error is None else abs(signed_error)
        confidence = (
            "高" if absolute_error is not None and absolute_error <= 2.0
            else "中" if absolute_error is not None and absolute_error <= 5.0
            else "低"
        )
        unresolved = sum(row.status == "unresolved" for row in projection.decisions)
        missing = []
        if unresolved:
            missing.append(f"{unresolved} 个{formula_label}相关 Buff 仍待结构化")
        if zankou_scorch and evidence.state_multiplier_label:
            missing.append(
                f"浊燃层数由逐击正向重放（置信度{evidence.state_confidence}），"
                "待运行时目标 Buff 层数覆盖"
            )
        condition = analysis.target_condition
        assert condition is not None
        stack_factors = (
            ()
            if channel_id != "reaction_scorch" or not evidence.state_multiplier_label
            else (_factor(
                "state_coefficient",
                evidence.state_multiplier_label,
                stack_multiplier,
                evidence.state_multiplier_basis,
                (
                    "min(结算前残虹浊燃层数, 3) × 单层伤害"
                    if zankou_scorch else "普通浊燃固定 1 层 × 单层伤害"
                ),
            ),)
        )
        factors = (
            _factor(
                "skill",
                "等级基础值",
                level_multiplier,
                evidence.evidence_basis,
                "官方环合伤害曲线按角色等级取档",
            ),
            *stack_factors,
            _factor(
                "scaling",
                "环合强度区",
                ring_multiplier,
                f"命中归属角色环合强度 {ring_strength:g}",
                "1 + 环合强度 / 600",
            ),
            _factor(
                "defense",
                "防御区",
                defense,
                defense_basis,
                "角色等级与敌方防御",
                terms=defense_terms,
            ),
            _factor(
                "resistance",
                "抗性区",
                resistance,
                f"{attribute} 属性抗性与穿透",
                "抗性分段函数(目标抗性 - 属性穿透)",
            ),
            _factor(
                "vulnerability",
                "易伤区",
                vulnerability,
                "敌方受到伤害提升",
                "1 + 易伤",
            ),
            *(
                dot_final_replay_factors(evidence)
                if channel_id == "reaction_scorch" else ()
            ),
            _factor(
                "critical",
                "暴击伤害倍率",
                1.0 + crit_damage if channel_id == "reaction_scorch" else 1.0,
                "浊燃固定 50% 暴击率" if channel_id == "reaction_scorch" else "创生不暴击",
                "1 + 暴击伤害" if channel_id == "reaction_scorch" else "固定为 1",
            ),
        )
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=non_critical,
            critical_damage=critical,
            selected_damage=selected,
            selected_error_percent=absolute_error,
            critical_state=critical_state,
            confidence=confidence,
            factors=factors,
            missing_evidence=tuple(missing),
            formula_type=formula_label,
            critical_rate=critical_rate,
            expected_damage=expected,
            corrected_expected_damage=(
                expected * hit.damage / selected if selected > 0.0 else None
            ),
            signed_error_percent=signed_error,
            critical_policy=(
                "fixed" if channel_id == "reaction_scorch" else "disabled"
            ),
            formula_damage_attribute=attribute,
        )

    @staticmethod
    def _replay_dark_star(
        *,
        hit: BattleAnalysisHit,
        evidence: BattleSkillDamageEvidence,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        analysis: BattleAnalysisSnapshot,
        formula_label: str,
    ) -> BattleHitReplayResult:
        condition = analysis.target_condition
        assert condition is not None
        level_multiplier = evidence.level_multiplier
        if level_multiplier is None:
            return BattleHitReplayResult(
                event_id=hit.event_id,
                observed_damage=hit.damage,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
                missing_evidence=("缺少黯星的官方 16 档等级基础值",),
                formula_type=formula_label,
            )
        ring_strength = max(0.0, float(values.get("MagBase", 0.0)))
        ring_multiplier = calculate_ring_strength_multiplier(ring_strength)
        attribute = "psychically"
        base_resistance = dict(condition.resistances).get(attribute, 0.20)
        target_resistance = sum(
            row.additive_value
            for row in projection.modifiers
            if row.target_scope == "target"
            and row.property_id in {
                "DamageResistPsychicallyBase",
                "DamageResistPsychicallyAdd",
            }
        )
        penetration = float(values.get("DamagePenetratePsychically", 0.0))
        resistance = calculate_resistance_multiplier(
            base_resistance + target_resistance - penetration
        )
        vulnerability = 1.0 + condition.vulnerability
        predicted = ceil_replay_damage(
            level_multiplier * ring_multiplier * resistance * vulnerability
        )
        signed_error = _signed_error(hit.damage, predicted)
        absolute_error = None if signed_error is None else abs(signed_error)
        confidence = (
            "高" if absolute_error is not None and absolute_error <= 2.0
            else "中" if absolute_error is not None and absolute_error <= 5.0
            else "低"
        )
        unresolved = sum(
            row.status == "unresolved" for row in projection.decisions
        )
        missing = (
            ()
            if unresolved == 0
            else (f"{unresolved} 个黯星相关 Buff 仍待结构化",)
        )
        factors = (
            _factor(
                "skill",
                "等级基础值",
                level_multiplier,
                evidence.evidence_basis,
                "官方环合伤害曲线按角色等级取档",
            ),
            _factor(
                "scaling",
                "环合强度区",
                ring_multiplier,
                f"命中归属角色环合强度 {ring_strength:g}",
                "1 + 环合强度 / 600",
            ),
            _factor("damage_up", "增伤区", 1.0, "黯星不读取角色通伤", "固定为 1"),
            _factor("defense", "防御区", 1.0, "黯星为心灵伤害", "固定为 1"),
            _factor(
                "resistance",
                "抗性区",
                resistance,
                "用户确认心灵抗性与命中时减抗/穿透",
                "抗性分段函数(心灵抗性 - 心灵穿透)",
            ),
            _factor(
                "vulnerability",
                "易伤区",
                vulnerability,
                "用户确认目标条件与敌方 Buff",
                "1 + 敌方受到伤害提升",
            ),
            _factor("independent", "独立最终乘区", 1.0, "当前无黯星独立修正", "固定为 1"),
        )
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=predicted,
            critical_damage=None,
            selected_damage=predicted,
            selected_error_percent=absolute_error,
            critical_state="not_applicable",
            confidence=confidence,
            factors=factors,
            missing_evidence=missing,
            formula_type=formula_label,
            critical_rate=0.0,
            expected_damage=predicted,
            corrected_expected_damage=(hit.damage if predicted > 0.0 else None),
            signed_error_percent=signed_error,
            critical_policy="disabled",
        )
