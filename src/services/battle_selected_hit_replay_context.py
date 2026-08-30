# 为固定轴反事实冻结全轴审计分支，并保守判定逐击子集重放资格。
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleHitReplayResult,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
)
from src.services.battle_hit_replay_support import (
    replay_error_percent,
    replay_signed_error_percent,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)


_STATELESS_SELECTED_CHANNELS = frozenset({
    "attachment",
    "direct",
    "direct_follow_up",
})


@dataclass(frozen=True, slots=True)
class FrozenReplayBranch:
    """One branch established by the completed full-axis baseline replay."""

    event_id: str
    critical_state: str
    critical_policy: str
    confidence: str


@dataclass(frozen=True, slots=True)
class PreparedReplayAuditInputs:
    """Request-scoped indexes shared by every Buff removal candidate."""

    hits_by_event: Mapping[str, BattleAnalysisHit]
    evidence_by_event: Mapping[str, BattleSkillDamageEvidence]
    baseline_replay_by_event: Mapping[str, BattleHitReplayResult]
    baselines_by_character: Mapping[int, BattleCharacterBaseline]
    baseline_values_by_character: Mapping[int, Mapping[str, float]]
    target_condition_by_event: Mapping[str, BattleTargetCondition | None]

    @classmethod
    def prepare(
        cls,
        analysis: BattleAnalysisSnapshot,
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        baseline_replays: Sequence[BattleHitReplayResult],
    ) -> "PreparedReplayAuditInputs":
        outgoing_hits = tuple(
            hit for hit in analysis.hits if hit.direction == "outgoing"
        )
        target_condition_by_key: dict[
            tuple[str, str], BattleTargetCondition | None
        ] = {}
        target_condition_by_event: dict[str, BattleTargetCondition | None] = {}
        for hit in outgoing_hits:
            target_key = (hit.scope_half.casefold(), hit.target_id)
            if target_key not in target_condition_by_key:
                hit_analysis = BattleTargetInstanceMappingService.analysis_for_hit(
                    analysis, hit
                )
                target_condition_by_key[target_key] = getattr(
                    hit_analysis,
                    "target_condition",
                    None,
                )
            target_condition_by_event[hit.event_id] = (
                target_condition_by_key[target_key]
            )
        return cls(
            hits_by_event={hit.event_id: hit for hit in outgoing_hits},
            evidence_by_event={row.event_id: row for row in skill_evidence},
            baseline_replay_by_event={
                row.event_id: row for row in baseline_replays
            },
            baselines_by_character={
                row.character_id: row for row in analysis.baselines
            },
            baseline_values_by_character={
                row.character_id: {
                    stat.property_id: float(stat.value)
                    for stat in row.stats
                }
                for row in analysis.baselines
            },
            target_condition_by_event=target_condition_by_event,
        )

    def select(
        self,
        selected_event_ids: Collection[str],
    ) -> "PreparedReplayAuditContext":
        return PreparedReplayAuditContext.from_inputs(self, selected_event_ids)


@dataclass(frozen=True, slots=True)
class PreparedReplayAuditContext:
    """Permission and frozen audit input for a selected-hit candidate replay.

    Preparing this context never asserts that an unknown or stateful formula is
    local.  A caller must use the ordinary full-axis replay whenever
    ``requires_full_axis`` is true.
    """

    selected_event_ids: frozenset[str]
    frozen_branches: tuple[FrozenReplayBranch, ...]
    requires_full_axis: bool
    full_axis_reasons: tuple[str, ...]

    @classmethod
    def prepare(
        cls,
        analysis: BattleAnalysisSnapshot,
        skill_evidence: Sequence[BattleSkillDamageEvidence],
        baseline_replays: Sequence[BattleHitReplayResult],
        selected_event_ids: Collection[str],
    ) -> "PreparedReplayAuditContext":
        inputs = PreparedReplayAuditInputs.prepare(
            analysis,
            skill_evidence,
            baseline_replays,
        )
        return cls.from_inputs(inputs, selected_event_ids)

    @classmethod
    def from_inputs(
        cls,
        inputs: PreparedReplayAuditInputs,
        selected_event_ids: Collection[str],
    ) -> "PreparedReplayAuditContext":
        selected = frozenset(str(event_id) for event_id in selected_event_ids)
        reasons: list[str] = []
        branches: list[FrozenReplayBranch] = []

        for event_id in sorted(selected):
            hit = inputs.hits_by_event.get(event_id)
            if hit is None:
                reasons.append(f"selected_event_missing:{event_id}")
                continue
            if hit.direction != "outgoing":
                reasons.append(f"selected_event_not_outgoing:{event_id}")
                continue
            channel_id, _label = classify_battle_hit_channel(hit)
            if channel_id not in _STATELESS_SELECTED_CHANNELS:
                reasons.append(f"stateful_or_unsupported_channel:{event_id}:{channel_id}")
            row_evidence = inputs.evidence_by_event.get(event_id)
            if row_evidence is not None and (
                row_evidence.state_multiplier_label
                or abs(float(row_evidence.state_multiplier) - 1.0) > 1e-12
            ):
                reasons.append(f"stateful_skill_evidence:{event_id}")
            original = inputs.baseline_replay_by_event.get(event_id)
            if original is None:
                reasons.append(f"baseline_replay_missing:{event_id}")
                continue
            if any(factor.factor_id == "state_coefficient" for factor in original.factors):
                reasons.append(f"stateful_baseline_formula:{event_id}")
            branches.append(FrozenReplayBranch(
                event_id=event_id,
                critical_state=original.critical_state,
                critical_policy=original.critical_policy,
                confidence=original.confidence,
            ))

        unique_reasons = tuple(dict.fromkeys(reasons))
        return cls(
            selected_event_ids=selected,
            frozen_branches=tuple(branches),
            requires_full_axis=bool(unique_reasons),
            full_axis_reasons=unique_reasons,
        )

    def require_selected_replay(self) -> None:
        if self.requires_full_axis:
            reasons = ", ".join(self.full_axis_reasons)
            raise ValueError(f"selected hit replay requires full axis: {reasons}")

    def freeze_candidate_branches(
        self,
        results: Sequence[BattleHitReplayResult],
    ) -> tuple[BattleHitReplayResult, ...]:
        """Apply only branches established by the original full-axis audit."""

        self.require_selected_replay()
        branches: Mapping[str, FrozenReplayBranch] = {
            row.event_id: row for row in self.frozen_branches
        }
        return tuple(
            self._freeze_one(result, branches[result.event_id])
            for result in results
        )

    @staticmethod
    def _freeze_one(
        result: BattleHitReplayResult,
        branch: FrozenReplayBranch,
    ) -> BattleHitReplayResult:
        state = branch.critical_state
        if state == "critical":
            selected = result.critical_damage
        elif state in {"non_critical", "not_applicable"}:
            selected = result.non_critical_damage
        elif state == "ambiguous" and branch.critical_policy != "unknown":
            selected = result.expected_damage
        else:
            selected = None
        error = (
            None
            if selected is None
            else replay_error_percent(result.observed_damage, selected)
        )
        signed_error = (
            None
            if selected is None
            else replay_signed_error_percent(result.observed_damage, selected)
        )
        corrected_expected = (
            result.expected_damage * result.observed_damage / selected
            if result.expected_damage is not None and selected is not None and selected > 0.0
            else None
        )
        return replace(
            result,
            selected_damage=selected,
            selected_error_percent=error,
            signed_error_percent=signed_error,
            critical_state=state,
            critical_policy=branch.critical_policy,
            confidence=branch.confidence,
            corrected_expected_damage=corrected_expected,
        )


__all__ = [
    "FrozenReplayBranch",
    "PreparedReplayAuditContext",
    "PreparedReplayAuditInputs",
]
