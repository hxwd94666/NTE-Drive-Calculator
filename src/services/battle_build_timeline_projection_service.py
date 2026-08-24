# 把固定轴边际结果投影成可复用现有 Canvas 的调整后逐击轴。
"""Immutable adjusted-timeline projection for one build counterfactual."""

from __future__ import annotations

from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleBuildCounterfactual,
    BattleRangeRoleSummary,
)


class BattleBuildTimelineProjectionService:
    """Project candidate hit sizes without changing the immutable source axis."""

    @staticmethod
    def project(
        analysis: BattleAnalysisSnapshot,
        counterfactual: BattleBuildCounterfactual,
    ) -> BattleAnalysisSnapshot:
        projected = {row.event_id: row for row in counterfactual.hits}
        projected_vital = {row.event_id: row for row in counterfactual.vital_events}

        def project_hit(hit: BattleAnalysisHit) -> BattleAnalysisHit:
            row = projected.get(hit.event_id)
            if row is None or hit.direction != "outgoing":
                return hit
            return replace(hit, damage=row.predicted_damage)

        timeline_hits = tuple(project_hit(hit) for hit in analysis.timeline_hits)
        selected_hits = tuple(project_hit(hit) for hit in analysis.hits)

        actions = tuple(
            replace(
                action,
                damage=max(
                    0.0,
                    action.damage
                    + sum(
                        projected[event_id].predicted_damage
                        - projected[event_id].baseline_damage
                        for event_id in action.evidence_event_ids
                        if event_id in projected
                    ),
                ),
            )
            for action in analysis.inferred_actions
        )
        groups = []
        for group in analysis.timeline_damage_groups:
            vital_event_id = (
                group.group_id.removeprefix("vital:")
                if group.group_id.startswith("vital:")
                else ""
            )
            vital = projected_vital.get(vital_event_id)
            if vital is not None:
                gain = vital.predicted_damage - vital.baseline_damage
                detail_lines = group.detail_lines + (
                    f"调整后预计 {vital.predicted_damage:,.2f}（{gain:+,.2f}，{(vital.ratio - 1.0) * 100.0:+.2f}%）",
                    f"{vital.explanation}；置信度 {vital.confidence}",
                )
                groups.append(replace(
                    group,
                    damage=vital.predicted_damage,
                    detail_lines=detail_lines,
                ))
                continue
            groups.append(replace(
                group,
                damage=max(
                        0.0,
                        group.damage
                        + sum(
                            projected[event_id].predicted_damage
                            - projected[event_id].baseline_damage
                            for event_id in group.evidence_event_ids
                            if event_id in projected
                        ),
                ),
            ))
        groups = tuple(groups)
        projected_timeline_vital_events = tuple(
            replace(
                event,
                effective_hp_loss=projected_vital[event.event_id].predicted_damage,
                inference_basis=(
                    f"{event.inference_basis}；边际："
                    f"{projected_vital[event.event_id].explanation}"
                ),
            )
            if event.event_id in projected_vital
            else event
            for event in analysis.timeline_max_hp_events
        )
        projected_selected_vital_events = tuple(
            replace(
                event,
                effective_hp_loss=projected_vital[event.event_id].predicted_damage,
                inference_basis=(
                    f"{event.inference_basis}；边际："
                    f"{projected_vital[event.event_id].explanation}"
                ),
            )
            if event.event_id in projected_vital
            else event
            for event in analysis.max_hp_events
        )
        vital_damage_by_role: dict[int, float] = {}
        vital_events_by_role: dict[int, int] = {}
        for event in projected_selected_vital_events:
            if event.source_character_id is None:
                continue
            vital_damage_by_role[event.source_character_id] = (
                vital_damage_by_role.get(event.source_character_id, 0.0)
                + event.effective_hp_loss
            )
            vital_events_by_role[event.source_character_id] = (
                vital_events_by_role.get(event.source_character_id, 0) + 1
            )
        original_roles = {row.character_id: row for row in analysis.roles}
        roles = tuple(
            BattleRangeRoleSummary(
                character_id=row.character_id,
                character_name=row.character_name,
                hits=original_roles.get(row.character_id).hits
                if row.character_id in original_roles
                else 0,
                damage=row.predicted_damage,
                dps=row.predicted_damage / max(0.001, analysis.duration_seconds),
                share_percent=(
                    row.predicted_damage / counterfactual.predicted_damage * 100.0
                    if counterfactual.predicted_damage
                    else 0.0
                ),
                raw_damage=max(
                    0.0,
                    row.predicted_damage
                    - vital_damage_by_role.get(row.character_id, 0.0),
                ),
                max_hp_reduction_damage=vital_damage_by_role.get(row.character_id, 0.0),
                max_hp_reduction_events=vital_events_by_role.get(row.character_id, 0),
            )
            for row in counterfactual.roles
        )
        projected_hit_damage = sum(
            hit.damage for hit in selected_hits if hit.direction == "outgoing"
        )
        return replace(
            analysis,
            timeline_hits=timeline_hits,
            hits=selected_hits,
            inferred_actions=actions,
            timeline_damage_groups=groups,
            timeline_max_hp_events=projected_timeline_vital_events,
            max_hp_events=projected_selected_vital_events,
            max_hp_reduction_damage=sum(
                event.effective_hp_loss for event in projected_selected_vital_events
            ),
            roles=roles,
            total_damage=projected_hit_damage,
            total_dps=projected_hit_damage / max(0.001, analysis.duration_seconds),
            effective_damage=counterfactual.predicted_damage,
            effective_dps=counterfactual.predicted_dps,
        )
