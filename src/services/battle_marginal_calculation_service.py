# 以真实逐击为锚点，在逐击动态 Buff 面板上计算属性边际。
"""Battle-report marginal calculations with safe per-hit Buff projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from src.domain.battle_counterfactual import BattleMarginalResult
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
    QuantificationStatus,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_counterfactual_projection_support import (
    HitProjection,
    VitalProjection,
    vital_projections,
)
from src.services.battle_damage_composition_service import (
    BattleDamageCompositionService,
    classify_battle_hit_channel,
)
from src.services.battle_fixed_critical_ratio_service import (
    continuous_direct_attribute,
    is_continuous_direct_hit,
    is_fixed_half_critical_hit,
)
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)
from src.services.battle_marginal_calculation_support import (
    ATTRIBUTE_ELEMENT_PROPERTY as _ATTRIBUTE_ELEMENT_PROPERTY,
    DAMAGE_PENETRATION_PROPERTY as _DAMAGE_PENETRATION_PROPERTY,
    ELEMENT_PROPERTIES as _ELEMENT_PROPERTIES,
    MARGINAL_LABELS as _MARGINAL_LABELS,
    WEAVE_SOURCE_PROPERTIES as _WEAVE_SOURCE_PROPERTIES,
    default_marginal_units,
    marginal_assumption,
    quantify_marginal,
)


class BattleMarginalCalculationService:
    """Calculate role margins without mistaking inferred Buffs for raw facts."""

    @staticmethod
    def default_units(
        baseline: BattleCharacterBaseline,
        *,
        hits: Sequence[BattleAnalysisHit] = (),
        replays: Mapping[str, BattleHitReplayResult] | None = None,
    ) -> dict[str, float]:
        return default_marginal_units(
            baseline,
            hits=hits,
            replays=replays,
            topple_ratio=BattleMarginalCalculationService._topple_ratio,
        )

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
        max_hp_events = tuple(getattr(analysis, "max_hp_events", ()))
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
        fallback_role_damage = sum(hit.damage for hit in role_hits) + derived_damage
        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=(),
            hits=outgoing_hits,
            hit_replays=analysis.hit_replays,
            max_hp_events=max_hp_events,
            segment_total_damage=max(0.0, float(analysis.effective_damage)),
            role_identities=tuple(sorted({
                int(hit.character_id): hit.character_name
                for hit in outgoing_hits
                if hit.character_id is not None
            }.items())),
        )
        observed_role_damage = next(
            (
                role.total_damage
                for role in composition.roles
                if role.character_id == character_id
            ),
            fallback_role_damage,
        )
        comparison = analysis.build_counterfactual
        comparison_hits = {
            row.event_id: row
            for row in (() if comparison is None else comparison.hits)
        }

        def anchor_damage(hit: BattleAnalysisHit) -> float:
            row = comparison_hits.get(hit.event_id)
            if row is None:
                return max(0.0, float(hit.damage))
            return cls._counterfactual_projection(
                row,
                fallback=max(0.0, float(hit.damage)),
            )

        def anchor_quantification(
            hit: BattleAnalysisHit,
        ) -> BattleCounterfactualRatio | None:
            row = comparison_hits.get(hit.event_id)
            return None if row is None else getattr(row, "quantification", None)

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
            else cls._counterfactual_projection(
                comparison_role,
                fallback=observed_role_damage,
            )
        )
        team_damage = (
            max(0.0, float(analysis.effective_damage))
            if comparison is None
            else cls._counterfactual_projection(
                comparison,
                fallback=max(0.0, float(analysis.effective_damage)),
            )
        )
        results = []
        for property_id, raw_unit in units.items():
            unit = float(raw_unit)
            changed = dict(edited)
            changed[property_id] = changed.get(property_id, 0.0) + unit
            edited_baseline = cls._baseline_with_values(baseline, edited)
            changed_baseline = cls._baseline_with_values(baseline, changed)
            relevant_hits = tuple(
                hit
                for hit in role_hits
                if cls._supports(
                    property_id,
                    hit,
                    replay=replays.get(hit.event_id),
                    character_id=character_id,
                )
            )
            hit_ratios: dict[str, BattleCounterfactualRatio] = {}
            for hit in relevant_hits:
                formula_hit = cls._attack_formula_hit(
                    property_id,
                    hit,
                    role_hits,
                )
                if formula_hit is None:
                    hit_ratios[hit.event_id] = cls._missing_linked_source_ratio()
                else:
                    hit_ratios[hit.event_id] = (
                        BattleHitCounterfactualRatioService.compare(
                            hit=formula_hit,
                            original_baseline=edited_baseline,
                            candidate_baseline=changed_baseline,
                            original_projection=projections[formula_hit.event_id],
                            candidate_projection=projections[formula_hit.event_id],
                            original_replay=replays.get(formula_hit.event_id),
                            target_condition=target_conditions[formula_hit.event_id],
                        )
                    )
                hit_ratios[hit.event_id] = cls._inherit_anchor_status(
                    hit_ratios[hit.event_id],
                    anchor_quantification(hit),
                )
            baseline_hit_damage_by_event = {
                hit.event_id: anchor_damage(hit)
                for hit in role_hits
            }
            projected_hits = {
                hit.event_id: HitProjection(
                    hit=hit,
                    predicted_damage=(
                        anchor_damage(hit)
                        if hit_ratios[hit.event_id].quantified_ratio is None
                        else anchor_damage(hit)
                        * float(hit_ratios[hit.event_id].quantified_ratio)
                    ),
                    quantification=hit_ratios[hit.event_id],
                )
                for hit in relevant_hits
            }
            comparison_vital = {
                row.event_id: row
                for row in (
                    () if comparison is None
                    else getattr(comparison, "vital_events", ())
                )
            }
            baseline_vital_states = {
                event_id: (*row.candidate_state, cls._counterfactual_projection(
                    row,
                    fallback=0.0,
                ))
                for event_id, row in comparison_vital.items()
                if getattr(row, "candidate_state", None) is not None
            }
            linked_vital: list[VitalProjection] = []
            for row in vital_projections(
                analysis,
                projected_hits,
                baseline_hit_damage_by_event,
                baseline_vital_states,
            ):
                if row.character_id != character_id or row.status == "not_applicable":
                    continue
                current_vital = comparison_vital.get(row.event_id)
                current_vital_damage = (
                    row.baseline_damage
                    if current_vital is None
                    else cls._counterfactual_projection(
                        current_vital,
                        fallback=row.baseline_damage,
                    )
                )
                ratio = (
                    row.predicted_damage / row.baseline_damage
                    if row.baseline_damage > 0.0
                    else 1.0
                )
                projected_vital = replace(
                    row,
                    baseline_damage=current_vital_damage,
                    predicted_damage=current_vital_damage * ratio,
                )
                current_vital_quantification = (
                    None
                    if current_vital is None
                    else getattr(current_vital, "quantification", None)
                )
                if current_vital_quantification is not None and not (
                    current_vital_quantification.status == "complete"
                    and getattr(current_vital, "candidate_state", None) is not None
                ):
                    projected_vital = cls._inherit_vital_anchor_status(
                        projected_vital,
                        current_vital_quantification,
                    )
                linked_vital.append(projected_vital)
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
            topple_ratios = {
                hit.event_id: ratio
                for hit in topple_hits
                if (
                    ratio := cls._topple_ratio(
                        replays.get(hit.event_id),
                        character_id=character_id,
                        unit=unit,
                    )
                ) is not None
            }
            quantification, known_increment = quantify_marginal(
                role_damage=role_damage,
                relevant_hits=relevant_hits,
                hit_ratios=hit_ratios,
                vital_projections=linked_vital,
                topple_hits=topple_hits,
                topple_ratios=topple_ratios,
                replays=replays,
                anchor_damage=anchor_damage,
                anchor_quantification=anchor_quantification,
                character_id=character_id,
            )
            baseline_damage = quantification.basis_damage
            known_projection_damage = (
                None
                if known_increment is None
                else baseline_damage + known_increment
            )
            quantified_role_gain = (
                None
                if known_increment is None
                or cls._denominator_status(comparison_role) == "unavailable"
                else (
                    known_increment / baseline_damage * 100.0
                    if baseline_damage
                    else 0.0
                )
            )
            quantified_team_gain = (
                None
                if known_increment is None
                or cls._denominator_status(comparison) == "unavailable"
                else known_increment / team_damage * 100.0 if team_damage else 0.0
            )
            full_role_gain = (
                quantified_role_gain
                if quantification.status in {"complete", "not_applicable"}
                and cls._denominator_status(comparison_role)
                in {"complete", "not_applicable"}
                else None
            )
            full_team_gain = (
                quantified_team_gain
                if quantification.status in {"complete", "not_applicable"}
                and cls._denominator_status(comparison)
                in {"complete", "not_applicable"}
                else None
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
                known_projection_damage=known_projection_damage,
                quantified_role_gain_percent=quantified_role_gain,
                quantified_team_gain_percent=quantified_team_gain,
                full_role_gain_percent=full_role_gain,
                full_team_gain_percent=full_team_gain,
                damage_share_percent=(
                    min(100.0, baseline_damage / team_damage * 100.0)
                    if team_damage else 0.0
                ),
                quantification=quantification,
                assumption=marginal_assumption(
                    property_id,
                    quantification.status,
                    applied_count=len(applied_intervals),
                    excluded_count=len(excluded_intervals),
                    critical_policies=tuple(
                        "fixed" if is_fixed_half_critical_hit(hit)
                        else cls._critical_policy(replays.get(hit.event_id))
                        for hit in role_hits
                        if is_fixed_half_critical_hit(hit) or hit.classification
                        in {"direct", "direct_follow_up", "weave"}
                    ),
                    ),
                role_denominator_status=cls._denominator_status(comparison_role),
                team_denominator_status=cls._denominator_status(comparison),
            ))
        return tuple(sorted(
            results,
            key=lambda row: (
                row.quantified_role_gain_percent
                if row.quantified_role_gain_percent is not None
                else float("-inf")
            ),
            reverse=True,
        ))

    @staticmethod
    def _counterfactual_projection(row: object, *, fallback: float) -> float:
        """Use only complete or known-component projections, never heuristics."""

        for field_name in ("candidate_damage", "known_projection_damage"):
            value = getattr(row, field_name, None)
            if value is not None:
                return max(0.0, float(value))
        return max(0.0, float(fallback))

    @staticmethod
    def _denominator_status(row: object | None) -> QuantificationStatus:
        if row is None:
            return "complete"
        if isinstance(row, BattleCounterfactualRatio):
            return row.status
        quantification = getattr(row, "quantification", None)
        return getattr(quantification, "status", "complete")

    @staticmethod
    def _inherit_anchor_status(
        ratio: BattleCounterfactualRatio,
        anchor: BattleCounterfactualRatio | None,
    ) -> BattleCounterfactualRatio:
        if (
            anchor is None
            or anchor.status in {"complete", "not_applicable"}
            or ratio.status == "not_applicable"
        ):
            return ratio
        gaps = tuple(dict.fromkeys((*anchor.gaps, *ratio.gaps)))
        if anchor.status == "unavailable" or ratio.status == "unavailable":
            return BattleCounterfactualRatio.unavailable(
                method="current_anchor_unavailable",
                confidence="低",
                dependency_scope="mechanic_specific",
                cancelled_dimension_ids=ratio.cancelled_dimension_ids,
                gaps=gaps or (BattleQuantificationGap(
                    code="current_anchor_unavailable",
                    dimension_id="current_hit_projection",
                    dependency_scope="mechanic_specific",
                    property_ids=(),
                    explanation="当前有效配置的逐击锚点无法完整投影。",
                ),),
                explanation="属性单位不能乘回原战逐击，当前锚点不可用。",
            )
        included = ratio.included_dimension_ids or ("current_hit_projection",)
        return BattleCounterfactualRatio.partial(
            float(ratio.quantified_ratio),
            method=ratio.method,
            confidence=ratio.confidence,
            dependency_scope=ratio.dependency_scope,
            included_dimension_ids=included,
            cancelled_dimension_ids=ratio.cancelled_dimension_ids,
            gaps=gaps,
            explanation=(
                f"{ratio.explanation} 当前有效配置锚点仅有已量化投影。"
            ),
        )

    @staticmethod
    def _inherit_vital_anchor_status(
        projection: VitalProjection,
        anchor: BattleCounterfactualRatio,
    ) -> VitalProjection:
        if projection.status == "not_applicable" or anchor.status == "not_applicable":
            return projection
        gaps = tuple(dict.fromkeys((*anchor.gaps, *projection.gaps)))
        if anchor.status == "unavailable" or projection.status == "unavailable":
            return replace(
                projection,
                predicted_damage=projection.baseline_damage,
                status="unavailable",
                gaps=gaps,
            )
        if anchor.status == "complete" and projection.status == "complete":
            gaps = tuple((*gaps, BattleQuantificationGap(
                code="current_vital_sequence_state_unavailable",
                dimension_id="target_current_hp",
                dependency_scope="mechanic_specific",
                property_ids=(),
                explanation=(
                    "当前配置只保存了生命结算锚点，未保存可供下一属性单位继续"
                    "顺推的候选当前生命状态。"
                ),
            )))
        return replace(projection, status="partial", gaps=gaps)

    @staticmethod
    def _baseline_with_values(
        baseline: BattleCharacterBaseline,
        values: Mapping[str, float],
    ) -> BattleCharacterBaseline:
        existing = {row.property_id: row for row in baseline.stats}
        stats = tuple(
            replace(row, value=float(values.get(row.property_id, row.value)))
            for row in baseline.stats
        )
        additions = tuple(
            BattleCharacterStat(
                property_id=property_id,
                label=property_id,
                value=float(value),
                is_percent=property_id not in {
                    "AtkBase", "AtkAdd", "HPMaxBase", "HPMaxAdd",
                    "DefBase", "DefAdd", "MagBase", "UnbalIntensityBase",
                },
            )
            for property_id, value in sorted(values.items())
            if property_id not in existing
        )
        return replace(baseline, stats=tuple((*stats, *additions)))

    @staticmethod
    def _supports(
        property_id: str,
        hit: BattleAnalysisHit,
        *,
        replay: BattleHitReplayResult | None,
        character_id: int,
    ) -> bool:
        channel, _label = classify_battle_hit_channel(hit)
        if channel in {
            "other_reflected_projectile",
            "special_fadia_shared_damage",
        }:
            return property_id != "UnbalIntensityBase"
        kuhara_formula = (
            BattleHitCounterfactualRatioService.is_kuhara_formula_hit(hit)
        )
        continuous_direct = (
            is_continuous_direct_hit(hit)
        )
        fixed_half_critical = (
            is_fixed_half_critical_hit(hit)
        )
        if property_id == "UnbalIntensityBase":
            return False
        if property_id in {"CritBase", "CritDamageBase"}:
            if fixed_half_critical:
                return property_id == "CritDamageBase"
            if replay is None and hit.classification == "weave":
                return False
            policy = BattleMarginalCalculationService._critical_policy(replay)
            if property_id == "CritBase":
                return policy in {"character", "unknown"}
            return policy in {"character", "fixed", "unknown"}
        if property_id == "MagBase":
            return BattleHitCounterfactualRatioService.supports_ring_strength(
                hit,
                replay,
            )
        reaction_attribute = {
            "reaction_scorch": "incantation",
            "reaction_nova": "psyche",
        }.get(
            channel,
            str(
                getattr(replay, "formula_damage_attribute", "") or ""
            ).casefold(),
        )
        if property_id == "DefIgnore":
            return (
                channel in {"reaction_creation", "reaction_scorch"}
                or kuhara_formula
                or continuous_direct
                or hit.classification in {"direct", "direct_follow_up", "weave"}
            )
        if property_id in _DAMAGE_PENETRATION_PROPERTY.values():
            expected_attribute = next(
                damage_type
                for damage_type, candidate in _DAMAGE_PENETRATION_PROPERTY.items()
                if candidate == property_id
            )
            return (
                (
                    (
                        "nature"
                        if kuhara_formula
                        else reaction_attribute
                        or continuous_direct_attribute(hit)
                    )
                    or hit.damage_attribute.casefold()
                ) == expected_attribute
                and (
                    channel in {
                        "reaction_creation", "reaction_scorch", "reaction_nova",
                    }
                    or
                    kuhara_formula
                    or continuous_direct
                    or hit.classification in {"direct", "direct_follow_up", "weave"}
                )
            )
        if property_id in _ELEMENT_PROPERTIES:
            formal_attribute = (
                "nature"
                if kuhara_formula
                else continuous_direct_attribute(hit)
                or hit.damage_attribute.casefold()
            )
            return (
                _ATTRIBUTE_ELEMENT_PROPERTY.get(formal_attribute) == property_id
                and (
                    kuhara_formula
                    or continuous_direct
                    or hit.classification in {"direct", "direct_follow_up", "weave"}
                )
            )
        return kuhara_formula or continuous_direct or hit.classification in {
            "direct", "direct_follow_up", "weave",
        }

    @staticmethod
    def _attack_formula_hit(
        property_id: str,
        hit: BattleAnalysisHit,
        role_hits: Sequence[BattleAnalysisHit],
    ) -> BattleAnalysisHit | None:
        """Route source-consuming weave fields through the paired direct hit."""

        if property_id not in _WEAVE_SOURCE_PROPERTIES or hit.classification != "weave":
            return hit
        return next(
            (
                row
                for row in role_hits
                if row.sequence == hit.sequence
                and row.target_id == hit.target_id
                and row.direction == hit.direction
                and not row.is_follow_up
                and row.classification == "direct"
            ),
            None,
        )

    @staticmethod
    def _missing_linked_source_ratio() -> BattleCounterfactualRatio:
        gap = BattleQuantificationGap(
            code="linked_source_hit_missing",
            dimension_id="weave_trigger_direct_hit",
            dependency_scope="mechanic_specific",
            property_ids=tuple(sorted(_WEAVE_SOURCE_PROPERTIES)),
            explanation="覆纹缺少同序列、同目标、同方向的触发直伤。",
        )
        return BattleCounterfactualRatio.unavailable(
            method="weave_source_unavailable",
            confidence="低",
            dependency_scope="mechanic_specific",
            cancelled_dimension_ids=(),
            gaps=(gap,),
            explanation="无法安全联动覆纹的触发直伤来源。",
        )

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
