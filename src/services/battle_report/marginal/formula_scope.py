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
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.replay.buff_cache import BattleHitBuffProjectionCache
from src.services.battle_report.damage.composition import (
    has_hit_source_evidence,
)
from src.services.battle_report.targets.instance_mapping import (
    BattleTargetInstanceMappingService,
)
from src.services.battle_report.damage.weave_source import (
    BattleWeaveSourceIndex, BattleWeaveSourceLookup, find_paired_weave_source_hit,
)


@dataclass(frozen=True, slots=True)
class BattleMarginalFormulaScope:
    outgoing_hits: tuple[BattleAnalysisHit, ...]
    replays: Mapping[str, BattleHitReplayResult]
    raw_projections: Mapping[str, BattleHitBuffProjection]
    formula_projections: Mapping[str, BattleHitBuffProjection]
    target_conditions: Mapping[str, BattleTargetCondition | None]
    role_hits: tuple[BattleAnalysisHit, ...]
    weave_sources: BattleWeaveSourceIndex


def formula_panel_character_id(
    hit: BattleAnalysisHit,
    replay: BattleHitReplayResult | None,
) -> int | None:
    """只解析公式面板 owner，不改原始伤害角色归属。"""

    if replay is not None and replay.formula_panel_character_id is not None:
        return replay.formula_panel_character_id
    return hit.character_id


def project_replay_formula_hit(
    hit: BattleAnalysisHit,
    replay: BattleHitReplayResult | None,
) -> BattleAnalysisHit:
    """仅重建重放使用的临时公式视图；原始逐击对象仍保留。"""

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
    *, projection_memo: BattleBuffProjectionMemo | None = None,
) -> BattleMarginalFormulaScope:
    """同时准备原始/公式投影与所选面板范围，不混同两类角色身份。"""

    outgoing_hits = tuple(
        hit for hit in analysis.hits if hit.direction == "outgoing"
    )
    replays = {row.event_id: row for row in analysis.hit_replays}
    weave_sources = BattleWeaveSourceIndex(outgoing_hits)
    projection_cache = BattleHitBuffProjectionCache(
        BattleBuffIntervalIndex(analysis.buff_intervals), memo=projection_memo,
    )
    formula_hits = {
        hit.event_id: project_replay_formula_hit(hit, replays.get(hit.event_id))
        for hit in outgoing_hits
    }
    projection_cache.prepare((*outgoing_hits, *formula_hits.values()))
    raw_projections = {hit.event_id: projection_cache.project(hit) for hit in outgoing_hits}
    formula_projections = {
        event_id: projection_cache.project(hit)
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
            weave_sources,
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
        weave_sources=weave_sources,
    )


def property_owner_matches(
    property_id: str,
    hit: BattleAnalysisHit,
    all_hits: BattleWeaveSourceLookup,
    replays: Mapping[str, BattleHitReplayResult],
    *,
    character_id: int,
    weave_source_properties: frozenset[str] | set[str],
) -> bool:
    """按既有逐击和配对来源判定维度归属，不从显示名猜 owner。"""

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
    statuses = {marginal_quantification_status(primary)}
    statuses.update(
        marginal_quantification_status(anchor_quantification(hit))
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


def marginal_quantification_status(row: object | None) -> QuantificationStatus:
    """合一原两处分母状态读取；None 默认与未知显式值均沿用原口径。"""
    if row is None:
        return "complete"
    if isinstance(row, BattleCounterfactualRatio):
        return row.status
    return getattr(getattr(row, "quantification", None), "status", "complete")


def _belongs_to_panel_scope(
    hit: BattleAnalysisHit,
    all_hits: BattleWeaveSourceLookup,
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
    "marginal_quantification_status",
    "formula_damage_attribute",
    "formula_panel_character_id",
    "prepare_marginal_formula_scope",
    "project_replay_formula_hit",
    "property_owner_matches",
]
