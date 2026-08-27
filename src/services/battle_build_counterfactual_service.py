# 在同一真实逐击轴上比较原始配置与修改配置的整队期望伤害。
"""Fixed-axis build comparison with an explicit full-damage estimate ladder."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from statistics import median

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleBuildCounterfactual,
    BattleBuildHitCounterfactual,
    BattleBuildRoleCounterfactual,
    BattleBuildVitalCounterfactual,
    BattleCharacterBaseline,
    BattleHitReplayResult,
    BattleRangeRoleSummary,
    BattleTargetCondition,
)
from src.services.battle_damage_composition_service import (
    BattleDamageCompositionService,
)
from src.services.battle_replay_formula_ratio_service import (
    paired_replay_formula,
    replay_formula_value,
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
)


BUILD_COUNTERFACTUAL_MODEL_VERSION = "battle-build-counterfactual-v2"

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
_STRUCTURED_METHODS = {"structured_expected", "structured_selected"}
_STRUCTURED_VITAL_METHODS = {
    "linked_source_hit_ratio",
    "fadia_inherent_hp_ratio",
    "mechanic_disabled",
}


def _stats(baseline: BattleCharacterBaseline | None) -> dict[str, float]:
    if baseline is None:
        return {}
    return {row.property_id: float(row.value) for row in baseline.stats}


def _safe_ratio(candidate: float, baseline: float) -> float | None:
    if baseline <= 0 or candidate < 0:
        return None
    value = candidate / baseline
    if value != value or value == float("inf"):
        return None
    return max(0.0, min(100.0, value))


class BattleBuildCounterfactualService:
    """Compare two independently replayed builds while preserving the real axis."""

    @classmethod
    def compare(
        cls,
        *,
        original: BattleAnalysisSnapshot,
        candidate: BattleAnalysisSnapshot,
    ) -> BattleBuildCounterfactual:
        if original.battle_record_id != candidate.battle_record_id:
            raise ValueError("原始配置与修改配置不属于同一战报")
        if (
            original.range_start_us != candidate.range_start_us
            or original.range_end_us != candidate.range_end_us
        ):
            raise ValueError("原始配置与修改配置没有冻结到同一分析时段")

        original_hits = {
            hit.event_id: hit
            for hit in original.hits
            if hit.direction == "outgoing"
        }
        candidate_hits = {
            hit.event_id: hit
            for hit in candidate.hits
            if hit.direction == "outgoing"
        }
        original_replays = {row.event_id: row for row in original.hit_replays}
        candidate_replays = {row.event_id: row for row in candidate.hit_replays}
        exact_ratios = cls._exact_ratios(
            original_hits,
            original_replays,
            candidate_replays,
        )
        skill_ratios, type_ratios, role_ratios = cls._ratio_catalogs(
            original_hits,
            exact_ratios,
        )
        original_baselines = {
            row.character_id: row for row in original.baselines
        }
        candidate_baselines = {
            row.character_id: row for row in candidate.baselines
        }

        projected_hits: list[BattleBuildHitCounterfactual] = []
        for event_id, hit in original_hits.items():
            candidate_hit = candidate_hits.get(event_id, hit)
            formula_pair = paired_replay_formula(
                original_replays.get(event_id),
                candidate_replays.get(event_id),
            )
            baseline_formula_damage = (
                formula_pair.baseline_damage
                if formula_pair is not None
                else replay_formula_value(original_replays.get(event_id))[0]
            )
            candidate_formula_damage = (
                formula_pair.candidate_damage
                if formula_pair is not None
                else replay_formula_value(candidate_replays.get(event_id))[0]
            )
            ratio, method, confidence, explanation = cls._estimate_ratio(
                hit=hit,
                candidate_hit=candidate_hit,
                exact_ratios=exact_ratios,
                skill_ratios=skill_ratios,
                type_ratios=type_ratios,
                role_ratios=role_ratios,
                original_baseline=original_baselines.get(hit.character_id),
                candidate_baseline=candidate_baselines.get(hit.character_id),
                target_condition=original.target_condition,
            )
            projected_hits.append(BattleBuildHitCounterfactual(
                event_id=event_id,
                character_id=hit.character_id,
                character_name=hit.character_name,
                skill_name=hit.skill_name,
                damage_name=hit.damage_name,
                baseline_damage=float(hit.damage),
                predicted_damage=float(hit.damage) * ratio,
                ratio=ratio,
                method=method,
                confidence=confidence,
                explanation=explanation,
                baseline_formula_damage=baseline_formula_damage,
                candidate_formula_damage=candidate_formula_damage,
            ))

        projected_vital_events = cls._vital_events(
            original=original,
            candidate=candidate,
            projected_hits=projected_hits,
            original_hits=original_hits,
            original_baselines=original_baselines,
            candidate_baselines=candidate_baselines,
        )
        role_rows = cls._roles(original, projected_hits, projected_vital_events)
        hit_baseline = sum(row.baseline_damage for row in projected_hits)
        hit_predicted = sum(row.predicted_damage for row in projected_hits)
        vital_baseline = sum(row.baseline_damage for row in projected_vital_events)
        vital_predicted = sum(row.predicted_damage for row in projected_vital_events)
        fixed_derived_damage = max(
            0.0,
            original.effective_damage - hit_baseline - vital_baseline,
        )
        baseline_damage = hit_baseline + vital_baseline + fixed_derived_damage
        predicted_damage = hit_predicted + vital_predicted + fixed_derived_damage
        structured_damage = sum(
            row.baseline_damage
            for row in projected_hits
            if row.method in _STRUCTURED_METHODS
        )
        structured_damage += sum(
            row.baseline_damage
            for row in projected_vital_events
            if row.method in _STRUCTURED_VITAL_METHODS
        )
        estimated_damage = max(0.0, baseline_damage - structured_damage)
        duration = max(0.001, float(original.duration_seconds))
        gain = (
            (predicted_damage / baseline_damage - 1.0) * 100.0
            if baseline_damage
            else 0.0
        )
        assumptions = (
            "固定原战报动作、逐击、目标与时段，只替换角色配置后重放。",
            "原击已识别暴击分支时，候选沿用同一分支；分支不唯一但暴击策略已知时才使用期望公式。",
            "结构化公式不可用时依次采用同技能、同类型、角色面板和同比保持估计；原轴伤害覆盖率固定为100%。",
            "已归因生命上限结算按来源机制联动：安魂曲五觉跟随对应噩梦逐击，法帝娅被动跟随其固有生命上限；证据不足时才固定原值。",
            "候选配置新增或删除动作、命中次数、技能循环和完全未知的额外结算，本阶段按零增量保守估计。",
        )
        projected_damage_by_event = {
            row.event_id: row.predicted_damage for row in projected_hits
        }
        composition_hits = tuple(
            replace(hit, damage=projected_damage_by_event.get(hit.event_id, hit.damage))
            for hit in original.hits
        )
        composition_roles = tuple(
            BattleRangeRoleSummary(
                character_id=row.character_id,
                character_name=row.character_name,
                hits=sum(
                    1 for hit in projected_hits if hit.character_id == row.character_id
                ),
                damage=row.predicted_damage,
                dps=row.predicted_damage / duration,
                share_percent=(
                    row.predicted_damage / predicted_damage * 100.0
                    if predicted_damage
                    else 0.0
                ),
            )
            for row in role_rows
        )
        vital_by_event = {row.event_id: row for row in projected_vital_events}
        composition_vital_events = tuple(
            replace(
                event,
                effective_hp_loss=vital_by_event[event.event_id].predicted_damage,
            )
            if event.event_id in vital_by_event
            else event
            for event in original.max_hp_events
        )
        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=composition_roles,
            hits=composition_hits,
            max_hp_events=composition_vital_events,
            hit_replays=candidate.hit_replays,
            role_identities=tuple(
                (row.character_id, row.character_name)
                for row in candidate.baselines
            ),
            segment_total_damage=predicted_damage,
        )
        return BattleBuildCounterfactual(
            model_version=BUILD_COUNTERFACTUAL_MODEL_VERSION,
            baseline_damage=baseline_damage,
            predicted_damage=predicted_damage,
            gain_percent=gain,
            baseline_dps=baseline_damage / duration,
            predicted_dps=predicted_damage / duration,
            structured_damage=structured_damage,
            estimated_damage=estimated_damage,
            structured_percent=(
                structured_damage / baseline_damage * 100.0
                if baseline_damage
                else 0.0
            ),
            roles=role_rows,
            hits=tuple(projected_hits),
            composition=composition,
            assumptions=assumptions,
            vital_events=projected_vital_events,
        )

    @classmethod
    def _vital_events(
        cls,
        *,
        original: BattleAnalysisSnapshot,
        candidate: BattleAnalysisSnapshot,
        projected_hits: Sequence[BattleBuildHitCounterfactual],
        original_hits: Mapping[str, BattleAnalysisHit],
        original_baselines: Mapping[int, BattleCharacterBaseline],
        candidate_baselines: Mapping[int, BattleCharacterBaseline],
    ) -> tuple[BattleBuildVitalCounterfactual, ...]:
        projected_by_event = {row.event_id: row for row in projected_hits}
        candidate_vital = {row.event_id: row for row in candidate.max_hp_events}
        result = []
        for event in original.max_hp_events:
            baseline_damage = max(0.0, float(event.effective_hp_loss))
            ratio = 1.0
            method = "fixed_derived_estimate"
            confidence = "低"
            explanation = "缺少可安全联动的来源公式，按原生命上限结算值保留"
            candidate_event = candidate_vital.get(event.event_id)
            if event.mechanic_kind == "lacrimosa_nightmare_awaken_5":
                if (
                    candidate_event is None
                    or candidate_event.mechanic_kind != event.mechanic_kind
                ):
                    ratio = 0.0
                    method = "mechanic_disabled"
                    confidence = "高"
                    explanation = "候选配置未激活安魂曲五觉，本次生命上限结算归零"
                else:
                    linked = cls._linked_vital_hits(
                        event.evidence_event_ids,
                        event.source_character_id,
                        event.source_skill_name,
                        projected_by_event,
                        original_hits,
                    )
                    linked_baseline = sum(row.baseline_damage for row in linked)
                    linked_candidate = sum(row.predicted_damage for row in linked)
                    linked_ratio = _safe_ratio(linked_candidate, linked_baseline)
                    if linked_ratio is not None:
                        ratio = linked_ratio
                        method = "linked_source_hit_ratio"
                        confidence = "中"
                        explanation = "按已归因噩梦逐击的候选/原始伤害比联动五觉结算"
            elif (
                event.mechanic_kind == "fadia_dark_star_max_hp_transfer"
                and event.source_character_id is not None
            ):
                original_hp = original_baselines.get(event.source_character_id)
                candidate_hp = candidate_baselines.get(event.source_character_id)
                hp_ratio = _safe_ratio(
                    float(candidate_hp.inherent_hp or 0.0) if candidate_hp else 0.0,
                    float(original_hp.inherent_hp or 0.0) if original_hp else 0.0,
                )
                if hp_ratio is not None:
                    ratio = hp_ratio
                    method = "fadia_inherent_hp_ratio"
                    confidence = "中"
                    explanation = "按候选/原始法帝娅固有生命上限比联动被动结算"
            result.append(BattleBuildVitalCounterfactual(
                event_id=event.event_id,
                character_id=event.source_character_id,
                character_name=event.source_character_name,
                mechanic_kind=event.mechanic_kind,
                mechanic_name=event.mechanic_name,
                baseline_damage=baseline_damage,
                predicted_damage=baseline_damage * ratio,
                ratio=ratio,
                method=method,
                confidence=confidence,
                explanation=explanation,
            ))
        return tuple(result)

    @staticmethod
    def _linked_vital_hits(
        evidence_event_ids: Sequence[str],
        character_id: int | None,
        source_skill_name: str,
        projected_by_event: Mapping[str, BattleBuildHitCounterfactual],
        original_hits: Mapping[str, BattleAnalysisHit],
    ) -> tuple[BattleBuildHitCounterfactual, ...]:
        rows = tuple(
            projected_by_event[event_id]
            for event_id in evidence_event_ids
            if event_id in projected_by_event
            and projected_by_event[event_id].character_id == character_id
        )
        if not rows:
            return ()
        source_name = source_skill_name.casefold()
        named = tuple(
            row
            for row in rows
            if source_name
            and (
                source_name in row.skill_name.casefold()
                or source_name in row.damage_name.casefold()
            )
        )
        if named:
            return named
        effect_matched = tuple(
            row
            for row in rows
            if "lacrimosa_blood_damage" in original_hits[row.event_id].gameplay_effect_id.casefold()
        )
        return effect_matched or rows

    @staticmethod
    def _exact_ratios(
        hits: Mapping[str, BattleAnalysisHit],
        original_replays: Mapping[str, BattleHitReplayResult],
        candidate_replays: Mapping[str, BattleHitReplayResult],
    ) -> dict[str, tuple[float, str]]:
        result: dict[str, tuple[float, str]] = {}
        for event_id in hits:
            pair = paired_replay_formula(
                original_replays.get(event_id),
                candidate_replays.get(event_id),
            )
            if pair is None:
                continue
            ratio = _safe_ratio(pair.candidate_damage, pair.baseline_damage)
            if ratio is None:
                continue
            result[event_id] = ratio, pair.method
        return result

    @classmethod
    def _ratio_catalogs(
        cls,
        hits: Mapping[str, BattleAnalysisHit],
        exact_ratios: Mapping[str, tuple[float, str]],
    ) -> tuple[
        dict[tuple[object, ...], float],
        dict[tuple[object, ...], float],
        dict[int, float],
    ]:
        skill_values: dict[tuple[object, ...], list[float]] = defaultdict(list)
        type_values: dict[tuple[object, ...], list[float]] = defaultdict(list)
        role_values: dict[int, list[float]] = defaultdict(list)
        for event_id, (ratio, _method) in exact_ratios.items():
            hit = hits[event_id]
            if hit.character_id is None:
                continue
            skill_values[cls._skill_key(hit)].append(ratio)
            type_values[cls._type_key(hit)].append(ratio)
            role_values[hit.character_id].append(ratio)
        return (
            {key: median(values) for key, values in skill_values.items()},
            {key: median(values) for key, values in type_values.items()},
            {key: median(values) for key, values in role_values.items()},
        )

    @classmethod
    def _estimate_ratio(
        cls,
        *,
        hit: BattleAnalysisHit,
        candidate_hit: BattleAnalysisHit,
        exact_ratios: Mapping[str, tuple[float, str]],
        skill_ratios: Mapping[tuple[object, ...], float],
        type_ratios: Mapping[tuple[object, ...], float],
        role_ratios: Mapping[int, float],
        original_baseline: BattleCharacterBaseline | None,
        candidate_baseline: BattleCharacterBaseline | None,
        target_condition: BattleTargetCondition | None,
    ) -> tuple[float, str, str, str]:
        exact = exact_ratios.get(hit.event_id)
        if exact is not None:
            method_label = (
                "逐击期望公式比值"
                if exact[1] == "structured_expected"
                else "逐击已选公式比值"
            )
            return exact[0], exact[1], "高", method_label
        skill_ratio = skill_ratios.get(cls._skill_key(hit))
        if skill_ratio is not None:
            return skill_ratio, "skill_peer_estimate", "中", "采用同技能已重放逐击的中位比值"
        type_ratio = type_ratios.get(cls._type_key(hit))
        if type_ratio is not None:
            return type_ratio, "type_peer_estimate", "中", "采用同角色同伤害类型已重放逐击的中位比值"
        panel_ratio = cls._panel_ratio(
            hit=candidate_hit,
            original_baseline=original_baseline,
            candidate_baseline=candidate_baseline,
            target_condition=target_condition,
        )
        if panel_ratio is not None:
            return panel_ratio, "panel_formula_estimate", "低", "按角色面板、属性与已确认目标乘区估计"
        if hit.character_id is not None and hit.character_id in role_ratios:
            return role_ratios[hit.character_id], "role_peer_estimate", "低", "采用该角色可重放逐击的中位比值"
        return 1.0, "unchanged_estimate", "低", "缺少可量化差异，按原击同比保持估计"

    @classmethod
    def _panel_ratio(
        cls,
        *,
        hit: BattleAnalysisHit,
        original_baseline: BattleCharacterBaseline | None,
        candidate_baseline: BattleCharacterBaseline | None,
        target_condition: BattleTargetCondition | None,
    ) -> float | None:
        original_values = _stats(original_baseline)
        candidate_values = _stats(candidate_baseline)
        if not original_values or not candidate_values:
            return None
        original = cls._panel_factor(
            original_values,
            hit.damage_attribute,
            float(original_baseline.character_level),
            target_condition,
        )
        candidate = cls._panel_factor(
            candidate_values,
            hit.damage_attribute,
            float(candidate_baseline.character_level),
            target_condition,
        )
        return _safe_ratio(candidate, original)

    @staticmethod
    def _panel_factor(
        values: Mapping[str, float],
        damage_attribute: str,
        character_level: float,
        target_condition: object,
    ) -> float:
        attribute = damage_attribute.casefold()
        attack = calculate_attribute_value(
            values.get("AtkBase", 0.0),
            values.get("AtkUp", 0.0),
            values.get("AtkAdd", 0.0),
        )
        critical = calculate_critical_multiplier(
            min(1.0, max(0.0, values.get("CritBase", 0.05))),
            max(0.0, values.get("CritDamageBase", 0.50)),
        )
        increase = 1.0 + values.get("DamageUpGeneralBase", 0.0)
        increase += values.get(_ELEMENT_PROPERTY.get(attribute, ""), 0.0)
        target = BattleBuildCounterfactualService._target_factor(
            values,
            attribute,
            character_level,
            target_condition,
        )
        return max(0.0, attack * critical * increase * target)

    @staticmethod
    def _target_factor(
        values: Mapping[str, float],
        attribute: str,
        character_level: float,
        condition: BattleTargetCondition | None,
    ) -> float:
        if condition is None:
            return 1.0
        scene = (
            DamageScene.BIG_WORLD
            if str(getattr(condition, "scene", "")) == "big_world"
            else DamageScene.OUTER_REALM
        )
        defense_penetration = min(1.0, max(-1.0, values.get("DefIgnore", 0.0)))
        defense_base = getattr(condition, "enemy_defense_base", None)
        if defense_base is not None:
            enemy_defense = calculate_enemy_defense_from_profile(
                EnemyDefenseProfileInput(
                    defense_base=float(defense_base),
                    defense_up=float(getattr(condition, "enemy_defense_up", 0.0)),
                    defense_add=float(getattr(condition, "enemy_defense_add", 0.0)),
                ),
                defense_penetration,
                float(getattr(condition, "defense_reduction", 0.0)),
            )
        else:
            enemy_defense = calculate_enemy_defense(
                float(getattr(condition, "enemy_level", 80.0)),
                defense_penetration,
                float(getattr(condition, "defense_reduction", 0.0)),
                scene,
            )
        defense = (
            1.0
            if attribute == "psychically"
            else calculate_defense_multiplier(character_level, enemy_defense)
        )
        resistances = dict(getattr(condition, "resistances", ()))
        resistance = float(resistances.get(attribute, 0.20))
        resistance -= values.get(_PENETRATION_PROPERTY.get(attribute, ""), 0.0)
        resistance_factor = calculate_resistance_multiplier(resistance)
        vulnerability = 1.0 + float(getattr(condition, "vulnerability", 0.0))
        return max(0.0, defense * resistance_factor * vulnerability)

    @staticmethod
    def _skill_key(hit: BattleAnalysisHit) -> tuple[object, ...]:
        return (
            hit.character_id,
            hit.ability_id or hit.skill_name,
            hit.damage_attribute.casefold(),
            hit.classification,
        )

    @staticmethod
    def _type_key(hit: BattleAnalysisHit) -> tuple[object, ...]:
        return (
            hit.character_id,
            hit.damage_attribute.casefold(),
            hit.classification,
        )

    @staticmethod
    def _roles(
        original: BattleAnalysisSnapshot,
        projected_hits: Sequence[BattleBuildHitCounterfactual],
        projected_vital_events: Sequence[BattleBuildVitalCounterfactual],
    ) -> tuple[BattleBuildRoleCounterfactual, ...]:
        projected_by_role: dict[int, list[BattleBuildHitCounterfactual]] = defaultdict(list)
        for hit in projected_hits:
            if hit.character_id is not None:
                projected_by_role[hit.character_id].append(hit)
        vital_by_role: dict[int, list[BattleBuildVitalCounterfactual]] = defaultdict(list)
        for event in projected_vital_events:
            if event.character_id is not None:
                vital_by_role[event.character_id].append(event)
        result = []
        for role in original.roles:
            hits = projected_by_role.get(role.character_id, [])
            vital_events = vital_by_role.get(role.character_id, [])
            derived = max(
                0.0,
                role.damage
                - sum(row.baseline_damage for row in hits)
                - sum(row.baseline_damage for row in vital_events),
            )
            baseline = (
                sum(row.baseline_damage for row in hits)
                + sum(row.baseline_damage for row in vital_events)
                + derived
            )
            predicted = (
                sum(row.predicted_damage for row in hits)
                + sum(row.predicted_damage for row in vital_events)
                + derived
            )
            structured = sum(
                row.baseline_damage
                for row in hits
                if row.method in _STRUCTURED_METHODS
            )
            structured += sum(
                row.baseline_damage
                for row in vital_events
                if row.method in _STRUCTURED_VITAL_METHODS
            )
            estimated = max(0.0, baseline - structured)
            result.append(BattleBuildRoleCounterfactual(
                character_id=role.character_id,
                character_name=role.character_name,
                baseline_damage=baseline,
                predicted_damage=predicted,
                gain_percent=(
                    (predicted / baseline - 1.0) * 100.0 if baseline else 0.0
                ),
                team_gain_percent=(
                    (predicted - baseline) / original.effective_damage * 100.0
                    if original.effective_damage
                    else 0.0
                ),
                structured_damage=structured,
                estimated_damage=estimated,
                structured_percent=(
                    structured / baseline * 100.0 if baseline else 0.0
                ),
            ))
        return tuple(sorted(result, key=lambda row: row.baseline_damage, reverse=True))
