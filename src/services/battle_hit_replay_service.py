# 按明确暴击候选与证据缺口确定性重放每一击。
from __future__ import annotations
from collections.abc import Mapping, Sequence
from dataclasses import replace
from src.domain.battle_report import (
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleSkillDamageEvidence,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_interval_index import (
    BattleBuffIntervalIndex,
    BattleBuffIntervalQuery,
)
from src.services.damage_calculation_service import (
    DamageScene,
    EnemyDefenseProfileInput,
    calculate_defense_multiplier,
    calculate_enemy_defense,
    calculate_enemy_defense_from_profile,
    calculate_resistance_multiplier,
)
from src.services.battle_damage_composition_service import classify_battle_hit_channel
from src.services.battle_special_hit_replay_service import BattleSpecialHitReplayService
from src.services.battle_topple_hit_replay_service import (
    BattleToppleCharacterConfig, BattleToppleHitReplayService,
)
from src.services.battle_hit_replay_support import (
    apply_observed_damage_correction, ceil_replay_damage,
    dot_final_replay_factors,
    first_replay_value as _first_value,
    literal_replay_term,
    replay_factor as _factor,
    replay_error_percent,
    replay_signed_error_percent,
    replay_source_terms as _source_terms,
    replay_target_profile_basis,
    reanchor_direct_replay_result,
)
from src.services.battle_hit_replay_audit_service import BattleHitReplayAuditService
from src.services.battle_hit_replay_formula_catalog import (
    DIRECT_FORMULA_CHANNELS as _DIRECT_FORMULA_CHANNELS,
    ELEMENT_DAMAGE_PROPERTIES as _ELEMENT_DAMAGE_PROPERTIES,
    ELEMENT_PENETRATION_PROPERTIES as _ELEMENT_PENETRATION_PROPERTIES,
    ELEMENT_RESISTANCE_PROPERTIES as _ELEMENT_RESISTANCE_PROPERTIES,
)
from src.services.battle_inferred_target_condition_service import (
    INFERRED_ENCOUNTER_SOURCE_KIND,
)
from src.services.battle_selected_hit_replay_context import (
    PreparedReplayAuditContext,
    PreparedReplayAuditInputs,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_full_replay_formula_cache import (
    full_replay_formula_cache_key,
)
from src.services.battle_hit_buff_projection_cache import (
    BattleHitBuffProjectionCache,
)
HIT_REPLAY_MODEL_VERSION = "battle-hit-replay-v29"
class BattleHitReplayService:
    @classmethod
    def replay(
        cls,
        analysis: BattleAnalysisSnapshot,
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        *,
        topple_character_configs: (
            Mapping[int, BattleToppleCharacterConfig] | None
        ) = None,
        apply_observed_refinements: bool = True,
        prepared_audit_context: PreparedReplayAuditContext | None = None,
        prepared_audit_inputs: PreparedReplayAuditInputs | None = None,
        buff_interval_index: BattleBuffIntervalQuery | None = None,
        projection_by_event: Mapping[str, BattleHitBuffProjection] | None = None,
        progress_callback: BattleAnalysisProgressCallback | None = None,
        progress_phase: str = "hit_replay",
        progress_message: str = "正在执行固定轴逐击公式重放…",
    ) -> tuple[BattleHitReplayResult, ...]:
        if prepared_audit_context is not None:
            prepared_audit_context.require_selected_replay()
        evidence_by_event = (
            prepared_audit_inputs.evidence_by_event
            if prepared_audit_inputs is not None
            else {row.event_id: row for row in skill_evidence}
        )
        baselines = (
            prepared_audit_inputs.baselines_by_character
            if prepared_audit_inputs is not None
            else {row.character_id: row for row in analysis.baselines}
        )
        baseline_values = (
            prepared_audit_inputs.baseline_values_by_character
            if prepared_audit_inputs is not None
            else {}
        )
        buff_intervals = (
            buff_interval_index
            if buff_interval_index is not None
            else BattleBuffIntervalIndex(
                getattr(analysis, "buff_intervals", ())
            )
        )
        projection_cache = BattleHitBuffProjectionCache(buff_intervals)
        replay_hits = tuple(
            hit
            for hit in analysis.hits
            if hit.direction == "outgoing"
            and (
                prepared_audit_context is None
                or hit.event_id in prepared_audit_context.selected_event_ids
            )
        )
        total_hits = len(replay_hits)
        report_battle_analysis_progress(
            progress_callback,
            phase=progress_phase,
            message=progress_message,
            completed=0,
            total=total_hits,
        )
        target_analysis_by_key: dict[tuple[str, str], BattleAnalysisSnapshot] = {}
        direct_formula_cache: dict[object, BattleHitReplayResult] = {}
        results = []
        for ordinal, hit in enumerate(replay_hits, start=1):
            if ordinal > 1 and (ordinal - 1) % 64 == 0:
                report_battle_analysis_progress(
                    progress_callback,
                    phase=progress_phase,
                    message=progress_message,
                    completed=ordinal - 1,
                    total=total_hits,
                )
            channel_id, formula_label = classify_battle_hit_channel(hit)
            evidence = evidence_by_event.get(hit.event_id)
            formula_hit = replace(hit, character_id=evidence.source_character_id) \
                if evidence and evidence.source_character_id is not None else hit
            baseline = baselines.get(formula_hit.character_id)
            if (
                prepared_audit_inputs is not None
                and hit.event_id
                in prepared_audit_inputs.target_condition_by_event
            ):
                hit_analysis = replace(
                    analysis,
                    target_condition=(
                        prepared_audit_inputs.target_condition_by_event[
                            hit.event_id
                        ]
                    ),
                )
            else:
                target_key = (
                    str(getattr(hit, "scope_half", "") or "").casefold(),
                    str(getattr(hit, "target_id", "") or ""),
                )
                hit_analysis = target_analysis_by_key.get(target_key)
                if hit_analysis is None:
                    hit_analysis = BattleTargetInstanceMappingService.analysis_for_hit(
                        analysis,
                        hit,
                    )
                    target_analysis_by_key[target_key] = hit_analysis
            if channel_id == "special_daffodill_extra_topple":
                result = BattleToppleHitReplayService.replay(
                    hit=hit, analysis=hit_analysis,
                    character_configs=topple_character_configs or {},
                    source_character_id=1054, formula_type="达芙蒂尔·额外倾陷伤害",
                )
                results.append(apply_observed_damage_correction(result, hit))
                continue
            if channel_id == "other_topple":
                result = BattleToppleHitReplayService.replay(
                    hit=hit,
                    analysis=hit_analysis,
                    character_configs=topple_character_configs or {},
                )
                results.append(apply_observed_damage_correction(result, hit))
                continue
            if channel_id == "special_fadia_shared_damage":
                result = cls._unreplayable(
                    hit.event_id,
                    hit.damage,
                    (
                        "破灭体验按法帝娅实际承受伤害转移（基础 300%，二觉 600%）；"
                        "当前封包只有护盾与分摊前的受击值，尚不能安全还原实际承受值"
                    ),
                    formula_label,
                )
                results.append(apply_observed_damage_correction(result, hit))
                continue
            if baseline is None:
                result = cls._unreplayable(
                    hit.event_id,
                    hit.damage,
                    "缺少角色面板",
                    formula_label,
                )
                results.append(apply_observed_damage_correction(result, hit))
                continue
            if channel_id == "reaction_hexed":
                projection = (
                    None
                    if projection_by_event is None
                    else projection_by_event.get(hit.event_id)
                )
                if projection is None:
                    projection = projection_cache.project(formula_hit)
                frozen = baseline_values.get(formula_hit.character_id) or {
                    row.property_id: row.value for row in baseline.stats
                }
                values = BattleBuffAttributeProjectionService.apply_additive(
                    frozen,
                    projection,
                )
                result = BattleSpecialHitReplayService.replay(
                    channel_id=channel_id,
                    formula_label=formula_label,
                    hit=formula_hit,
                    evidence=evidence,
                    projection=projection,
                    values=values,
                    analysis=hit_analysis,
                )
                assert result is not None
                results.append(apply_observed_damage_correction(result, hit))
                continue
            if evidence is None:
                result = cls._unreplayable(
                    hit.event_id,
                    hit.damage,
                    "缺少等级解析后的技能倍率",
                    formula_label,
                )
                results.append(apply_observed_damage_correction(result, hit))
                continue
            if getattr(hit_analysis, "target_condition", None) is None:
                result = cls._unreplayable(
                    hit.event_id,
                    hit.damage,
                    "尚未保存用户确认的单目标防御与抗性",
                    "直伤",
                )
                results.append(apply_observed_damage_correction(result, hit))
                continue
            projection = (
                None
                if projection_by_event is None
                else projection_by_event.get(hit.event_id)
            )
            if projection is None:
                projection = projection_cache.project(formula_hit)
            frozen = baseline_values.get(formula_hit.character_id) or {
                row.property_id: row.value for row in baseline.stats
            }
            values = BattleBuffAttributeProjectionService.apply_additive(
                frozen,
                projection,
            )
            if channel_id not in _DIRECT_FORMULA_CHANNELS:
                special = BattleSpecialHitReplayService.replay(
                    channel_id=channel_id,
                    formula_label=formula_label,
                    hit=formula_hit,
                    evidence=evidence,
                    projection=projection,
                    values=values,
                    analysis=hit_analysis,
                )
                result = (
                    special
                    if special is not None
                    else cls._unreplayable(
                        hit.event_id,
                        hit.damage,
                        f"{formula_label} 需要独立逐击重放适配器",
                        formula_label,
                    )
                )
                results.append(apply_observed_damage_correction(result, hit))
                continue
            rendered_formula_label = (
                formula_label
                if channel_id in {
                    "direct",
                    "direct_follow_up",
                    "special_lacrimosa_dissonance",
                }
                else f"直伤（{formula_label}）"
            )
            cache_key = full_replay_formula_cache_key(
                channel_id=channel_id,
                formula_label=rendered_formula_label,
                hit=formula_hit,
                evidence=evidence,
                baseline=baseline,
                projection=projection,
                values=values,
                analysis=hit_analysis,
            )
            template = (
                None if cache_key is None else direct_formula_cache.get(cache_key)
            )
            if template is None:
                result = cls._replay_direct(
                    hit=formula_hit,
                    evidence=evidence,
                    baseline=baseline,
                    projection=projection,
                    values=values,
                    character_level=baseline.character_level,
                    analysis=hit_analysis,
                    applied_intervals=projection.applied_interval_ids,
                    excluded_intervals=projection.excluded_interval_ids,
                    formula_label=rendered_formula_label,
                )
                if cache_key is not None:
                    direct_formula_cache[cache_key] = result
            else:
                result = reanchor_direct_replay_result(template, formula_hit)
            results.append(apply_observed_damage_correction(
                result,
                hit,
            ))
        raw_results = tuple(results)
        if prepared_audit_context is not None:
            report_battle_analysis_progress(
                progress_callback, phase=progress_phase,
                message=progress_message, completed=total_hits, total=total_hits,
            )
            return prepared_audit_context.freeze_candidate_branches(raw_results)
        if not apply_observed_refinements:
            report_battle_analysis_progress(
                progress_callback, phase=progress_phase,
                message=progress_message, completed=total_hits, total=total_hits,
            )
            return raw_results
        return BattleHitReplayAuditService.postprocess(
            analysis, raw_results, progress_callback=progress_callback,
        )
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
    ) -> BattleHitReplayResult:
        condition = analysis.target_condition
        assert condition is not None
        inferred_target = (
            condition.source_kind == INFERRED_ENCOUNTER_SOURCE_KIND
        )
        resolved_target, target_profile_basis = replay_target_profile_basis(
            analysis, inferred_target
        )
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
        scaling_value = _first_value(values, evidence.scaling_property_id)
        multiplier = (
            evidence.scaling_multiplier * evidence.multiplier_coefficient
        )
        element_property = _ELEMENT_DAMAGE_PROPERTIES.get(attribute)
        damage_increase = (
            1.0
            + values.get("DamageUpGeneralBase", 0.0)
            + (values.get(element_property, 0.0) if element_property else 0.0)
        )
        vulnerability = 1.0 + condition.vulnerability
        penetration = values.get("DefIgnore", 0.0)
        if condition.enemy_defense_base is None:
            scene = (
                DamageScene.OPEN_WORLD
                if condition.scene == "open_world"
                else DamageScene.OUTER_REALM
            )
            enemy_defense = calculate_enemy_defense(
                condition.enemy_level,
                penetration,
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
                penetration,
                condition.defense_reduction,
            )
            defense_basis = f"{target_profile_basis} DefBase/6"
        defense = (
            1.0
            if attribute == "psychically"
            else calculate_defense_multiplier(character_level, enemy_defense)
        )
        base_resistance = dict(condition.resistances).get(attribute, 0.20)
        resistance_property_ids = _ELEMENT_RESISTANCE_PROPERTIES.get(attribute, ())
        dynamic_resistance = sum(
            modifier.additive_value
            for modifier in projection.modifiers
            if modifier.target_scope == "target"
            and modifier.property_id in resistance_property_ids
        )
        resistance = base_resistance + dynamic_resistance
        penetration_property = _ELEMENT_PENETRATION_PROPERTIES.get(attribute)
        if penetration_property:
            resistance -= values.get(penetration_property, 0.0)
        resistance_factor = calculate_resistance_multiplier(resistance)
        independent = 1.0
        for property_id, value in values.items():
            normalized = property_id.casefold()
            if "finaldamage" in normalized or "damageupfinal" in normalized:
                independent *= 1.0 + value
        one_stack_non_critical = (
            multiplier
            * scaling_value
            * damage_increase
            * defense
            * resistance_factor
            * vulnerability
            * independent
            * max(1.0, evidence.dot_final_multiplier)
        )
        crit_damage_bonus = max(0.0, values.get("CritDamageBase", 0.50))
        stack_coefficient = max(1.0, evidence.state_multiplier)
        raw_non_critical = one_stack_non_critical * stack_coefficient
        non_critical = ceil_replay_damage(raw_non_critical)
        critical_disabled = evidence.critical_policy == "disabled"
        critical_unknown = evidence.critical_policy == "unknown"
        critical = (
            None
            if critical_disabled
            else ceil_replay_damage(raw_non_critical * (1.0 + crit_damage_bonus))
        )
        critical_rate = (
            None
            if critical_unknown
            else 0.0 if critical_disabled
            else min(
                1.0,
                max(
                    0.0,
                    evidence.fixed_crit_rate
                    if evidence.critical_policy == "fixed"
                    else values.get("CritBase", 0.05),
                ),
            )
        )
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
            tuple(
                property_id
                for property_id in ("DamageUpGeneralBase", element_property)
                if property_id
            ),
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
            if "finaldamage" in property_id.casefold()
            or "damageupfinal" in property_id.casefold()
        )
        independent_terms = _source_terms(
            baseline,
            projection,
            independent_ids,
        )
        stack_factors = (() if not evidence.state_multiplier_label else (_factor(
            "state_coefficient",
            evidence.state_multiplier_label,
            stack_coefficient,
            evidence.state_multiplier_basis,
            formula="min(当前同类状态层数, 10) × 每层系数 1",
        ),))
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
                    "L / ([DefBase × (1 + DefUp) + DefAdd] / 6 × "
                    "(1 - 防御穿透) × (1 - 防御降低) + L)，L=角色等级+100"
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
                "命中时结构化最终伤害 Buff",
                formula="各最终伤害提升独立相乘",
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
        crit_error = (
            None if critical is None else replay_error_percent(hit.damage, critical)
        )
        best_is_crit = bool(
            crit_error is not None and crit_error < noncrit_error
        )
        selected = critical if best_is_crit and critical is not None else non_critical
        error = noncrit_error if crit_error is None else min(noncrit_error, crit_error)
        signed_error = replay_signed_error_percent(hit.damage, selected)
        expected = (
            None if critical_rate is None
            else non_critical if critical is None
            else non_critical * (1.0 - critical_rate) + critical * critical_rate
        )
        corrected_expected = (
            expected * hit.damage / selected
            if expected is not None and selected > 0.0
            else None
        )
        separation = (
            0.0 if crit_error is None else abs(noncrit_error - crit_error)
        )
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
            missing.append(
                "目标实例仍未识别；防御与抗性来自本场最大生命指纹唯一命中、"
                "且候选敌人共享同一乘区的静态配置"
            )
        if evidence.state_multiplier_label:
            missing.append(
                f"当前结算层数由逐击正向重放（置信度{evidence.state_confidence}），"
                "待运行时目标 Buff 层数覆盖"
            )
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
