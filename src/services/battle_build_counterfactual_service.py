# 在同一真实逐击轴上比较原始配置与修改配置的整队期望伤害。
"""Fixed-axis build comparison with quantified and heuristic projections."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from statistics import median

from src.domain.battle_counterfactual import (
    BattleBuildCounterfactual,
    BattleBuildHitCounterfactual,
    BattleBuildVitalCounterfactual,
)
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleRangeRoleSummary,
)
from src.services.battle_damage_composition_service import (
    BattleDamageCompositionService,
)
from src.services.battle_build_quantification_service import (
    BattleBuildQuantificationService,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_interval_index import BattleBuffIntervalIndex
from src.services.battle_daffodill_marginal_service import (
    DAFFODILL_EFFECT_FIVE_METHOD,
    BattleDaffodillMarginalService,
)
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)
from src.services.battle_replay_formula_ratio_service import (
    paired_replay_formula,
    replay_formula_value,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_build_role_counterfactual_support import (
    build_role_counterfactuals,
)
from src.services.battle_build_vital_support import (
    linked_lacrimosa_vital_hits,
    projected_hit_damage,
    safe_ratio,
)
BUILD_COUNTERFACTUAL_MODEL_VERSION = "battle-build-counterfactual-v5"
_STRUCTURED_METHODS = {
    "structured_expected",
    "structured_selected",
    DAFFODILL_EFFECT_FIVE_METHOD,
}
_STRUCTURED_VITAL_METHODS = {
    "linked_source_hit_ratio", "linked_source_hit_ratio_sequential_hp",
    "fadia_source_max_hp_ratio", "mechanic_disabled",
}
class BattleBuildCounterfactualService:
    """Compare two independently replayed builds while preserving the real axis."""

    @classmethod
    def compare(
        cls,
        *,
        original: BattleAnalysisSnapshot,
        candidate: BattleAnalysisSnapshot,
        progress_callback: BattleAnalysisProgressCallback | None = None,
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
        original_baselines = {
            row.character_id: row for row in original.baselines
        }
        candidate_baselines = {
            row.character_id: row for row in candidate.baselines
        }
        build_inputs_unchanged = (
            BattleDaffodillMarginalService.direct_formula_inputs_unchanged(
                original, candidate,
            )
        )
        original_interval_index = BattleBuffIntervalIndex(
            BattleDaffodillMarginalService.direct_formula_intervals(original)
        )
        candidate_interval_index = BattleBuffIntervalIndex(
            BattleDaffodillMarginalService.direct_formula_intervals(candidate)
        )
        routed_by_target: dict[tuple[str, str], BattleAnalysisSnapshot] = {}
        total_hits = len(original_hits)
        report_battle_analysis_progress(
            progress_callback,
            phase="build_compare",
            message="正在逐击汇总修改副本与原始配置差异…",
            completed=0,
            total=total_hits,
        )

        projected_hits: list[BattleBuildHitCounterfactual] = []
        for ordinal, (event_id, hit) in enumerate(
            original_hits.items(),
            start=1,
        ):
            formula_pair = paired_replay_formula(
                original_replays.get(event_id),
                candidate_replays.get(event_id),
            )
            target_key = (hit.scope_half.casefold(), hit.target_id)
            routed = routed_by_target.get(target_key)
            if routed is None:
                routed = BattleTargetInstanceMappingService.analysis_for_hit(
                    original,
                    hit,
                )
                routed_by_target[target_key] = routed
            quantification = BattleHitCounterfactualRatioService.compare(
                hit=hit,
                original_baseline=original_baselines.get(hit.character_id),
                candidate_baseline=candidate_baselines.get(hit.character_id),
                original_projection=BattleBuffAttributeProjectionService.project_hit(
                    hit,
                    original_interval_index,
                ),
                candidate_projection=BattleBuffAttributeProjectionService.project_hit(
                    candidate_hits.get(event_id, hit),
                    candidate_interval_index,
                ),
                original_replay=original_replays.get(event_id),
                candidate_replay=candidate_replays.get(event_id),
                target_condition=routed.target_condition,
            )
            if (
                quantification.status == "unavailable"
                and build_inputs_unchanged
                and candidate_hits.get(event_id) == hit
            ):
                quantification = BattleCounterfactualRatio.not_applicable(
                    method="frozen_hit_not_applicable",
                    dependency_scope="mechanic_specific",
                    cancelled_dimension_ids=("frozen_hit",),
                    explanation=(
                        "原始与候选的冻结逐击、角色面板及重放输入均未变化。"
                    ),
                )
            if (
                quantification.status == "not_applicable"
                and not build_inputs_unchanged
                and (
                    event_id not in original_replays
                    or event_id not in candidate_replays
                )
            ):
                quantification = cls._unavailable_ratio(
                    code="candidate_formula_dependency_unresolved",
                    explanation=(
                        "候选配置已变化，但本击缺少成对公式，不能证明与变化无关。"
                    ),
                )
            ratio = quantification.quantified_ratio
            known_projection = (
                None if ratio is None else float(hit.damage) * ratio
            )
            candidate_damage = (
                known_projection
                if quantification.status in {"complete", "not_applicable"}
                else None
            )
            projected_hits.append(BattleBuildHitCounterfactual(
                event_id=event_id,
                character_id=hit.character_id,
                character_name=hit.character_name,
                skill_name=hit.skill_name,
                damage_name=hit.damage_name,
                baseline_damage=float(hit.damage),
                known_projection_damage=known_projection,
                candidate_damage=candidate_damage,
                heuristic_projection_damage=None,
                quantification=quantification,
                baseline_formula_damage=(
                    formula_pair.baseline_damage
                    if formula_pair is not None
                    else replay_formula_value(original_replays.get(event_id))[0]
                ),
                candidate_formula_damage=(
                    formula_pair.candidate_damage
                    if formula_pair is not None
                    else replay_formula_value(candidate_replays.get(event_id))[0]
                ),
            ))
            if ordinal == total_hits or ordinal % 64 == 0:
                report_battle_analysis_progress(
                    progress_callback,
                    phase="build_compare",
                    message="正在逐击汇总修改副本与原始配置差异…",
                    completed=ordinal,
                    total=total_hits,
                )
        skill_ratios, type_ratios, role_ratios = cls._ratio_catalogs(
            original_hits,
            projected_hits,
        )
        projected_hits = [
            cls._with_heuristic(
                row,
                original_hits[row.event_id],
                skill_ratios,
                type_ratios,
                role_ratios,
            )
            for row in projected_hits
        ]
        projected_hits.extend(BattleDaffodillMarginalService.derived_rows(
            original=original,
            candidate=candidate,
        ))

        projected_vital_events = cls._vital_events(
            original=original,
            candidate=candidate,
            projected_hits=projected_hits,
            original_hits=original_hits,
            original_baselines=original_baselines,
            candidate_baselines=candidate_baselines,
        )
        hit_baseline = sum(row.baseline_damage for row in projected_hits)
        vital_baseline = sum(row.baseline_damage for row in projected_vital_events)
        fixed_derived_damage = max(
            0.0,
            original.effective_damage - hit_baseline - vital_baseline,
        )
        baseline_damage = hit_baseline + vital_baseline + fixed_derived_damage
        fixed_derived_unchanged = build_inputs_unchanged
        quantification = BattleBuildQuantificationService.aggregate(
            rows=(*projected_hits, *projected_vital_events),
            fixed_damage=fixed_derived_damage,
            fixed_unchanged=fixed_derived_unchanged,
        )
        known_projection_damage = (
            None
            if quantification.quantified_increment is None
            else baseline_damage + quantification.quantified_increment
        )
        candidate_damage = (
            known_projection_damage
            if quantification.status in {"complete", "not_applicable"}
            else None
        )
        heuristic_rows = (*projected_hits, *projected_vital_events)
        heuristic_projection_damage = (
            sum(BattleBuildQuantificationService.display_projection(row) for row in heuristic_rows)
            + fixed_derived_damage
            if any(
                row.heuristic_projection_damage is not None
                for row in heuristic_rows
            )
            else None
        )
        structured_damage = sum(
            row.baseline_damage
            for row in projected_hits
            if row.quantification.method in _STRUCTURED_METHODS
        )
        structured_damage += sum(
            row.baseline_damage
            for row in projected_vital_events
            if row.quantification.method in _STRUCTURED_VITAL_METHODS
        )
        duration = max(0.001, float(original.duration_seconds))
        known_gain = (
            None
            if known_projection_damage is None or not baseline_damage
            else (known_projection_damage / baseline_damage - 1.0) * 100.0
        )
        gain = (
            known_gain if candidate_damage is not None else None
        )
        role_rows = build_role_counterfactuals(
            original,
            projected_hits,
            projected_vital_events,
            fixed_derived_unchanged=fixed_derived_unchanged,
            structured_methods=_STRUCTURED_METHODS,
            structured_vital_methods=_STRUCTURED_VITAL_METHODS,
        )
        assumptions = (
            "固定原战报动作、逐击、目标与时段，只替换角色配置后重放。",
            "原击已识别暴击分支时，候选沿用同一分支；分支不唯一但暴击策略已知时才使用期望公式。",
            "每击按半场和目标实例消费冻结画像；身份未知但画像等价仍可完整量化，未变化的未知共同乘区允许相消。",
            "目标敏感 peer 不跨半场或目标；peer 只形成独立 heuristic，不进入已量化收益。",
            "未量化逐击只以原始值保留固定轴，不冒充候选值或精确零收益。",
        )
        projected_damage_by_event = {
            row.event_id: BattleBuildQuantificationService.known_or_source(row)
            for row in projected_hits
        }
        composition_hits = tuple(
            replace(hit, damage=projected_damage_by_event.get(hit.event_id, hit.damage))
            for hit in original.hits
        )
        derived_rows = tuple(
            row for row in projected_hits
            if row.quantification.method == DAFFODILL_EFFECT_FIVE_METHOD
        )
        composition_hits = tuple((
            *composition_hits,
            *(
                BattleDaffodillMarginalService.composition_hit(row, candidate_hits)
                for row in derived_rows
            ),
        ))
        composition_total_damage = (
            baseline_damage
            if known_projection_damage is None
            else known_projection_damage
        )
        composition_roles = tuple(
            BattleRangeRoleSummary(
                character_id=row.character_id,
                character_name=row.character_name,
                hits=sum(
                    1 for hit in projected_hits if hit.character_id == row.character_id
                ),
                damage=BattleBuildQuantificationService.known_or_source(row),
                dps=BattleBuildQuantificationService.known_or_source(row) / duration,
                share_percent=(
                    BattleBuildQuantificationService.known_or_source(row)
                    / composition_total_damage
                    * 100.0
                    if composition_total_damage
                    else 0.0
                ),
            )
            for row in role_rows
        )
        vital_by_event = {row.event_id: row for row in projected_vital_events}
        composition_vital_events = tuple(
            replace(
                event,
                effective_hp_loss=BattleBuildQuantificationService.known_or_source(
                    vital_by_event[event.event_id]
                ),
            )
            if event.event_id in vital_by_event
            else event
            for event in original.max_hp_events
        )
        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=composition_roles,
            hits=composition_hits,
            max_hp_events=composition_vital_events,
            hit_replays=tuple((
                *candidate.hit_replays,
                *(
                    BattleDaffodillMarginalService.composition_replay(
                        row, candidate_hits, candidate_replays,
                    )
                    for row in derived_rows
                ),
            )),
            role_identities=tuple(
                (row.character_id, row.character_name)
                for row in candidate.baselines
            ),
            segment_total_damage=composition_total_damage,
        )
        return BattleBuildCounterfactual(
            model_version=BUILD_COUNTERFACTUAL_MODEL_VERSION,
            baseline_damage=baseline_damage,
            known_projection_damage=known_projection_damage,
            candidate_damage=candidate_damage,
            heuristic_projection_damage=heuristic_projection_damage,
            known_gain_percent=known_gain,
            gain_percent=gain,
            baseline_dps=baseline_damage / duration,
            known_projection_dps=(
                None
                if known_projection_damage is None
                else known_projection_damage / duration
            ),
            candidate_dps=(
                None if candidate_damage is None else candidate_damage / duration
            ),
            heuristic_projection_dps=(
                None
                if heuristic_projection_damage is None
                else heuristic_projection_damage / duration
            ),
            quantification=quantification,
            structured_damage=structured_damage,
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
        reduction_delta_by_target: dict[tuple[str, str], float] = {}
        effective_delta_by_target: dict[tuple[str, str], float] = {}
        result = []
        for event in sorted(
            original.max_hp_events,
            key=lambda row: (row.observed_at_us, row.event_id),
        ):
            baseline_damage = max(0.0, float(event.effective_hp_loss))
            quantification = cls._unavailable_ratio(
                code="mechanic_dependency_unresolved",
                explanation="缺少可安全联动的来源公式，原值只保留为轴上事实。",
            )
            heuristic_projection = None
            sequential_projection: float | None = None
            candidate_state: tuple[float, float, float] | None = None
            candidate_event = candidate_vital.get(event.event_id)
            if event.mechanic_kind == "lacrimosa_nightmare_awaken_5":
                if (
                    candidate_event is None
                    or candidate_event.mechanic_kind != event.mechanic_kind
                ):
                    quantification = BattleCounterfactualRatio.complete(
                        0.0,
                        method="mechanic_disabled",
                        confidence="高",
                        dependency_scope="mechanic_specific",
                        included_dimension_ids=("mechanic_activation",),
                        explanation="候选配置未激活安魂曲五觉，本次生命上限结算归零。",
                    )
                else:
                    linked = linked_lacrimosa_vital_hits(
                        event.evidence_event_ids,
                        event.source_character_id,
                        event.target_id,
                        event.scope_half,
                        projected_by_event,
                        original_hits,
                    )
                    quantification, heuristic_projection = cls._linked_ratio(
                        linked,
                        baseline_damage,
                    )
                source_ratio = quantification.quantified_ratio
                if source_ratio is not None:
                    prior_rows = tuple(
                        row
                        for row in projected_hits
                        if row.event_id in original_hits
                        and original_hits[row.event_id].target_id == event.target_id
                        and original_hits[row.event_id].scope_half == event.scope_half
                        and (
                            original_hits[row.event_id].relative_time_us
                            < event.observed_at_us
                            or row.event_id in event.evidence_event_ids
                        )
                    )
                    unresolved = tuple(
                        row
                        for row in prior_rows
                        if row.quantification.status == "unavailable"
                    )
                    if unresolved:
                        continuity_gap = BattleQuantificationGap(
                            code="max_hp_axis_continuity_unavailable",
                            dimension_id="target_current_hp",
                            dependency_scope="mechanic_specific",
                            property_ids=(),
                            explanation=(
                                "安魂曲五觉前存在无法投影的同目标逐击，"
                                "无法完整顺推候选当前生命。"
                            ),
                        )
                        if quantification.status in {"complete", "partial"}:
                            quantification = BattleCounterfactualRatio.partial(
                                source_ratio,
                                method="linked_source_hit_ratio_sequential_hp",
                                confidence="中",
                                dependency_scope="mechanic_specific",
                                included_dimension_ids=("nightmare_source",),
                                cancelled_dimension_ids=(),
                                gaps=tuple(dict.fromkeys((
                                    *quantification.gaps,
                                    continuity_gap,
                                ))),
                                explanation=(
                                    "噩梦来源倍率可量化，但固定轴当前生命连续性不完整。"
                                ),
                            )
                    if not unresolved:
                        target_key = (event.scope_half, event.target_id)
                        hit_delta = sum(
                            projected_hit_damage(row)
                            - float(original_hits[row.event_id].damage)
                            for row in prior_rows
                        )
                        current_max = max(
                            0.0,
                            float(event.old_max_hp)
                            - reduction_delta_by_target.get(target_key, 0.0),
                        )
                        current_hp = max(
                            0.0,
                            min(
                                current_max,
                                float(event.hp_before_settlement)
                                - hit_delta
                                - effective_delta_by_target.get(target_key, 0.0),
                            ),
                        )
                        changed_reduction = max(
                            0.0,
                            float(event.max_hp_reduction) * source_ratio,
                        )
                        sequential_projection = (
                            current_hp
                            * min(1.0, changed_reduction / current_max)
                            if current_max > 0.0
                            else 0.0
                        )
                        candidate_state = (current_max, current_hp, changed_reduction)
                        if quantification.method != "mechanic_disabled":
                            quantification = replace(
                                quantification,
                                method="linked_source_hit_ratio_sequential_hp",
                                explanation=(
                                    "按正式噩梦来源倍率改变生命上限削减，"
                                    "并沿固定逐击轴顺推候选当前生命与当前上限。"
                                ),
                            )
                        reduction_delta_by_target[target_key] = (
                            reduction_delta_by_target.get(target_key, 0.0)
                            + changed_reduction
                            - float(event.max_hp_reduction)
                        )
                        effective_delta_by_target[target_key] = (
                            effective_delta_by_target.get(target_key, 0.0)
                            + sequential_projection
                            - baseline_damage
                        )
            elif (
                event.mechanic_kind == "fadia_dark_star_max_hp_transfer"
                and event.source_character_id is not None
            ):
                original_hp = original_baselines.get(event.source_character_id)
                candidate_hp = candidate_baselines.get(event.source_character_id)
                hp_ratio = safe_ratio(
                    float(candidate_hp.source_max_hp or 0.0) if candidate_hp else 0.0,
                    float(original_hp.source_max_hp or 0.0) if original_hp else 0.0,
                )
                if hp_ratio is not None:
                    quantification = BattleCounterfactualRatio.complete(
                        hp_ratio,
                        method="fadia_source_max_hp_ratio",
                        confidence="中",
                        dependency_scope="mechanic_specific",
                        included_dimension_ids=("source_max_hp",),
                        explanation=(
                            "按候选/原始法帝娅冻结来源当前 MAXHP 比"
                            "联动本次被动结算。"
                        ),
                    )
            ratio = quantification.quantified_ratio
            known_projection = (
                sequential_projection
                if sequential_projection is not None
                else (None if ratio is None else baseline_damage * ratio)
            )
            result.append(BattleBuildVitalCounterfactual(
                event_id=event.event_id,
                character_id=event.source_character_id,
                character_name=event.source_character_name,
                mechanic_kind=event.mechanic_kind,
                mechanic_name=event.mechanic_name,
                baseline_damage=baseline_damage,
                known_projection_damage=known_projection,
                candidate_damage=(
                    known_projection
                    if quantification.status in {"complete", "not_applicable"}
                    else None
                ),
                heuristic_projection_damage=heuristic_projection,
                quantification=quantification,
                candidate_state=candidate_state,
            ))
        return tuple(result)

    @classmethod
    def _ratio_catalogs(
        cls,
        hits: Mapping[str, BattleAnalysisHit],
        rows: Sequence[BattleBuildHitCounterfactual],
    ) -> tuple[
        dict[tuple[object, ...], float],
        dict[tuple[object, ...], float],
        dict[tuple[object, ...], float],
    ]:
        skill_values: dict[tuple[object, ...], list[float]] = defaultdict(list)
        type_values: dict[tuple[object, ...], list[float]] = defaultdict(list)
        role_values: dict[tuple[object, ...], list[float]] = defaultdict(list)
        for row in rows:
            ratio = row.quantification.quantified_ratio
            if (
                row.quantification.status != "complete"
                or ratio is None
                or row.event_id not in hits
            ):
                continue
            hit = hits[row.event_id]
            skill_values[cls._skill_key(hit)].append(ratio)
            type_values[cls._type_key(hit)].append(ratio)
            role_values[cls._role_key(hit)].append(ratio)
        return (
            {key: median(values) for key, values in skill_values.items()},
            {key: median(values) for key, values in type_values.items()},
            {key: median(values) for key, values in role_values.items()},
        )

    @classmethod
    def _with_heuristic(
        cls,
        row: BattleBuildHitCounterfactual,
        hit: BattleAnalysisHit,
        skill_ratios: Mapping[tuple[object, ...], float],
        type_ratios: Mapping[tuple[object, ...], float],
        role_ratios: Mapping[tuple[object, ...], float],
    ) -> BattleBuildHitCounterfactual:
        if row.candidate_damage is not None:
            return row
        ratio = skill_ratios.get(cls._skill_key(hit))
        if ratio is None:
            ratio = type_ratios.get(cls._type_key(hit))
        if ratio is None:
            ratio = role_ratios.get(cls._role_key(hit))
        if ratio is None:
            return row
        return replace(
            row,
            heuristic_projection_damage=row.baseline_damage * ratio,
        )

    @staticmethod
    def _unavailable_ratio(
        *,
        code: str,
        explanation: str,
    ) -> BattleCounterfactualRatio:
        gap = BattleQuantificationGap(
            code=code,
            dimension_id="derived_mechanic",
            dependency_scope="mechanic_specific",
            property_ids=(),
            explanation=explanation,
        )
        return BattleCounterfactualRatio.unavailable(
            method="derived_mechanic_unavailable",
            confidence="低",
            dependency_scope="mechanic_specific",
            cancelled_dimension_ids=(),
            gaps=(gap,),
            explanation=explanation,
        )

    @classmethod
    def _linked_ratio(
        cls,
        rows: Sequence[BattleBuildHitCounterfactual],
        baseline_damage: float,
    ) -> tuple[BattleCounterfactualRatio, float | None]:
        if not rows:
            return cls._unavailable_ratio(
                code="linked_source_hit_missing",
                explanation="未找到可联动的来源逐击。",
            ), None
        linked_baseline = sum(row.baseline_damage for row in rows)
        known_total = sum(
            BattleBuildQuantificationService.known_or_source(row)
            for row in rows
        )
        linked_ratio = safe_ratio(known_total, linked_baseline)
        gaps = tuple(dict.fromkeys(
            gap
            for row in rows
            for gap in row.quantification.gaps
        ))
        has_quantified = any(
            row.known_projection_damage is not None for row in rows
        )
        if linked_ratio is None or not has_quantified:
            return cls._unavailable_ratio(
                code="linked_source_hit_unavailable",
                explanation="来源逐击均不可量化，不能联动生命上限结算。",
            ), cls._linked_heuristic(rows, baseline_damage)
        if gaps:
            quantification = BattleCounterfactualRatio.partial(
                linked_ratio,
                method="linked_source_hit_ratio",
                confidence="低",
                dependency_scope="mechanic_specific",
                included_dimension_ids=("linked_source_hits",),
                cancelled_dimension_ids=(),
                gaps=gaps,
                explanation="仅按来源逐击已量化分量联动；缺口不代表零收益。",
            )
        else:
            quantification = BattleCounterfactualRatio.complete(
                linked_ratio,
                method="linked_source_hit_ratio",
                confidence="中",
                dependency_scope="mechanic_specific",
                included_dimension_ids=("linked_source_hits",),
                explanation="按已归因来源逐击的完整候选/原始伤害比联动。",
            )
        return quantification, cls._linked_heuristic(rows, baseline_damage)

    @classmethod
    def _linked_heuristic(
        cls,
        rows: Sequence[BattleBuildHitCounterfactual],
        baseline_damage: float,
    ) -> float | None:
        if not any(row.heuristic_projection_damage is not None for row in rows):
            return None
        linked_baseline = sum(row.baseline_damage for row in rows)
        if linked_baseline <= 0.0:
            return None
        ratio = sum(
            BattleBuildQuantificationService.display_projection(row)
            for row in rows
        ) / linked_baseline
        return baseline_damage * ratio

    @staticmethod
    def _skill_key(hit: BattleAnalysisHit) -> tuple[object, ...]:
        return (
            hit.character_id,
            hit.scope_half,
            hit.target_id,
            hit.ability_id or hit.skill_name,
            hit.damage_attribute.casefold(),
            hit.classification,
        )

    @staticmethod
    def _type_key(hit: BattleAnalysisHit) -> tuple[object, ...]:
        return (
            hit.character_id,
            hit.scope_half,
            hit.target_id,
            hit.damage_attribute.casefold(),
            hit.classification,
        )

    @staticmethod
    def _role_key(hit: BattleAnalysisHit) -> tuple[object, ...]:
        return hit.character_id, hit.scope_half, hit.target_id
