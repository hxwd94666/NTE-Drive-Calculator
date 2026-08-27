# 聚合固定轴配装反事实的逐击、角色与整场量化状态。
"""Build-counterfactual quantification aggregation and projections."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.battle_counterfactual import (
    BattleBuildHitCounterfactual,
    BattleBuildVitalCounterfactual,
)
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.domain.battle_report import BattleAnalysisSnapshot


BuildCounterfactualRow = (
    BattleBuildHitCounterfactual | BattleBuildVitalCounterfactual
)


class BattleBuildQuantificationService:
    """Keep quantified, full-candidate, heuristic and source values distinct."""

    @staticmethod
    def known_or_source(row: BuildCounterfactualRow) -> float:
        value = row.known_projection_damage
        return row.baseline_damage if value is None else value

    @staticmethod
    def display_projection(row: BuildCounterfactualRow) -> float:
        for value in (
            row.candidate_damage,
            row.heuristic_projection_damage,
            row.known_projection_damage,
        ):
            if value is not None:
                return value
        return row.baseline_damage

    @staticmethod
    def fixed_derived_is_unchanged(
        original: BattleAnalysisSnapshot,
        candidate: BattleAnalysisSnapshot,
    ) -> bool:
        # 只有完整分析快照相同才能证明轴外派生伤害未受候选影响。
        # 仅比较面板或 replay 会漏掉 Buff、目标画像和机制事实变化。
        return original == candidate

    @staticmethod
    def aggregate(
        *,
        rows: Sequence[BuildCounterfactualRow],
        fixed_damage: float,
        fixed_unchanged: bool,
    ) -> BattleDamageQuantification:
        fully = 0.0
        partial = 0.0
        unavailable = 0.0
        unchanged = fixed_damage if fixed_unchanged else 0.0
        known_increment = 0.0
        has_complete = False
        gaps: list[BattleQuantificationGap] = []
        for row in rows:
            status = row.quantification.status
            if status == "complete":
                fully += row.baseline_damage
                has_complete = True
            elif status == "partial":
                partial += row.baseline_damage
            elif status == "unavailable":
                unavailable += row.baseline_damage
            else:
                unchanged += row.baseline_damage
            if row.known_projection_damage is not None:
                known_increment += (
                    row.known_projection_damage - row.baseline_damage
                )
            gaps.extend(row.quantification.gaps)
        if fixed_damage > 0.0 and not fixed_unchanged:
            unavailable += fixed_damage
            gaps.append(BattleQuantificationGap(
                code="derived_damage_dependency_unresolved",
                dimension_id="fixed_derived_damage",
                dependency_scope="mechanic_specific",
                property_ids=(),
                explanation="未归因派生伤害缺少候选联动公式。",
            ))
        unique_gaps = tuple(dict.fromkeys(gaps))
        if unavailable > 0.0:
            if fully > 0.0 or partial > 0.0:
                status = "partial"
                quantified_increment: float | None = known_increment
            else:
                status = "unavailable"
                quantified_increment = None
        elif partial > 0.0:
            status = "partial"
            quantified_increment = known_increment
        elif has_complete:
            status = "complete"
            quantified_increment = known_increment
        else:
            status = "not_applicable"
            quantified_increment = 0.0
        return BattleDamageQuantification.from_buckets(
            status=status,
            fully_quantified_damage=fully,
            partially_quantified_damage=partial,
            unavailable_damage=unavailable,
            proven_unchanged_damage=unchanged,
            quantified_increment=quantified_increment,
            gaps=unique_gaps,
        )


__all__ = ["BattleBuildQuantificationService"]
