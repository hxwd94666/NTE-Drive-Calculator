# 把固定轴边际结果投影成可复用现有 Canvas 的调整后逐击轴。
"""Immutable adjusted-timeline projection for one build counterfactual."""

from __future__ import annotations

from dataclasses import replace

from src.domain.battle_counterfactual import BattleBuildCounterfactual
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleRangeRoleSummary,
    BattleTimelineDamageGroup,
)
from src.services.battle_daffodill_marginal_service import (
    DAFFODILL_EFFECT_FIVE_METHOD,
    BattleDaffodillMarginalService,
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
        source_hits = {hit.event_id: hit for hit in analysis.hits}
        source_replays = {row.event_id: row for row in analysis.hit_replays}
        derived_rows = tuple(
            row for row in counterfactual.hits
            if row.method == DAFFODILL_EFFECT_FIVE_METHOD
        )
        derived_hits = tuple(
            BattleDaffodillMarginalService.composition_hit(row, source_hits)
            for row in derived_rows
        )

        def project_hit(hit: BattleAnalysisHit) -> BattleAnalysisHit:
            row = projected.get(hit.event_id)
            if row is None or hit.direction != "outgoing":
                return hit
            return replace(hit, damage=row.predicted_damage)

        timeline_hits = tuple(sorted(
            (*map(project_hit, analysis.timeline_hits), *derived_hits),
            key=lambda hit: (hit.relative_time_us, hit.event_id),
        ))
        selected_hits = tuple(sorted(
            (*map(project_hit, analysis.hits), *derived_hits),
            key=lambda hit: (hit.relative_time_us, hit.event_id),
        ))

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
        groups.extend(
            BattleTimelineDamageGroup(
                group_id=f"candidate:{row.event_id}",
                character_id=1054,
                character_name="达芙蒂尔",
                direction="outgoing",
                channel_key="special_daffodill_extra_topple",
                channel_label="候选五觉·额外倾陷",
                damage_name=row.damage_name,
                source_skill_name=row.skill_name,
                ability_id="character_awaken:1054:Effect5",
                start_us=source_hits[row.source_event_id].relative_time_us + 1,
                end_us=source_hits[row.source_event_id].relative_time_us + 2,
                hits=1,
                damage=row.predicted_damage,
                evidence_event_ids=(row.event_id,),
                detail_lines=(row.explanation, f"置信度 {row.confidence}"),
            )
            for row in derived_rows
        )
        groups = tuple(sorted(
            groups,
            key=lambda group: (group.start_us, group.end_us, group.group_id),
        ))
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
        roles = tuple(
            BattleRangeRoleSummary(
                character_id=row.character_id,
                character_name=row.character_name,
                hits=sum(
                    hit.direction == "outgoing"
                    and hit.character_id == row.character_id
                    for hit in selected_hits
                ),
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
            hit_replays=tuple((
                *analysis.hit_replays,
                *(
                    BattleDaffodillMarginalService.composition_replay(
                        row, source_hits, source_replays,
                    )
                    for row in derived_rows
                ),
            )),
        )
