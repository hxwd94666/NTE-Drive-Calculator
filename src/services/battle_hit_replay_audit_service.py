# 用同场实际逐击补充噩梦层数与伤害归属诊断，不改写原始伤害事实。
"""Narrow post-processing for replay evidence that the formula cannot own."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from math import isfinite, log

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitReplayFactor,
    BattleHitReplayResult,
)
from src.services.battle_hit_replay_support import (
    ceil_replay_damage,
    replay_error_percent,
    replay_signed_error_percent,
)


_NIGHTMARE_MARKER = "lacrimosa_blood_damage"
_EROSION_ID = "ge_player_zankou_dotdamage"
_OBSERVED_UNIT_WINDOW_US = 10_000_000
_RECENT_APPLICATION_WINDOW_US = 700_000
_DUPLICATE_DAMAGE_WINDOW_US = 700_000
_DARK_STAR_EFFECT_ID = "buff_reaction_4_new"


def _target_key(hit: BattleAnalysisHit) -> tuple[str, str]:
    return (
        str(hit.scope_half or "").casefold(),
        str(hit.target_id or "unknown").casefold(),
    )


def _factor(
    result: BattleHitReplayResult,
    factor_id: str,
) -> BattleHitReplayFactor | None:
    return next(
        (row for row in result.factors if row.factor_id == factor_id),
        None,
    )


def _is_nightmare_application(hit: BattleAnalysisHit) -> bool:
    effect = hit.gameplay_effect_id.casefold()
    ability = hit.ability_id.casefold()
    return bool(
        hit.character_id == 1004
        and hit.classification == "direct"
        and _NIGHTMARE_MARKER not in effect
        and "qte" not in effect
        and "qte" not in ability
        and "steal" not in effect
        and ability != "ga_lacrimosa_steal"
    )


class BattleHitReplayAuditService:
    """Apply evidence-only replay refinements after deterministic formulas."""

    @classmethod
    def postprocess(
        cls,
        analysis: BattleAnalysisSnapshot,
        results: tuple[BattleHitReplayResult, ...],
    ) -> tuple[BattleHitReplayResult, ...]:
        adjusted = cls.apply_dark_star_hp_remainder_observation(analysis, results)
        adjusted = cls.apply_nightmare_observed_layer_adjustment(analysis, adjusted)
        adjusted = cls.apply_erosion_settlement_adjustment(analysis, adjusted)
        return cls.apply_damage_attribution_conflicts(analysis, adjusted)

    @classmethod
    def apply_dark_star_hp_remainder_observation(
        cls,
        analysis: BattleAnalysisSnapshot,
        results: tuple[BattleHitReplayResult, ...],
    ) -> tuple[BattleHitReplayResult, ...]:
        """Recover one missing structured Dark Star additional settlement.

        nte-core models a four-wrapper server settlement as primary damage plus
        a separately typed follow-up. Older saved axes may contain only the
        primary value while their target HP transition still covers both. This
        refinement exposes an exact formula-comparable observation without
        mutating the raw hit, totals, attribution, or persisted battle axis.
        """

        hits_by_event = {hit.event_id: hit for hit in analysis.hits}
        conflicts = cls.damage_attribution_conflict_ids(analysis.hits)
        replacements: dict[str, BattleHitReplayResult] = {}
        for result in results:
            hit = hits_by_event.get(result.event_id)
            selected = result.selected_damage
            if (
                hit is None
                or result.event_id in conflicts
                or hit.direction != "outgoing"
                or hit.is_follow_up
                or hit.gameplay_effect_id.casefold() != _DARK_STAR_EFFECT_ID
                or "黯星" not in result.formula_type
                or selected is None
                or selected <= 0.0
                or result.observed_damage <= 0.0
                or hit.target_hp_before is None
                or hit.target_hp_after is None
                or not hit.target_id
                or hit.target_id.casefold() in {"unknown", "unknown-target"}
                or (hit.overkill_damage or 0.0) > 0.0
                or hit.damage_overlap_correction > 0.0
            ):
                continue
            before = float(hit.target_hp_before)
            after = float(hit.target_hp_after)
            if (
                not isfinite(before)
                or not isfinite(after)
                or before <= after
                or after <= 0.0
            ):
                continue
            hp_delta = before - after
            remainder = hp_delta - hit.damage
            tolerance = max(1.0, abs(selected) * 0.000_001)
            reported_matches = abs(hit.damage - selected) <= tolerance
            if (
                remainder <= 0.0
                or reported_matches
                or abs(remainder - selected) > tolerance
            ):
                continue
            observed = float(remainder)
            signed_error = replay_signed_error_percent(observed, selected)
            absolute_error = replay_error_percent(observed, selected)
            expected = result.expected_damage
            corrected_expected = (
                expected * observed / selected
                if expected is not None and selected > 0.0
                else None
            )
            basis = (
                f"同目标生命差 {hp_delta:g} 减同批主伤害 {hit.damage:g} "
                f"得到 {observed:g}；与 toolkit 四段服务端结算中的"
                "黯星追加伤害模型及本击公式候选严格一致"
            )
            replacements[result.event_id] = replace(
                result,
                observed_damage=observed,
                selected_error_percent=absolute_error,
                signed_error_percent=signed_error,
                confidence="高",
                corrected_expected_damage=corrected_expected,
                reported_damage=hit.damage,
                observed_damage_source="target_hp_transition_remainder",
                observed_damage_basis=basis,
                missing_evidence=tuple(dict.fromkeys((
                    *result.missing_evidence,
                    (
                        f"原始逐击仍上报主伤害 {hit.damage:g}；公式比较只采用"
                        f"目标生命变化中严格分离的黯星追加伤害 {observed:g}，"
                        "不改写原轴、伤害合计或生命上限下降"
                    ),
                ))),
            )
        return tuple(replacements.get(row.event_id, row) for row in results)

    @classmethod
    def apply_erosion_settlement_adjustment(
        cls,
        analysis: BattleAnalysisSnapshot,
        results: tuple[BattleHitReplayResult, ...],
    ) -> tuple[BattleHitReplayResult, ...]:
        """Choose only single-share or formal-full erosion settlement modes.

        The formal stack remains forward-replayed. This adjustment changes the
        current hit's formula coefficient only; it never back-writes the stack
        or opens an unconstrained 1..10 nearest-damage search.
        """

        hits_by_event = {hit.event_id: hit for hit in analysis.hits}
        conflicts = cls.damage_attribution_conflict_ids(analysis.hits)
        replacements: dict[str, BattleHitReplayResult] = {}
        for result in results:
            hit = hits_by_event.get(result.event_id)
            stack_factor = _factor(result, "state_coefficient")
            if (
                hit is None
                or result.event_id in conflicts
                or hit.gameplay_effect_id.casefold() != _EROSION_ID
                or "蚀心" not in result.formula_type
                or stack_factor is None
                or stack_factor.value <= 1.0
                or result.non_critical_damage is None
                or result.non_critical_damage <= 0.0
                or result.observed_damage <= 0.0
            ):
                continue
            formal_layers = float(stack_factor.value)
            single_noncritical = ceil_replay_damage(
                result.non_critical_damage / formal_layers
            )
            single_critical = (
                None
                if result.critical_damage is None
                else ceil_replay_damage(result.critical_damage / formal_layers)
            )
            candidates: list[tuple[str, float, bool, float]] = [
                ("正式满份", formal_layers, False, result.non_critical_damage),
                ("单份", 1.0, False, single_noncritical),
            ]
            if result.critical_damage is not None:
                candidates.append(
                    ("正式满份", formal_layers, True, result.critical_damage)
                )
            if single_critical is not None:
                candidates.append(("单份", 1.0, True, single_critical))
            ranked: list[tuple[float, str, float, bool, float]] = sorted(
                (
                    abs(log(result.observed_damage / predicted)),
                    mode,
                    coefficient,
                    is_critical,
                    predicted,
                )
                for mode, coefficient, is_critical, predicted in candidates
                if predicted > 0.0
            )
            if not ranked:
                continue
            best = ranked[0]
            separation = (
                ranked[1][0] - best[0] if len(ranked) > 1 else float("inf")
            )
            _loss, mode, coefficient, inferred_critical, selected = best
            error = replay_error_percent(result.observed_damage, selected)
            signed_error = replay_signed_error_percent(
                result.observed_damage,
                selected,
            )
            if error > 20.0 or separation < 0.015:
                replacements[result.event_id] = replace(
                    result,
                    critical_state="ambiguous",
                    confidence="低",
                    missing_evidence=tuple(dict.fromkeys((
                        *result.missing_evidence,
                        "蚀心单份/正式满份与暴击候选未唯一分离，保留正向状态公式并输出低置信",
                    ))),
                )
                continue
            confidence = (
                "高"
                if error <= 2.0 and separation >= 0.03
                else "中"
            )
            basis = (
                f"正式状态机在本击前为 {formal_layers:g} 层；历史纯自跳轴证明"
                f"蚀心可能按单份或正式满份结算，本击仅在这两类中匹配为{mode}；"
                "暴击按整跳选择一次；该选择不反写正式层数"
            )
            factors = tuple(
                replace(
                    row,
                    label="蚀心本跳有效结算系数",
                    value=coefficient,
                    evidence_basis=basis,
                    formula="有效系数 ∈ {1, 正式层数}",
                )
                if row.factor_id == "state_coefficient"
                else row
                for row in result.factors
            )
            noncritical = (
                result.non_critical_damage
                if coefficient == formal_layers
                else single_noncritical
            )
            critical = (
                result.critical_damage
                if coefficient == formal_layers
                else single_critical
            )
            expected = (
                None
                if result.critical_rate is None
                else noncritical
                if critical is None
                else (
                    noncritical * (1.0 - result.critical_rate)
                    + critical * result.critical_rate
                )
            )
            corrected_expected = (
                expected * result.observed_damage / selected
                if expected is not None and selected > 0.0
                else None
            )
            replacements[result.event_id] = replace(
                result,
                non_critical_damage=noncritical,
                critical_damage=critical,
                selected_damage=selected,
                selected_error_percent=error,
                signed_error_percent=signed_error,
                critical_state=(
                    "critical" if inferred_critical else "non_critical"
                ),
                confidence=confidence,
                factors=factors,
                missing_evidence=tuple(dict.fromkeys((
                    *result.missing_evidence,
                    "蚀心结算模式来自受约束的单份/正式满份反算，待受控战报确认引擎内部批次语义",
                ))),
                expected_damage=expected,
                corrected_expected_damage=corrected_expected,
            )
        return tuple(replacements.get(row.event_id, row) for row in results)

    @classmethod
    def apply_nightmare_observed_layer_adjustment(
        cls,
        analysis: BattleAnalysisSnapshot,
        results: tuple[BattleHitReplayResult, ...],
    ) -> tuple[BattleHitReplayResult, ...]:
        hits_by_event = {hit.event_id: hit for hit in analysis.hits}
        conflicts = cls.damage_attribution_conflict_ids(analysis.hits)
        applications: dict[tuple[str, str], list[int]] = defaultdict(list)
        for hit in analysis.hits:
            if _is_nightmare_application(hit):
                applications[_target_key(hit)].append(hit.relative_time_us)
        for times in applications.values():
            times.sort()

        samples: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
        replacements: dict[str, BattleHitReplayResult] = {}
        ordered = sorted(
            results,
            key=lambda row: (
                hits_by_event[row.event_id].relative_time_us,
                hits_by_event[row.event_id].sequence,
                row.event_id,
            ),
        )
        for original in ordered:
            hit = hits_by_event[original.event_id]
            result = original
            stack_factor = _factor(result, "state_coefficient")
            if (
                original.event_id not in conflicts
                and "噩梦" in original.formula_type
                and stack_factor is not None
                and stack_factor.value >= 2.0
                and original.non_critical_damage is not None
            ):
                target = _target_key(hit)
                recent_samples = [
                    row for row in samples[target]
                    if hit.relative_time_us - row[0] <= _OBSERVED_UNIT_WINDOW_US
                ]
                samples[target] = recent_samples
                if recent_samples and cls._has_recent_application(
                    applications[target],
                    hit.relative_time_us,
                ):
                    current_layers = int(round(stack_factor.value))
                    current_match = cls._best_unit_match(
                        result,
                        current_layers,
                        recent_samples,
                    )
                    missing_match = cls._best_unit_match(
                        result,
                        current_layers - 1,
                        recent_samples,
                    )
                    if (
                        missing_match is not None
                        and missing_match[0] <= 0.01
                        and (
                            current_match is None
                            or current_match[0] >= 0.03
                        )
                    ):
                        result = cls._replace_nightmare_layers(
                            result,
                            stack_factor,
                            current_layers - 1,
                            inferred_critical=missing_match[1],
                            reference_unit=missing_match[2],
                        )
                        replacements[result.event_id] = result
                        stack_factor = _factor(result, "state_coefficient")

            if (
                original.event_id not in conflicts
                and "噩梦" in result.formula_type
                and stack_factor is not None
                and result.non_critical_damage is not None
                and stack_factor.value > 0.0
            ):
                sample = cls._plausible_observed_unit(result, stack_factor.value)
                if sample is not None:
                    samples[_target_key(hit)].append((hit.relative_time_us, sample))
        return tuple(replacements.get(row.event_id, row) for row in results)

    @staticmethod
    def _has_recent_application(times: Sequence[int], at_us: int) -> bool:
        position = bisect_right(times, at_us)
        if position <= 0:
            return False
        delta = at_us - times[position - 1]
        return 0 <= delta <= _RECENT_APPLICATION_WINDOW_US

    @staticmethod
    def _best_unit_match(
        result: BattleHitReplayResult,
        layers: int,
        samples: Sequence[tuple[int, float]],
    ) -> tuple[float, bool, float] | None:
        if layers <= 0 or result.observed_damage <= 0.0:
            return None
        crit = _factor(result, "critical")
        hypotheses = [(result.observed_damage / layers, False)]
        if crit is not None and crit.value > 1.0:
            hypotheses.append((result.observed_damage / layers / crit.value, True))
        matches = (
            (abs(unit - reference) / reference, is_critical, reference)
            for unit, is_critical in hypotheses
            for _at_us, reference in samples
            if reference > 0.0
        )
        return min(matches, default=None, key=lambda row: row[0])

    @staticmethod
    def _plausible_observed_unit(
        result: BattleHitReplayResult,
        layers: float,
    ) -> float | None:
        formula_unit = result.non_critical_damage / layers
        if formula_unit <= 0.0:
            return None
        crit = _factor(result, "critical")
        candidates = [result.observed_damage / layers]
        if crit is not None and crit.value > 1.0:
            candidates.append(result.observed_damage / layers / crit.value)
        plausible = tuple(
            (abs(unit - formula_unit) / formula_unit, unit)
            for unit in candidates
            if abs(unit - formula_unit) / formula_unit <= 0.30
        )
        return min(plausible, default=(0.0, None), key=lambda row: row[0])[1]

    @staticmethod
    def _replace_nightmare_layers(
        result: BattleHitReplayResult,
        stack_factor: BattleHitReplayFactor,
        layers: int,
        *,
        inferred_critical: bool,
        reference_unit: float,
    ) -> BattleHitReplayResult:
        ratio = layers / stack_factor.value
        assert result.non_critical_damage is not None
        noncrit = ceil_replay_damage(result.non_critical_damage * ratio)
        critical = (
            None
            if result.critical_damage is None
            else ceil_replay_damage(result.critical_damage * ratio)
        )
        selected = critical if inferred_critical and critical is not None else noncrit
        state = "critical" if inferred_critical and critical is not None else "non_critical"
        expected = (
            None
            if result.critical_rate is None
            else noncrit
            if critical is None
            else noncrit * (1.0 - result.critical_rate) + critical * result.critical_rate
        )
        corrected_expected = (
            expected * result.observed_damage / selected
            if expected is not None and selected > 0.0
            else None
        )
        basis = (
            f"逐击正向重放原为 {stack_factor.value:g} 层；本击前 0.7 秒内存在"
            f"噩梦施加 hit，且同目标前序噩梦实伤反算单层约 {reference_unit:g}；"
            "当前实伤只支持服务器少接收 1 层。仅修正本击展示/边际基准，"
            "不反写后续状态。"
        )
        factors = tuple(
            replace(row, value=float(layers), evidence_basis=basis)
            if row.factor_id == "state_coefficient"
            else row
            for row in result.factors
        )
        return replace(
            result,
            non_critical_damage=noncrit,
            critical_damage=critical,
            selected_damage=selected,
            selected_error_percent=replay_error_percent(
                result.observed_damage,
                selected,
            ),
            signed_error_percent=replay_signed_error_percent(
                result.observed_damage,
                selected,
            ),
            critical_state=state,
            confidence="中",
            factors=factors,
            missing_evidence=tuple(dict.fromkeys((
                *result.missing_evidence,
                "噩梦层数由同场实际伤害反算补正，不冒充运行时 Buff 层数",
            ))),
            expected_damage=expected,
            corrected_expected_damage=corrected_expected,
        )

    @classmethod
    def apply_damage_attribution_conflicts(
        cls,
        analysis: BattleAnalysisSnapshot,
        results: tuple[BattleHitReplayResult, ...],
    ) -> tuple[BattleHitReplayResult, ...]:
        conflicts = cls.damage_attribution_conflict_ids(analysis.hits)
        reason = (
            "同一服务端 HP 结算的重复归属候选：同一目标 700ms 内另一伤害项"
            "上报相同伤害，且 HP 区间重叠或结算端点一致；这不表示伤害实际发生两次。"
            "保留原始逐击与公式候选，但本击不参与暴击或层数校准"
        )
        return tuple(
            replace(
                row,
                critical_state="ambiguous",
                confidence="低",
                missing_evidence=tuple(dict.fromkeys((*row.missing_evidence, reason))),
            )
            if row.event_id in conflicts
            else row
            for row in results
        )

    @staticmethod
    def damage_attribution_conflict_ids(
        hits: Sequence[BattleAnalysisHit],
    ) -> frozenset[str]:
        """Return raw event IDs whose damage ownership is formally conflicted."""

        ordered = sorted(
            (
                hit for hit in hits
                if getattr(hit, "direction", "") == "outgoing"
            ),
            key=lambda row: (row.relative_time_us, row.sequence, row.event_id),
        )
        conflicts: set[str] = set()
        for index, first in enumerate(ordered):
            if first.target_hp_before is None or first.target_hp_after is None:
                continue
            for second in ordered[index + 1:]:
                delta = second.relative_time_us - first.relative_time_us
                if delta > _DUPLICATE_DAMAGE_WINDOW_US:
                    break
                if (
                    _target_key(first) != _target_key(second)
                    or first.gameplay_effect_id.casefold()
                    == second.gameplay_effect_id.casefold()
                    or second.target_hp_before is None
                    or second.target_hp_after is None
                ):
                    continue
                tolerance = max(0.5, abs(first.damage) * 0.000_001)
                if abs(first.damage - second.damage) > tolerance:
                    continue
                first_low, first_high = sorted((
                    first.target_hp_after,
                    first.target_hp_before,
                ))
                second_low, second_high = sorted((
                    second.target_hp_after,
                    second.target_hp_before,
                ))
                hp_tolerance = max(0.5, abs(first.damage) * 0.000_001)
                overlaps = max(first_low, second_low) < min(first_high, second_high)
                same_endpoint = abs(
                    first.target_hp_after - second.target_hp_after
                ) <= hp_tolerance
                if overlaps or same_endpoint:
                    conflicts.update((first.event_id, second.event_id))
        return frozenset(conflicts)
