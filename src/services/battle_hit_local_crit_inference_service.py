# 用同一伤害项的重复数值对补充逐击暴击分支弱证据。
"""Local repeated-value critical-branch inference for hit replay."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisSnapshot,
    BattleHitReplayResult,
)
from src.services.battle_hit_replay_support import (
    replay_error_percent,
    replay_factor,
    replay_signed_error_percent,
)


class BattleHitLocalCritInferenceService:
    """Apply report-local crit evidence without changing raw hit facts."""

    @staticmethod
    def apply(
        analysis: BattleAnalysisSnapshot,
        results: Sequence[BattleHitReplayResult],
    ) -> tuple[BattleHitReplayResult, ...]:
        hits = {row.event_id: row for row in analysis.hits}
        baselines = {
            row.character_id: {stat.property_id: stat.value for stat in row.stats}
            for row in analysis.baselines
        }
        grouped: dict[tuple[int | None, str], list[BattleHitReplayResult]] = (
            defaultdict(list)
        )
        for result in results:
            hit = hits[result.event_id]
            if (
                hit.gameplay_effect_id
                and result.non_critical_damage is not None
                and result.critical_damage is not None
                and all(
                    row.factor_id != "state_coefficient"
                    for row in result.factors
                )
            ):
                grouped[(hit.character_id, hit.gameplay_effect_id)].append(result)
        replacements: dict[str, BattleHitReplayResult] = {}
        for (character_id, _damage_id), rows in grouped.items():
            if len(rows) < 4:
                continue
            counts = Counter(round(row.observed_damage, 3) for row in rows)
            values = sorted(counts)
            if len(values) < 2:
                continue
            baseline = baselines.get(character_id, {})
            expected = 1.0 + max(0.0, baseline.get("CritDamageBase", 0.50))
            formula_ratios = [
                row.critical_damage / row.non_critical_damage
                for row in rows
                if row.critical_damage is not None
                and row.non_critical_damage is not None
                and row.non_critical_damage > 0
            ]
            if formula_ratios:
                expected = sorted(formula_ratios)[len(formula_ratios) // 2]
            pairs = []
            for low_index, low in enumerate(values):
                if low <= 0:
                    continue
                for high in values[low_index + 1:]:
                    ratio = high / low
                    if not 1.20 <= ratio <= 4.50:
                        continue
                    if abs(ratio - expected) / expected > 0.25:
                        continue
                    pairs.append((low, high, ratio, min(counts[low], counts[high])))
            if not pairs:
                continue
            candidates = []
            for candidate in pairs:
                matching = [
                    pair
                    for pair in pairs
                    if abs(pair[2] - candidate[2]) / candidate[2] <= 0.02
                ]
                support = sum(pair[3] for pair in matching)
                candidates.append((support, len(matching), candidate[2], matching))
            support, pair_count, crit_ratio, matching = max(
                candidates,
                key=lambda row: (row[0], row[1], -abs(row[2] - expected)),
            )
            if support < 2 or (
                pair_count < 2
                and not any(
                    counts[low] >= 2 and counts[high] >= 2
                    for low, high, *_ in matching
                )
            ):
                continue
            low_values = {pair[0] for pair in matching}
            high_values = {pair[1] for pair in matching}
            for result in rows:
                observed = round(result.observed_damage, 3)
                is_low = observed in low_values
                is_high = observed in high_values
                if is_low == is_high:
                    continue
                state = "non_critical" if is_low else "critical"
                selected = (
                    result.non_critical_damage
                    if is_low
                    else result.critical_damage
                )
                assert selected is not None
                selected_error = replay_error_percent(
                    result.observed_damage,
                    selected,
                )
                signed_error = replay_signed_error_percent(
                    result.observed_damage,
                    selected,
                )
                corrected_expected = (
                    result.expected_damage * result.observed_damage / selected
                    if result.expected_damage is not None and selected > 0.0
                    else None
                )
                replacements[result.event_id] = replace(
                    result,
                    selected_damage=selected,
                    selected_error_percent=selected_error,
                    signed_error_percent=signed_error,
                    critical_state=state,
                    confidence="中",
                    corrected_expected_damage=corrected_expected,
                    factors=(
                        *result.factors,
                        replay_factor(
                            "local_crit_pair",
                            "同伤害项暴击倍率",
                            crit_ratio,
                            (
                                f"本战报 {pair_count} 组重复数值对，"
                                f"共同倍率约 {crit_ratio:.3f}（弱证据）"
                            ),
                        ),
                    ),
                    missing_evidence=tuple(dict.fromkeys((
                        *result.missing_evidence,
                        "暴击由本战报同 GE 数值对补充，不冒充 nte-core 暴击标记",
                    ))),
                )
        return tuple(replacements.get(row.event_id, row) for row in results)


__all__ = ["BattleHitLocalCritInferenceService"]
