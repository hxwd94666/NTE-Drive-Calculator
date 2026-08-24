# 以真实逐击为锚点，在逐击动态 Buff 面板上计算属性边际。
"""Battle-report marginal calculations with safe per-hit Buff projection."""

from __future__ import annotations

from collections.abc import Mapping

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleMarginalResult,
    BattleTargetCondition,
)
from src.domain.official_role import ROLE_PANEL_MARGINAL_UNITS
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
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


_ELEMENT_PROPERTIES = {
    "DamageUpChaosBase",
    "DamageUpCosmosBase",
    "DamageUpIncantationBase",
    "DamageUpLakshanaBase",
    "DamageUpNatureBase",
    "DamageUpPsycheBase",
    "DamageUpPsychicallyBase",
}
_ATTRIBUTE_ELEMENT_PROPERTY = {
    "chaos": "DamageUpChaosBase",
    "cosmos": "DamageUpCosmosBase",
    "incantation": "DamageUpIncantationBase",
    "lakshana": "DamageUpLakshanaBase",
    "nature": "DamageUpNatureBase",
    "psyche": "DamageUpPsycheBase",
    "psychically": "DamageUpPsychicallyBase",
}
_MARGINAL_LABELS = {
    "CritBase": "暴击率",
    "CritDamageBase": "暴击伤害",
    "DamageUpGeneralBase": "通用伤害增强",
    "AtkUp": "攻击力提升",
    "AtkAdd": "固定攻击力",
    "HPMaxUp": "生命值提升",
    "HPMaxAdd": "固定生命值",
    "DefUp": "防御力提升",
    "DefAdd": "固定防御力",
    "DefIgnore": "防御忽略",
    "ElementDamage": "属性伤害增强",
    "MagBase": "环合强度",
}
_MARGINAL_UNITS = {
    **ROLE_PANEL_MARGINAL_UNITS,
    "DefIgnore": 0.01,
    "MagBase": 10.0,
}
_DAMAGE_PENETRATION_PROPERTY = {
    "chaos": "DamagePenetrateChaos",
    "cosmos": "DamagePenetrateCosmos",
    "incantation": "DamagePenetrateIncantation",
    "lakshana": "DamagePenetrateLakshana",
    "nature": "DamagePenetrateNature",
    "psyche": "DamagePenetratePsyche",
    "psychically": "DamagePenetratePsychically",
}


class BattleMarginalCalculationService:
    """Calculate role margins without mistaking inferred Buffs for raw facts."""

    @staticmethod
    def default_units(baseline: BattleCharacterBaseline) -> dict[str, float]:
        present = {row.property_id for row in baseline.stats}
        result = {
            property_id: float(unit)
            for property_id, unit in _MARGINAL_UNITS.items()
            if property_id in present
        }
        element = next((item for item in present if item in _ELEMENT_PROPERTIES), None)
        if element is not None:
            result[element] = float(_MARGINAL_UNITS["ElementDamage"])
        for property_id in _DAMAGE_PENETRATION_PROPERTY.values():
            if property_id in present:
                result[property_id] = 0.01
        return result

    @classmethod
    def calculate(
        cls,
        *,
        analysis: BattleAnalysisSnapshot,
        character_id: int,
        edited_values: Mapping[str, float],
        units: Mapping[str, float],
    ) -> tuple[BattleMarginalResult, ...]:
        baseline = next(
            (row for row in analysis.baselines if row.character_id == character_id),
            None,
        )
        if baseline is None:
            return ()
        frozen = {row.property_id: row.value for row in baseline.stats}
        edited = {
            **frozen,
            **{str(key): float(value) for key, value in edited_values.items()},
        }
        role_hits = tuple(
            hit
            for hit in analysis.hits
            if hit.direction == "outgoing" and hit.character_id == character_id
        )
        projections = {
            hit.event_id: BattleBuffAttributeProjectionService.project_hit(
                hit,
                analysis.buff_intervals,
            )
            for hit in role_hits
        }
        applied_intervals = {
            interval_id
            for projection in projections.values()
            for interval_id in projection.applied_interval_ids
        }
        excluded_intervals = {
            interval_id
            for projection in projections.values()
            for interval_id in projection.excluded_interval_ids
        }
        derived_damage = next(
            (
                role.max_hp_reduction_damage
                for role in analysis.roles
                if role.character_id == character_id
            ),
            0.0,
        )
        role_damage = sum(hit.damage for hit in role_hits) + derived_damage
        results = []
        for property_id, raw_unit in units.items():
            unit = float(raw_unit)
            changed = dict(edited)
            changed[property_id] = changed.get(property_id, 0.0) + unit
            supported_hits = tuple(
                hit
                for hit in role_hits
                if cls._supports(
                    property_id,
                    hit,
                    target_condition=analysis.target_condition,
                )
            )
            supported_damage = sum(hit.damage for hit in supported_hits)
            formula_hits = tuple(
                hit
                for hit in role_hits
                if hit.classification in {"direct", "direct_follow_up", "weave"}
            )
            formula_damage = sum(hit.damage for hit in formula_hits)
            edited_formula = sum(
                hit.damage * cls._factor_ratio(
                    frozen,
                    edited,
                    hit,
                    projections[hit.event_id],
                    baseline,
                    analysis.target_condition,
                )
                for hit in formula_hits
            )
            changed_formula = sum(
                hit.damage * cls._factor_ratio(
                    frozen,
                    changed,
                    hit,
                    projections[hit.event_id],
                    baseline,
                    analysis.target_condition,
                )
                for hit in formula_hits
            )
            baseline_damage = role_damage - formula_damage + edited_formula
            predicted_damage = role_damage - formula_damage + changed_formula
            increment = predicted_damage - baseline_damage
            role_gain = increment / baseline_damage * 100.0 if baseline_damage else 0.0
            team_gain = (
                increment / analysis.effective_damage * 100.0
                if analysis.effective_damage
                else 0.0
            )
            percent = property_id not in {"AtkAdd", "HPMaxAdd", "DefAdd", "MagBase"}
            results.append(BattleMarginalResult(
                property_id=property_id,
                label=cls._label(property_id, baseline),
                unit=unit,
                is_percent=percent,
                baseline_damage=baseline_damage,
                predicted_damage=predicted_damage,
                role_gain_percent=role_gain,
                team_dps_gain_percent=team_gain,
                supported_damage=supported_damage,
                unsupported_damage=max(0.0, role_damage - supported_damage),
                coverage_percent=(
                    supported_damage / role_damage * 100.0 if role_damage else 0.0
                ),
                assumption=cls._assumption(
                    property_id,
                    supported_damage > 0,
                    applied_count=len(applied_intervals),
                    excluded_count=len(excluded_intervals),
                ),
            ))
        return tuple(sorted(
            results,
            key=lambda row: row.role_gain_percent,
            reverse=True,
        ))

    @staticmethod
    def _supports(
        property_id: str,
        hit: BattleAnalysisHit,
        *,
        target_condition: BattleTargetCondition | None,
    ) -> bool:
        if property_id == "MagBase":
            return hit.classification == "weave"
        if property_id == "DefIgnore":
            return (
                target_condition is not None
                and hit.classification in {"direct", "direct_follow_up", "weave"}
            )
        if property_id in _DAMAGE_PENETRATION_PROPERTY.values():
            expected_attribute = next(
                damage_type
                for damage_type, candidate in _DAMAGE_PENETRATION_PROPERTY.items()
                if candidate == property_id
            )
            return (
                target_condition is not None
                and hit.damage_attribute.casefold() == expected_attribute
                and hit.classification in {"direct", "direct_follow_up", "weave"}
            )
        if property_id in _ELEMENT_PROPERTIES:
            return (
                _ATTRIBUTE_ELEMENT_PROPERTY.get(hit.damage_attribute.casefold())
                == property_id
                and hit.classification in {"direct", "direct_follow_up", "weave"}
            )
        if property_id in {"HPMaxUp", "HPMaxAdd", "DefUp", "DefAdd"}:
            return False
        return hit.classification in {"direct", "direct_follow_up", "weave"}

    @staticmethod
    def _direct_factor(
        values: Mapping[str, float],
        damage_attribute: str,
    ) -> float:
        attack = calculate_attribute_value(
            values.get("AtkBase", 0.0),
            values.get("AtkUp", 0.0),
            values.get("AtkAdd", 0.0),
        )
        critical = calculate_critical_multiplier(
            min(1.0, max(0.0, values.get("CritBase", 0.05))),
            max(0.0, values.get("CritDamageBase", 0.50)),
        )
        element_property = _ATTRIBUTE_ELEMENT_PROPERTY.get(
            damage_attribute.casefold()
        )
        element = values.get(element_property, 0.0) if element_property else 0.0
        increase = 1.0 + values.get("DamageUpGeneralBase", 0.0) + element
        return max(0.0, attack) * max(0.0, critical) * max(0.0, increase)

    @classmethod
    def _factor_ratio(
        cls,
        frozen: Mapping[str, float],
        current: Mapping[str, float],
        hit: BattleAnalysisHit,
        projection: BattleHitBuffProjection,
        baseline: BattleCharacterBaseline,
        target_condition: BattleTargetCondition | None,
    ) -> float:
        frozen_with_buff = BattleBuffAttributeProjectionService.apply_additive(
            frozen,
            projection,
        )
        current_with_buff = BattleBuffAttributeProjectionService.apply_additive(
            current,
            projection,
        )
        base_direct = cls._direct_factor(
            frozen_with_buff,
            hit.damage_attribute,
        )
        current_direct = cls._direct_factor(
            current_with_buff,
            hit.damage_attribute,
        )
        base_enemy = cls._enemy_factor(
            frozen_with_buff,
            baseline,
            target_condition,
            hit,
            projection,
        )
        current_enemy = cls._enemy_factor(
            current_with_buff,
            baseline,
            target_condition,
            hit,
            projection,
        )
        base_factor = base_direct * base_enemy
        current_factor = current_direct * current_enemy
        direct_ratio = current_factor / base_factor if base_factor > 0 else 1.0
        if hit.classification != "weave":
            return direct_ratio
        base_weave = calculate_weave_followup_damage(
            1.0,
            max(0.0, frozen_with_buff.get("MagBase", 0.0)),
        )
        current_weave = calculate_weave_followup_damage(
            1.0,
            max(0.0, current_with_buff.get("MagBase", 0.0)),
        )
        return direct_ratio * (
            current_weave / base_weave if base_weave > 0 else 1.0
        )

    @staticmethod
    def _enemy_factor(
        values: Mapping[str, float],
        baseline: BattleCharacterBaseline,
        condition: BattleTargetCondition | None,
        hit: BattleAnalysisHit,
        projection: BattleHitBuffProjection,
    ) -> float:
        if condition is None:
            return 1.0
        scene = (
            DamageScene.OPEN_WORLD
            if condition.scene == "open_world"
            else DamageScene.OUTER_REALM
        )
        defense_penetration = min(1.0, max(-1.0, values.get("DefIgnore", 0.0)))
        if condition.enemy_defense_base is not None:
            enemy_defense = calculate_enemy_defense_from_profile(
                EnemyDefenseProfileInput(
                    defense_base=condition.enemy_defense_base,
                    defense_up=condition.enemy_defense_up,
                    defense_add=condition.enemy_defense_add,
                ),
                defense_penetration,
                condition.defense_reduction,
            )
        else:
            enemy_defense = calculate_enemy_defense(
                condition.enemy_level,
                defense_penetration,
                condition.defense_reduction,
                scene,
            )
        damage_attribute = hit.damage_attribute.casefold()
        defense = (
            1.0
            if damage_attribute == "psychically"
            else calculate_defense_multiplier(
                baseline.character_level,
                enemy_defense,
            )
        )
        resistance = dict(condition.resistances).get(damage_attribute, 0.20)
        resistance += sum(
            modifier.additive_value
            for modifier in projection.modifiers
            if modifier.target_scope == "target"
            and modifier.property_id.startswith("DamageResist")
        )
        penetration_property = _DAMAGE_PENETRATION_PROPERTY.get(
            damage_attribute
        )
        if penetration_property is not None:
            resistance -= values.get(penetration_property, 0.0)
        resistance_factor = max(0.0, calculate_resistance_multiplier(resistance))
        vulnerability = max(0.0, 1.0 + condition.vulnerability)
        return max(0.0, defense) * resistance_factor * vulnerability

    @staticmethod
    def _label(property_id: str, baseline: BattleCharacterBaseline) -> str:
        if property_id in _DAMAGE_PENETRATION_PROPERTY.values():
            return next(
                (row.label for row in baseline.stats if row.property_id == property_id),
                "属性抗性穿透",
            )
        if property_id in _ELEMENT_PROPERTIES:
            return next(
                (row.label for row in baseline.stats if row.property_id == property_id),
                "属性伤害增强",
            )
        return _MARGINAL_LABELS.get(property_id, property_id)

    @staticmethod
    def _assumption(
        property_id: str,
        supported: bool,
        *,
        applied_count: int,
        excluded_count: int,
    ) -> str:
        if not supported:
            if property_id == "DefIgnore":
                return "尚未保存用户确认的单目标等级/场景，防御忽略暂不计算。"
            if property_id in _DAMAGE_PENETRATION_PROPERTY.values():
                return "尚未保存用户确认的单目标分属性抗性，抗性穿透暂不计算。"
            return "当前逐击缺少可复用的倍率或缩放属性证据，暂不计算。"
        if property_id in {"CritBase", "CritDamageBase"}:
            basis = "逐击没有可靠暴击标记，使用期望暴击模型。"
        elif property_id == "MagBase":
            basis = "仅重放已识别的覆纹追加攻击，复用统一环合强度公式。"
        else:
            basis = "以真实逐击伤害为锚点，仅替换角色属性相关乘区。"
        return (
            f"{basis}已将 {applied_count} 个动态 Buff 区间按击投影；"
            f"{excluded_count} 个区间因常驻重复或证据不足未进入数值。"
        )
