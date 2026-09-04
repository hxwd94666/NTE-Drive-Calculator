# 为属性边际分离原始伤害归属、公式面板归属与覆纹来源归属。
"""Formula-owner projections used by battle marginal calculations."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleTargetCondition,
)
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    QuantificationStatus,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_damage_composition_service import (
    has_hit_source_evidence,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)
from src.services.battle_weave_source_service import find_paired_weave_source_hit


@dataclass(frozen=True, slots=True)
class BattleMarginalFormulaScope:
    outgoing_hits: tuple[BattleAnalysisHit, ...]
    replays: Mapping[str, BattleHitReplayResult]
    raw_projections: Mapping[str, BattleHitBuffProjection]
    formula_projections: Mapping[str, BattleHitBuffProjection]
    target_conditions: Mapping[str, BattleTargetCondition | None]
    role_hits: tuple[BattleAnalysisHit, ...]


def formula_panel_character_id(
    hit: BattleAnalysisHit,
    replay: BattleHitReplayResult | None,
) -> int | None:
    """Return the panel owner without changing raw damage attribution."""

    if replay is not None and replay.formula_panel_character_id is not None:
        return replay.formula_panel_character_id
    return hit.character_id


def project_replay_formula_hit(
    hit: BattleAnalysisHit,
    replay: BattleHitReplayResult | None,
) -> BattleAnalysisHit:
    """Rebuild the ephemeral formula consumer view retained by replay."""

    if replay is None:
        return hit
    panel_character_id = formula_panel_character_id(hit, replay)
    return replace(
        hit,
        character_id=panel_character_id,
        damage_attribute=replay.formula_damage_attribute or hit.damage_attribute,
        is_formal_follow_up=(
            replay.formula_is_formal_follow_up or hit.is_formal_follow_up
        ),
        target_has_weave=replay.formula_target_has_weave or hit.target_has_weave,
        formula_context_kind=replay.formula_context_kind or hit.formula_context_kind,
        formula_context_confidence=(
            replay.formula_context_confidence or hit.formula_context_confidence
        ),
        formula_context_basis=replay.formula_context_basis or hit.formula_context_basis,
    )


def formula_damage_attribute(
    hit: BattleAnalysisHit,
    replay: BattleHitReplayResult | None,
) -> str:
    return str(
        getattr(replay, "formula_damage_attribute", "") or hit.damage_attribute
    ).casefold()


def prepare_marginal_formula_scope(
    analysis: BattleAnalysisSnapshot,
    character_id: int,
) -> BattleMarginalFormulaScope:
    """Prepare both raw and formula projections plus the selected panel scope."""

    outgoing_hits = tuple(
        hit for hit in analysis.hits if hit.direction == "outgoing"
    )
    replays = {row.event_id: row for row in analysis.hit_replays}
    raw_projections = {
        hit.event_id: BattleBuffAttributeProjectionService.project_hit(
            hit,
            analysis.buff_intervals,
        )
        for hit in outgoing_hits
    }
    formula_hits = {
        hit.event_id: project_replay_formula_hit(hit, replays.get(hit.event_id))
        for hit in outgoing_hits
    }
    formula_projections = {
        event_id: BattleBuffAttributeProjectionService.project_hit(
            hit,
            analysis.buff_intervals,
        )
        for event_id, hit in formula_hits.items()
    }
    target_conditions = {
        hit.event_id: BattleTargetInstanceMappingService.analysis_for_hit(
            analysis,
            hit,
        ).target_condition
        for hit in outgoing_hits
    }
    role_hits = tuple(
        hit
        for hit in outgoing_hits
        if _belongs_to_panel_scope(
            hit,
            outgoing_hits,
            replays,
            character_id=character_id,
        )
    )
    return BattleMarginalFormulaScope(
        outgoing_hits=outgoing_hits,
        replays=replays,
        raw_projections=raw_projections,
        formula_projections=formula_projections,
        target_conditions=target_conditions,
        role_hits=role_hits,
    )


def property_owner_matches(
    property_id: str,
    hit: BattleAnalysisHit,
    all_hits: Sequence[BattleAnalysisHit],
    replays: Mapping[str, BattleHitReplayResult],
    *,
    character_id: int,
    weave_source_properties: frozenset[str] | set[str],
) -> bool:
    """Return whether the selected panel owns this hit's changed dimension."""

    if hit.classification != "weave":
        return formula_panel_character_id(hit, replays.get(hit.event_id)) == character_id
    source = find_paired_weave_source_hit(hit, all_hits)
    if property_id == "MagBase":
        owner = hit.character_id if source is None else source.character_id
        return owner == character_id
    if property_id in weave_source_properties:
        if source is None:
            return formula_panel_character_id(hit, replays.get(hit.event_id)) == character_id
        return formula_panel_character_id(
            source,
            replays.get(source.event_id),
        ) == character_id
    return formula_panel_character_id(hit, replays.get(hit.event_id)) == character_id


def extend_panel_denominator(
    damage: float,
    primary: object | None,
    hits: Sequence[BattleAnalysisHit],
    character_id: int,
    anchor_damage: Callable[[BattleAnalysisHit], float],
    anchor_quantification: Callable[
        [BattleAnalysisHit], BattleCounterfactualRatio | None
    ],
) -> tuple[float, QuantificationStatus]:
    """Close the formula-panel denominator over every bucket-eligible hit.

    Composition deliberately keeps source-less packets in the public unknown
    bucket.  Marginal analysis still has to retain such a raw role-owned packet
    as unavailable evidence; otherwise its unavailable bucket would have no
    matching denominator damage.  Hits controlled by another raw actor but by
    this formula panel are the second supplemental class.
    """

    supplemental = tuple(
        hit
        for hit in hits
        if hit.character_id != character_id or not has_hit_source_evidence(hit)
    )
    statuses = {_quantification_status(primary)}
    statuses.update(
        _quantification_status(anchor_quantification(hit))
        for hit in supplemental
    )
    if "unavailable" in statuses:
        status: QuantificationStatus = "unavailable"
    elif "partial" in statuses:
        status = "partial"
    elif statuses == {"not_applicable"}:
        status = "not_applicable"
    else:
        status = "complete"
    return damage + sum(anchor_damage(hit) for hit in supplemental), status


def _quantification_status(row: object | None) -> QuantificationStatus:
    if row is None:
        return "complete"
    if isinstance(row, BattleCounterfactualRatio):
        return row.status
    return getattr(getattr(row, "quantification", None), "status", "complete")


def _belongs_to_panel_scope(
    hit: BattleAnalysisHit,
    all_hits: Sequence[BattleAnalysisHit],
    replays: Mapping[str, BattleHitReplayResult],
    *,
    character_id: int,
) -> bool:
    if hit.classification != "weave":
        return formula_panel_character_id(hit, replays.get(hit.event_id)) == character_id
    source = find_paired_weave_source_hit(hit, all_hits)
    if source is None:
        return formula_panel_character_id(hit, replays.get(hit.event_id)) == character_id
    return (
        source.character_id == character_id
        or formula_panel_character_id(source, replays.get(source.event_id)) == character_id
    )


__all__ = [
    "BattleMarginalFormulaScope",
    "extend_panel_denominator",
    "formula_damage_attribute",
    "formula_panel_character_id",
    "prepare_marginal_formula_scope",
    "project_replay_formula_hit",
    "property_owner_matches",
]
