# 以真实逐击为锚点，在逐击动态 Buff 面板上计算属性边际。
"""Battle-report marginal calculations with safe per-hit Buff projection."""

from __future__ import annotations

from collections.abc import Mapping

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleMarginalResult,
    BattleTargetCondition,
)
from src.domain.official_role import ROLE_PANEL_MARGINAL_UNITS
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
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
    "UnbalIntensityBase": "倾陷强度",
}
_MARGINAL_UNITS = {
    **ROLE_PANEL_MARGINAL_UNITS,
    "DefIgnore": 0.01,
    "MagBase": 6.0,
    "UnbalIntensityBase": 6.0,
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
        outgoing_hits = tuple(
            hit for hit in analysis.hits if hit.direction == "outgoing"
        )
        role_hits = tuple(
            hit
            for hit in outgoing_hits
            if hit.character_id == character_id
        )
        replays = {row.event_id: row for row in analysis.hit_replays}
        projections = {
            hit.event_id: BattleBuffAttributeProjectionService.project_hit(
                hit,
                analysis.buff_intervals,
            )
            for hit in role_hits
        }
        target_conditions = {
            hit.event_id: BattleTargetInstanceMappingService.analysis_for_hit(
                analysis,
                hit,
            ).target_condition
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
        observed_role_damage = sum(hit.damage for hit in role_hits) + derived_damage
        comparison = analysis.build_counterfactual
        comparison_hits = {
            row.event_id: max(0.0, float(row.predicted_damage))
            for row in (() if comparison is None else comparison.hits)
        }

        def anchor_damage(hit: BattleAnalysisHit) -> float:
            return comparison_hits.get(hit.event_id, max(0.0, float(hit.damage)))

        comparison_role = next(
            (
                row
                for row in (() if comparison is None else comparison.roles)
                if row.character_id == character_id
            ),
            None,
        )
        role_damage = (
            observed_role_damage
            if comparison_role is None
            else max(0.0, float(comparison_role.predicted_damage))
        )
        team_damage = (
            max(0.0, float(analysis.effective_damage))
            if comparison is None
            else max(0.0, float(comparison.predicted_damage))
        )
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
                    target_condition=target_conditions[hit.event_id],
                    replay=replays.get(hit.event_id),
                    character_id=character_id,
                )
            )
            topple_hits = tuple(
                hit
                for hit in outgoing_hits
                if property_id == "UnbalIntensityBase"
                and cls._topple_ratio(
                    replays.get(hit.event_id),
                    character_id=character_id,
                    unit=0.0,
                ) is not None
            )
            supported_by_id = {
                hit.event_id: hit for hit in (*supported_hits, *topple_hits)
            }
            supported_damage = sum(
                anchor_damage(hit) for hit in supported_by_id.values()
            )
            formula_hits = tuple(
                hit
                for hit in supported_hits
                if hit.classification in {"direct", "direct_follow_up", "weave"}
            )
            formula_damage = sum(anchor_damage(hit) for hit in formula_hits)
            edited_formula = sum(
                anchor_damage(hit) * cls._factor_ratio(
                    frozen,
                    edited,
                    hit,
                    projections[hit.event_id],
                    baseline,
                    target_conditions[hit.event_id],
                    replays.get(hit.event_id),
                )
                for hit in formula_hits
            )
            changed_formula = sum(
                anchor_damage(hit) * cls._factor_ratio(
                    frozen,
                    changed,
                    hit,
                    projections[hit.event_id],
                    baseline,
                    target_conditions[hit.event_id],
                    replays.get(hit.event_id),
                )
                for hit in formula_hits
            )
            topple_increment = sum(
                anchor_damage(hit) * (ratio - 1.0)
                for hit in topple_hits
                if (
                    ratio := cls._topple_ratio(
                        replays.get(hit.event_id),
                        character_id=character_id,
                        unit=unit,
                    )
                ) is not None
            )
            baseline_damage = role_damage - formula_damage + edited_formula
            predicted_damage = (
                baseline_damage + changed_formula - edited_formula + topple_increment
            )
            increment = predicted_damage - baseline_damage
            role_gain = increment / baseline_damage * 100.0 if baseline_damage else 0.0
            team_gain = (
                increment / team_damage * 100.0
                if team_damage
                else 0.0
            )
            percent = property_id not in {
                "AtkAdd", "HPMaxAdd", "DefAdd", "MagBase", "UnbalIntensityBase",
            }
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
                    min(100.0, supported_damage / role_damage * 100.0)
                    if role_damage else 0.0
                ),
                damage_share_percent=(
                    min(100.0, role_damage / team_damage * 100.0)
                    if team_damage else 0.0
                ),
                assumption=cls._assumption(
                    property_id,
                    supported_damage > 0,
                    applied_count=len(applied_intervals),
                    excluded_count=len(excluded_intervals),
                    critical_policies=tuple(
                        cls._critical_policy(replays.get(hit.event_id))
                        for hit in role_hits
                        if hit.classification
                        in {"direct", "direct_follow_up", "weave"}
                    ),
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
        replay: BattleHitReplayResult | None,
        character_id: int,
    ) -> bool:
        if property_id == "UnbalIntensityBase":
            return (
                BattleMarginalCalculationService._topple_ratio(
                    replay,
                    character_id=character_id,
                    unit=0.0,
                )
                is not None
            )
        if property_id in {"CritBase", "CritDamageBase"}:
            if replay is None and hit.classification == "weave":
                return False
            policy = BattleMarginalCalculationService._critical_policy(replay)
            if property_id == "CritBase":
                return policy == "character"
            return policy in {"character", "fixed"}
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
        replay: BattleHitReplayResult | None,
    ) -> float:
        attack = calculate_attribute_value(
            values.get("AtkBase", 0.0),
            values.get("AtkUp", 0.0),
            values.get("AtkAdd", 0.0),
        )
        policy = BattleMarginalCalculationService._critical_policy(replay)
        if policy in {"disabled", "unknown"}:
            critical = 1.0
        else:
            critical_rate = (
                float(replay.critical_rate or 0.0)
                if policy == "fixed" and replay is not None
                else values.get("CritBase", 0.05)
            )
            critical = calculate_critical_multiplier(
                min(1.0, max(0.0, critical_rate)),
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
        replay: BattleHitReplayResult | None,
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
            replay,
        )
        current_direct = cls._direct_factor(
            current_with_buff,
            hit.damage_attribute,
            replay,
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
    def _critical_policy(
        replay: BattleHitReplayResult | None,
    ) -> str:
        # Direct unit callers without replay evidence keep the historical
        # character-expectation fallback. Real battle-detail loads always carry
        # the structured policy produced by the shared hit replay.
        if replay is None:
            return "character"
        policy = str(getattr(replay, "critical_policy", "unknown"))
        return policy if policy in {"character", "fixed", "disabled"} else "unknown"

    @staticmethod
    def _topple_ratio(
        replay: BattleHitReplayResult | None,
        *,
        character_id: int,
        unit: float,
    ) -> float | None:
        """Scale one retained team-topple cell without rebuilding its formula."""

        if replay is None or replay.critical_state == "unreplayable":
            return None
        contributions = tuple(
            factor
            for factor in replay.factors
            if factor.factor_id.startswith("topple_character:")
        )
        source = next(
            (
                factor
                for factor in contributions
                if factor.factor_id == f"topple_character:{character_id}"
            ),
            None,
        )
        total = sum(max(0.0, float(factor.value)) for factor in contributions)
        if source is None or total <= 0.0:
            return None
        if not any(
            term.property_id == "UnbalIntensityBase" for term in source.terms
        ):
            return None

        def term_total(*property_ids: str) -> float:
            accepted = set(property_ids)
            return sum(
                float(term.value)
                for term in source.terms
                if term.property_id in accepted
            )

        base = max(0.0, term_total("UnbalIntensityBase"))
        up = term_total("UnbalIntensityUp")
        add = term_total("UnbalIntensityAdd")
        damage_up = term_total("UnbalDamageUp", "ToppleDamageUp")
        strength = base * (1.0 + up) + add
        changed_strength = max(0.0, base + unit) * (1.0 + up) + add
        current_zone = 1.0 + strength / 300.0 + damage_up
        changed_zone = 1.0 + changed_strength / 300.0 + damage_up
        if current_zone <= 0.0 or changed_zone < 0.0:
            return None
        changed_source = max(0.0, float(source.value)) * changed_zone / current_zone
        changed_total = total - max(0.0, float(source.value)) + changed_source
        return changed_total / total

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
        critical_policies: tuple[str, ...],
    ) -> str:
        if not supported:
            if property_id == "DefIgnore":
                return (
                    "当前相关逐击缺少可靠冻结敌方防御画像；"
                    "0% 表示未量化，不表示没有收益。"
                )
            if property_id in _DAMAGE_PENETRATION_PROPERTY.values():
                return (
                    "当前相关逐击缺少可靠冻结敌方分属性抗性画像；"
                    "0% 表示未量化，不表示没有收益。"
                )
            if property_id in {"CritBase", "CritDamageBase"}:
                policies = "/".join(sorted(set(critical_policies))) or "unknown"
                return (
                    f"当前相关逐击暴击策略为 {policies}，该属性没有可量化逐击；"
                    "固定暴击、不可暴击和未知策略不会退回本场暴击拟合。"
                )
            return "当前逐击缺少可复用的倍率或缩放属性证据，暂不计算。"
        if property_id in {"CritBase", "CritDamageBase"}:
            policies = "/".join(sorted(set(critical_policies))) or "character"
            basis = f"按逐击 {policies} 暴击策略使用期望伤害，不拟合本场暴击结果。"
        elif property_id == "MagBase":
            basis = "仅重放已识别的覆纹追加攻击，复用统一环合强度公式。"
        elif property_id == "UnbalIntensityBase":
            return (
                "复用团队倾陷逐角色贡献，单位只改变当前角色倾陷强度格；"
                "命中时倾陷 Buff 已保留在该角色公式因子中。"
            )
        else:
            basis = "以真实逐击伤害为锚点，仅替换角色属性相关乘区。"
        return (
            f"{basis}已将 {applied_count} 个动态 Buff 区间按击投影；"
            f"{excluded_count} 个区间因常驻重复或证据不足未进入数值。"
        )
