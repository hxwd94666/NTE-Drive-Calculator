# 聚合固定轴配装反事实的逐击、角色与整场量化状态。
"""Build-counterfactual quantification aggregation and projections."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence, Set

from src.domain.battle_report.counterfactual.results import (
    BattleBuildHitCounterfactual, BattleBuildRoleCounterfactual, BattleBuildVitalCounterfactual,
)
from src.domain.battle_report.counterfactual.quantification import BattleDamageQuantification, BattleQuantificationGap
from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot


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
        # 已知增量、启发式展示和完整候选分别聚合；缺口绝不变成零收益。
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


def build_role_counterfactuals(
    original: BattleAnalysisSnapshot,
    projected_hits: Sequence[BattleBuildHitCounterfactual],
    projected_vital_events: Sequence[BattleBuildVitalCounterfactual],
    *,
    fixed_derived_unchanged: bool,
    structured_methods: Set[str],
    structured_vital_methods: Set[str],
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
        rows = (*hits, *vital_events)
        quantification = BattleBuildQuantificationService.aggregate(
            rows=rows,
            fixed_damage=derived,
            fixed_unchanged=fixed_derived_unchanged,
        )
        known_projection = (
            None
            if quantification.quantified_increment is None
            else baseline + quantification.quantified_increment
        )
        candidate_damage = (
            known_projection
            if quantification.status in {"complete", "not_applicable"}
            else None
        )
        heuristic_projection = (
            sum(
                BattleBuildQuantificationService.display_projection(row)
                for row in rows
            ) + derived
            if any(row.heuristic_projection_damage is not None for row in rows)
            else None
        )
        structured = sum(
            row.baseline_damage
            for row in hits
            if row.quantification.method in structured_methods
        )
        structured += sum(
            row.baseline_damage
            for row in vital_events
            if row.quantification.method in structured_vital_methods
        )
        known_gain = (
            None
            if known_projection is None or not baseline
            else (known_projection / baseline - 1.0) * 100.0
        )
        known_team_gain = (
            None
            if known_projection is None or not original.effective_damage
            else (known_projection - baseline) / original.effective_damage * 100.0
        )
        result.append(BattleBuildRoleCounterfactual(
            character_id=role.character_id,
            character_name=role.character_name,
            baseline_damage=baseline,
            known_projection_damage=known_projection,
            candidate_damage=candidate_damage,
            heuristic_projection_damage=heuristic_projection,
            known_gain_percent=known_gain,
            gain_percent=(known_gain if candidate_damage is not None else None),
            known_team_gain_percent=known_team_gain,
            team_gain_percent=(
                known_team_gain if candidate_damage is not None else None
            ),
            quantification=quantification,
            structured_damage=structured,
            structured_percent=(structured / baseline * 100.0 if baseline else 0.0),
        ))
    return tuple(sorted(result, key=lambda row: row.baseline_damage, reverse=True))

__all__ = ["BattleBuildQuantificationService", "build_role_counterfactuals"]
