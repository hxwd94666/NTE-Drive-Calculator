# 为配装收益冻结公式面板逐击、团队倾陷份额与生命结算归属。
"""Selected-panel damage shares used only by marginal-benefit presentation."""

from __future__ import annotations

from dataclasses import dataclass, replace

from src.domain.battle_counterfactual import (
    BattleBuildCounterfactual,
    BattleBuildHitCounterfactual,
    BattleBuildVitalCounterfactual,
)
from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_marginal_calculation_support import (
    topple_character_contribution,
)
from src.services.battle_marginal_formula_scope import (
    prepare_marginal_formula_scope,
)


@dataclass(frozen=True, slots=True)
class BattleMarginalBenefitHitShare:
    event_id: str
    baseline_share_ratio: float
    is_team_topple: bool


@dataclass(frozen=True, slots=True)
class BattleMarginalBenefitRoleScope:
    character_id: int
    hit_shares: tuple[BattleMarginalBenefitHitShare, ...]
    vital_event_ids: frozenset[str]


def prepare_marginal_benefit_role_scope(
    analysis: BattleAnalysisSnapshot,
    character_id: int,
) -> BattleMarginalBenefitRoleScope:
    """Freeze stable event ownership without rewriting Core's raw actor IDs."""

    formula_scope = prepare_marginal_formula_scope(analysis, character_id)
    panel_event_ids = {hit.event_id for hit in formula_scope.role_hits}
    shares: dict[str, BattleMarginalBenefitHitShare] = {}
    for hit in formula_scope.outgoing_hits:
        replay = formula_scope.replays.get(hit.event_id)
        has_topple_cells = bool(
            replay is not None
            and any(
                factor.factor_id.startswith("topple_character:")
                for factor in replay.factors
            )
        )
        if has_topple_cells:
            contribution = topple_character_contribution(
                replay,
                character_id=character_id,
                team_topple_damage=float(hit.damage),
            )
            if contribution is None or hit.damage <= 0.0:
                continue
            shares[hit.event_id] = BattleMarginalBenefitHitShare(
                event_id=hit.event_id,
                baseline_share_ratio=contribution / float(hit.damage),
                is_team_topple=True,
            )
        elif hit.event_id in panel_event_ids:
            shares[hit.event_id] = BattleMarginalBenefitHitShare(
                event_id=hit.event_id,
                baseline_share_ratio=1.0,
                is_team_topple=False,
            )
    vital_event_ids = frozenset(
        event.event_id
        for event in (*analysis.max_hp_events, *analysis.estimated_max_hp_events)
        if event.source_character_id == character_id
    )
    return BattleMarginalBenefitRoleScope(
        character_id=character_id,
        hit_shares=tuple(shares.values()),
        vital_event_ids=vital_event_ids,
    )


def marginal_benefit_role_rows(
    comparison: BattleBuildCounterfactual,
    scope: BattleMarginalBenefitRoleScope,
) -> tuple[BattleBuildHitCounterfactual | BattleBuildVitalCounterfactual, ...]:
    """Project team packets onto the selected formula panel's owned share."""

    shares = {row.event_id: row for row in scope.hit_shares}
    hits = tuple(
        _shared_hit(row, shares[row.event_id])
        for row in comparison.hits
        if row.event_id in shares
    )
    vital = tuple(
        row
        for row in comparison.vital_events
        if row.event_id in scope.vital_event_ids
    )
    return (*hits, *vital)


def observed_marginal_benefit_role_damage(
    analysis: BattleAnalysisSnapshot,
    scope: BattleMarginalBenefitRoleScope,
) -> float:
    """Return the endpoint damage represented by the frozen selected scope."""

    shares = {row.event_id: row for row in scope.hit_shares}
    hit_damage = sum(
        float(hit.damage) * shares[hit.event_id].baseline_share_ratio
        for hit in analysis.hits
        if hit.event_id in shares
    )
    vital_damage = sum(
        float(event.effective_hp_loss)
        for event in analysis.max_hp_events
        if event.event_id in scope.vital_event_ids
    )
    return hit_damage + vital_damage


def _shared_hit(
    row: BattleBuildHitCounterfactual,
    share: BattleMarginalBenefitHitShare,
) -> BattleBuildHitCounterfactual:
    baseline = float(row.baseline_damage) * share.baseline_share_ratio
    if not share.is_team_topple:
        return row

    def project(value: float | None) -> float | None:
        if value is None:
            return None
        return baseline + float(value) - float(row.baseline_damage)

    return replace(
        row,
        baseline_damage=baseline,
        known_projection_damage=project(row.known_projection_damage),
        candidate_damage=project(getattr(row, "candidate_damage", None)),
        heuristic_projection_damage=project(
            getattr(row, "heuristic_projection_damage", None),
        ),
    )


__all__ = [
    "BattleMarginalBenefitRoleScope",
    "marginal_benefit_role_rows",
    "observed_marginal_benefit_role_damage",
    "prepare_marginal_benefit_role_scope",
]
