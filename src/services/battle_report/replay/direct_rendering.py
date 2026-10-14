# 将直伤数值结果还原为逐击解释、候选暴击和证据状态。
from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.domain.battle_report.build.inputs import BattleCharacterBaseline
from src.domain.battle_report.buffs.models import BattleHitBuffProjection
from src.domain.battle_report.replay.models import BattleHitReplayResult, BattleSkillDamageEvidence
from src.services.battle_report.replay.support import (
    dot_final_replay_factors,
    literal_replay_term,
    replay_factor as _factor,
    replay_error_percent,
    replay_signed_error_percent,
    replay_source_terms as _source_terms,
    replay_target_profile_basis,
    skill_final_replay_terms,
)
from src.services.battle_report.replay.formula_catalog import (
    ELEMENT_DAMAGE_PROPERTIES as _ELEMENT_DAMAGE_PROPERTIES,
    ELEMENT_PENETRATION_PROPERTIES as _ELEMENT_PENETRATION_PROPERTIES,
    ELEMENT_RESISTANCE_PROPERTIES as _ELEMENT_RESISTANCE_PROPERTIES,
)
from src.services.battle_report.targets.inferred_condition import INFERRED_ENCOUNTER_SOURCE_KIND
from src.services.battle_report.replay.direct_numeric import prepare_direct_formula_input, calculate_python_direct_formula


class BattleDirectHitReplayMixin:
    @classmethod
    def _replay_direct(
        cls,
        *,
        hit,
        evidence: BattleSkillDamageEvidence,
        baseline: BattleCharacterBaseline,
        projection: BattleHitBuffProjection,
        values: Mapping[str, float],
        character_level: float,
        analysis: BattleAnalysisSnapshot,
        applied_intervals: tuple[str, ...],
        excluded_intervals: tuple[str, ...],
        formula_label: str = "直伤",
        numeric: Mapping[str, Any] | None = None,
    ) -> BattleHitReplayResult:
        condition = analysis.target_condition
        assert condition is not None
        inferred_target = condition.source_kind == INFERRED_ENCOUNTER_SOURCE_KIND
        resolved_target, target_profile_basis = replay_target_profile_basis(analysis, inferred_target)
        if evidence.state_multiplier_label and evidence.state_multiplier <= 0.0:
            return cls._unreplayable(
                hit.event_id,
                hit.damage,
                evidence.state_multiplier_basis,
                formula_label,
            )
        attribute = evidence.damage_attribute.casefold()
        if attribute == "true":
            return cls._unreplayable(
                hit.event_id,
                hit.damage,
                (
                    "静态 TRUE 没有独立属性抗性；该伤害必须由专用 channel "
                    "适配器还原为角色固有属性后再计算防御与对应抗性。"
                ),
                formula_label,
            )
        if numeric is None:
            numeric = calculate_python_direct_formula(
                prepare_direct_formula_input(
                    evidence=evidence,
                    values=values,
                    character_level=character_level,
                    analysis=analysis,
                    projection=projection,
                )
            )
        accepted_unknown = (
            evidence.critical_policy == "unknown"
            and numeric["status"] == "partial"
            and numeric["gap_codes"] == ["critical_policy_unknown"]
        )
        if numeric["status"] != "complete" and not accepted_unknown:
            raise ValueError("Native direct formula rejected supported frozen input")
        numeric_factors = numeric["factors"]
        scaling_value = numeric_factors["scaling_value"]
        multiplier = numeric_factors["multiplier"]
        damage_increase = numeric_factors["damage_increase"]
        defense = numeric_factors["defense"]
        resistance_factor = numeric_factors["resistance_factor"]
        vulnerability = numeric_factors["vulnerability"]
        independent = numeric_factors["independent"]
        non_critical = numeric["non_critical_damage"]
        critical = numeric["critical_damage"]
        critical_rate = numeric["critical_rate"]
        element_property = _ELEMENT_DAMAGE_PROPERTIES.get(attribute)
        penetration = values.get("DefIgnore", 0.0)
        defense_basis = (
            "用户场景与显示等级近似" if condition.enemy_defense_base is None else f"{target_profile_basis} DefBase/6"
        )
        base_resistance = dict(condition.resistances).get(attribute, 0.20)
        resistance_property_ids = _ELEMENT_RESISTANCE_PROPERTIES.get(attribute, ())
        penetration_property = _ELEMENT_PENETRATION_PROPERTIES.get(attribute)
        crit_damage_bonus = max(0.0, values.get("CritDamageBase", 0.50))
        stack_coefficient = max(1.0, evidence.state_multiplier)
        critical_disabled = evidence.critical_policy == "disabled"
        critical_unknown = evidence.critical_policy == "unknown"
        scaling_terms = _source_terms(
            baseline,
            projection,
            {
                "Atk": ("AtkBase", "AtkUp", "AtkAdd"),
                "HPMax": ("HPMaxBase", "HPMaxUp", "HPMaxAdd"),
                "Def": ("DefBase", "DefUp", "DefAdd"),
            }.get(evidence.scaling_property_id, (evidence.scaling_property_id,)),
        )
        damage_terms = _source_terms(
            baseline,
            projection,
            tuple(property_id for property_id in ("DamageUpGeneralBase", element_property) if property_id),
        )
        critical_terms = _source_terms(
            baseline,
            projection,
            ("CritDamageBase",),
        )
        defense_terms = (
            literal_replay_term(
                "character:level",
                "CharacterLevel",
                "角色等级",
                character_level,
                "character",
                "人物",
                is_percent=False,
                basis="冻结角色等级",
            ),
            literal_replay_term(
                "target:DefBase",
                "DefBase",
                "DefBase",
                condition.enemy_defense_base or condition.enemy_level,
                "target",
                "敌方",
                is_percent=False,
                basis=defense_basis,
            ),
            literal_replay_term(
                "target:DefUp",
                "DefUp",
                "防御提升",
                condition.enemy_defense_up,
                "target",
                "敌方",
                is_percent=True,
                basis=target_profile_basis,
            ),
            literal_replay_term(
                "target:DefAdd",
                "DefAdd",
                "额外防御",
                condition.enemy_defense_add,
                "target",
                "敌方",
                is_percent=False,
                basis=target_profile_basis,
            ),
            literal_replay_term(
                "attacker:DefIgnore",
                "DefIgnore",
                "防御穿透",
                penetration,
                "resolved",
                "攻击者",
                is_percent=True,
                basis="命中时角色属性",
            ),
            literal_replay_term(
                "target:DefReduction",
                "DefReduction",
                "防御降低",
                condition.defense_reduction,
                "target",
                "敌方",
                is_percent=True,
                basis=target_profile_basis,
            ),
        )
        resistance_terms = (
            literal_replay_term(
                "target:resistance",
                f"Resistance:{attribute}",
                "属性抗性",
                base_resistance,
                "target",
                "敌方",
                is_percent=True,
                basis=target_profile_basis,
            ),
            *_source_terms(
                baseline,
                projection,
                (penetration_property,) if penetration_property else (),
            ),
            *_source_terms(
                baseline,
                projection,
                resistance_property_ids,
            ),
        )
        vulnerability_terms = (
            literal_replay_term(
                "target:vulnerability",
                "Vulnerability",
                "受到伤害提升",
                condition.vulnerability,
                "target",
                "敌方",
                is_percent=True,
                basis=f"{target_profile_basis}与敌方 Buff",
            ),
        )
        independent_ids = tuple(
            property_id
            for property_id in values
            if "finaldamage" in property_id.casefold() or "damageupfinal" in property_id.casefold()
        )
        independent_terms = (
            *_source_terms(baseline, projection, independent_ids),
            *skill_final_replay_terms(evidence),
        )
        stack_factors = (
            ()
            if not evidence.state_multiplier_label
            else (
                _factor(
                    "state_coefficient",
                    evidence.state_multiplier_label,
                    stack_coefficient,
                    evidence.state_multiplier_basis,
                    formula="min(当前同类状态层数, 10) × 每层系数 1",
                ),
            )
        )
        factors = (
            _factor(
                "skill",
                "倍率区",
                multiplier,
                evidence.evidence_basis,
                formula="静态基础倍率 × 倍率修正",
            ),
            *stack_factors,
            _factor(
                "scaling",
                f"{evidence.scaling_property_id} 乘区",
                scaling_value,
                "战报冻结配装与逐击 Buff 投影",
                formula="基础值 × (1 + 百分比提升) + 额外固定值",
                terms=scaling_terms,
            ),
            _factor(
                "damage_up",
                "增伤区",
                damage_increase,
                "角色面板与命中时 Buff",
                formula="1 + 通用增伤 + 属性增伤",
                terms=damage_terms,
            ),
            _factor(
                "defense",
                "防御区",
                defense,
                defense_basis,
                formula=(
                    "L / ([DefBase × (1 + DefUp) + DefAdd] / 6 × (1 - 防御穿透) × (1 - 防御降低) + L)，L=角色等级+100"
                ),
                terms=defense_terms,
            ),
            _factor(
                "resistance",
                "抗性区",
                resistance_factor,
                target_profile_basis,
                formula="抗性分段函数(目标抗性 - 属性穿透)",
                terms=resistance_terms,
            ),
            _factor(
                "vulnerability",
                "易伤区",
                vulnerability,
                f"{target_profile_basis}；敌方受到伤害提升默认 0",
                formula="1 + 敌方受到伤害提升",
                terms=vulnerability_terms,
            ),
            _factor(
                "independent",
                "独立最终乘区",
                independent,
                (
                    "命中时结构化最终伤害 Buff 与逐击限定觉醒"
                    if evidence.skill_final_multiplier_basis
                    else "命中时结构化最终伤害 Buff"
                ),
                formula="各最终伤害提升与技能限定最终倍率独立相乘",
                terms=independent_terms,
            ),
            *dot_final_replay_factors(evidence),
            _factor(
                "critical",
                "暴击伤害倍率",
                1.0 if critical_disabled else 1.0 + crit_damage_bonus,
                (
                    "正式伤害语义固定不可暴击"
                    if critical_disabled
                    else "暴击能力未确认；仅并列展示候选"
                    if critical_unknown
                    else "命中时角色暴击伤害"
                ),
                formula="固定为 1" if critical_disabled else "1 + 暴击伤害",
                terms=() if critical_disabled else critical_terms,
            ),
        )
        noncrit_error = replay_error_percent(hit.damage, non_critical)
        crit_error = None if critical is None else replay_error_percent(hit.damage, critical)
        best_is_crit = bool(crit_error is not None and crit_error < noncrit_error)
        selected = critical if best_is_crit and critical is not None else non_critical
        error = noncrit_error if crit_error is None else min(noncrit_error, crit_error)
        signed_error = replay_signed_error_percent(hit.damage, selected)
        expected = numeric["expected_damage"]
        corrected_expected = expected * hit.damage / selected if expected is not None and selected > 0.0 else None
        separation = 0.0 if crit_error is None else abs(noncrit_error - crit_error)
        if critical_disabled:
            state = "not_applicable"
            confidence = "高" if error <= 2.0 else "中" if error <= 5.0 else "低"
        elif error <= 2.0 and separation >= 2.0:
            state = "critical" if best_is_crit else "non_critical"
            confidence = "高"
        elif error <= 5.0 and separation >= 1.0:
            state = "critical" if best_is_crit else "non_critical"
            confidence = "中"
        else:
            state = "ambiguous"
            confidence = "低"
        missing = []
        if excluded_intervals:
            missing.append(f"{len(excluded_intervals)} 个 Buff 区间未进入数值")
        if not applied_intervals:
            missing.append("当前击未匹配到动态 Buff 区间")
        if inferred_target and not resolved_target:
            missing.append("目标实例仍未识别；防御与抗性来自本场最大生命指纹唯一命中、且候选敌人共享同一乘区的静态配置")
        if evidence.state_multiplier_label:
            missing.append(f"当前结算层数由逐击正向重放（置信度{evidence.state_confidence}），待运行时层数证据覆盖")
            if evidence.state_confidence == "低":
                confidence = "低"
                if not critical_disabled:
                    state = "ambiguous"
                missing.append(evidence.state_multiplier_basis)
        if critical_unknown:
            missing.append("该伤害是否允许暴击尚未确认；期望伤害暂不计算")
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=non_critical,
            critical_damage=critical,
            selected_damage=selected,
            selected_error_percent=error,
            critical_state=state,
            confidence=confidence,
            factors=factors,
            missing_evidence=tuple(missing),
            formula_type=formula_label,
            critical_rate=critical_rate,
            expected_damage=expected,
            corrected_expected_damage=corrected_expected,
            signed_error_percent=signed_error,
            critical_policy=evidence.critical_policy,
        )

    @staticmethod
    def _unreplayable(
        event_id: str,
        observed_damage: float,
        reason: str,
        formula_type: str = "未分类",
    ) -> BattleHitReplayResult:
        return BattleHitReplayResult(
            event_id=event_id,
            observed_damage=observed_damage,
            non_critical_damage=None,
            critical_damage=None,
            selected_damage=None,
            selected_error_percent=None,
            critical_state="unreplayable",
            confidence="未解析",
            factors=(),
            missing_evidence=(reason,),
            formula_type=formula_type,
        )
