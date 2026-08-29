# 聚合修改副本逐击与机制收益为角色级反事实结果。
"""Role-level aggregation support for fixed-axis build comparison."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence, Set

from src.domain.battle_counterfactual import (
    BattleBuildHitCounterfactual,
    BattleBuildRoleCounterfactual,
    BattleBuildVitalCounterfactual,
)
from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_build_quantification_service import (
    BattleBuildQuantificationService,
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


__all__ = ["build_role_counterfactuals"]
