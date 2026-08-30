# 维护弧盘图鉴的正式消耗投影和稳定展示顺序。
"""Qt-free helpers for fork progression and campaign-backed catalog order.

Limited membership, titles and release order are required injected
``LocalizedForkCampaign`` records.  The remaining launch batch uses the
confirmed stable quality/type/name order without inventing release dates.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.domain.static_catalog import CatalogLink
from src.domain.static_catalog_terminology import LocalizedForkCampaign
from src.services.static_catalog_mechanics_models import encode_record
from src.services.static_catalog_fork_service import (
    ForkBuffDefinition,
    ForkCatalogDetail,
    ForkCatalogSummary,
    ForkCost,
    ForkRefinementLevel,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


@dataclass(frozen=True, slots=True)
class ForkCostDisplay:
    display_name: str
    amount_text: str
    raw_item_id: str
    canonical_item_id: str | None = None
    text_table: str | None = None
    text_key: str | None = None
    resolved_locale: str | None = None


@dataclass(frozen=True, slots=True)
class ForkProgressionState:
    """One complete fork progression state selected by the player."""

    level: int
    breakthrough_stage: int | None
    mixing_level: int


@dataclass(frozen=True, slots=True)
class ForkProgressionMaterialRequirement:
    """Aggregated official cost; ``None`` preserves an unknown quantity."""

    item_id: str
    required_quantity: int | None
    known_quantity: int
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ForkProgressionRequirementGap:
    """A formal source row that cannot become an exact material quantity."""

    code: str
    source_ref: str
    item_id: str | None = None
    raw_value: str | None = None


@dataclass(frozen=True, slots=True)
class ForkProgressionRequest:
    """Page-side request handed to the shared progression orchestration."""

    kind: str
    fork_id: str
    current: ForkProgressionState
    target: ForkProgressionState
    requirements: tuple[ForkProgressionMaterialRequirement, ...]
    requirement_gaps: tuple[ForkProgressionRequirementGap, ...]
    required_upgrade_exp: int | None


@dataclass(frozen=True, slots=True)
class ForkCatalogRoute:
    """Player-readable action paired with one routable catalog identity."""

    label: str
    link: CatalogLink


def fork_character_catalog_link(
    character_id: int,
    *,
    owner: bool,
) -> CatalogLink:
    """Route an owner or compatible character through the shared catalog."""

    return CatalogLink(
        domain_key="character",
        record_id=str(int(character_id)),
        relation_kind="owner" if owner else "compatible",
    )


def fork_mechanics_catalog_routes(
    refinement: ForkRefinementLevel | None,
    buffs: tuple[ForkBuffDefinition, ...],
) -> tuple[ForkCatalogRoute, ...]:
    """Project only exact structured effect identities into mechanics links."""

    routes: list[ForkCatalogRoute] = []
    if refinement is not None and refinement.projected_effect_definition_id:
        routes.append(ForkCatalogRoute(
            label="查看混频效果机制",
            link=CatalogLink(
                domain_key="combat_mechanics",
                record_id=encode_record(
                    "effect",
                    f"combat_effect\x1f{refinement.projected_effect_definition_id}",
                ),
                relation_kind="mixing_effect",
            ),
        ))
    for buff in buffs:
        if not buff.target_available or not buff.asset_path:
            continue
        routes.append(ForkCatalogRoute(
            label="查看 Buff 机制",
            link=CatalogLink(
                domain_key="combat_mechanics",
                record_id=encode_record("effect", f"buff\x1f{buff.asset_path}"),
                relation_kind="buff",
            ),
        ))
    unique: dict[CatalogLink, ForkCatalogRoute] = {}
    for route in routes:
        unique.setdefault(route.link, route)
    return tuple(unique.values())


def parse_fork_costs(raw_value: str | None) -> tuple[ForkCost, ...]:
    """Preserve stable IDs and exact integer quantities from a formal DTO field."""

    values: list[ForkCost] = []
    for part in filter(None, (
        item.strip() for item in str(raw_value or "").split(",")
    )):
        item_id, separator, amount = part.partition(":")
        parsed_amount: int | None = None
        if separator:
            try:
                parsed_amount = int(amount)
            except ValueError:
                parsed_amount = None
        values.append(ForkCost(
            item_id=item_id,
            amount=parsed_amount,
            raw_value=amount if separator else part,
        ))
    return tuple(values)


def build_fork_progression_request(
    detail: ForkCatalogDetail,
    *,
    current: ForkProgressionState,
    target: ForkProgressionState,
) -> ForkProgressionRequest:
    """Aggregate only formal DTO costs; never infer a missing item or quantity."""

    current_rank = (current.level, current.breakthrough_stage or 0)
    target_rank = (target.level, target.breakthrough_stage or 0)
    if target_rank < current_rank or target.mixing_level < current.mixing_level:
        raise ValueError("目标养成状态不能低于当前状态")

    aggregated: dict[str, dict[str, object]] = {}
    gaps: list[ForkProgressionRequirementGap] = []

    def add_cost(cost: ForkCost, source_ref: str) -> None:
        item_id = str(cost.item_id or "").strip()
        if not item_id:
            gaps.append(ForkProgressionRequirementGap(
                code="stable_item_id_unavailable",
                source_ref=source_ref,
                raw_value=cost.raw_value,
            ))
            return
        entry = aggregated.setdefault(item_id, {
            "known": 0,
            "unknown": False,
            "sources": [],
        })
        sources = entry["sources"]
        assert isinstance(sources, list)
        sources.append(source_ref)
        amount = cost.amount
        if amount is None or amount <= 0:
            entry["unknown"] = True
            gaps.append(ForkProgressionRequirementGap(
                code="official_quantity_unavailable",
                source_ref=source_ref,
                item_id=item_id,
                raw_value=cost.raw_value,
            ))
            return
        entry["known"] = int(entry["known"]) + amount

    current_stage = current.breakthrough_stage or 0
    target_stage = target.breakthrough_stage or 0
    crossed_stages = {
        row.stage: row for row in detail.breakthroughs
        if current_stage < row.stage <= target_stage
    }
    for stage in range(current_stage + 1, target_stage + 1):
        row = crossed_stages.get(stage)
        source_ref = f"breakthrough:{stage}"
        if row is None:
            gaps.append(ForkProgressionRequirementGap(
                code="breakthrough_cost_row_unavailable",
                source_ref=source_ref,
            ))
            continue
        costs = (*row.item_costs, *row.gold_costs)
        if not costs:
            gaps.append(ForkProgressionRequirementGap(
                code="breakthrough_cost_unavailable",
                source_ref=source_ref,
            ))
        for cost in costs:
            add_cost(cost, source_ref)

    refinements = {
        row.level: row for row in detail.refinement_levels
        if current.mixing_level < row.level <= target.mixing_level
    }
    for level in range(current.mixing_level + 1, target.mixing_level + 1):
        row = refinements.get(level)
        source_ref = f"mixing:{level}"
        if row is None or not row.need_gold_raw:
            gaps.append(ForkProgressionRequirementGap(
                code="mixing_cost_unavailable",
                source_ref=source_ref,
            ))
            continue
        for cost in parse_fork_costs(row.need_gold_raw):
            add_cost(cost, source_ref)

    required_upgrade_exp: int | None = 0
    if target.level > current.level:
        growth = {
            row.level: row.need_exp for row in detail.growth_levels
            if current.level < row.level <= target.level
        }
        if len(growth) != target.level - current.level:
            required_upgrade_exp = None
            gaps.append(ForkProgressionRequirementGap(
                code="upgrade_exp_rows_unavailable",
                source_ref=f"levels:{current.level + 1}-{target.level}",
            ))
        else:
            required_upgrade_exp = sum(growth.values())
        gaps.append(ForkProgressionRequirementGap(
            code="level_material_relation_unavailable",
            source_ref=f"levels:{current.level + 1}-{target.level}",
        ))

    requirements = tuple(
        ForkProgressionMaterialRequirement(
            item_id=item_id,
            required_quantity=(
                None if bool(entry["unknown"]) else int(entry["known"])
            ),
            known_quantity=int(entry["known"]),
            source_refs=tuple(dict.fromkeys(entry["sources"])),
        )
        for item_id, entry in sorted(aggregated.items())
    )
    return ForkProgressionRequest(
        kind="fork_progression",
        fork_id=detail.summary.fork_id,
        current=current,
        target=target,
        requirements=requirements,
        requirement_gaps=tuple(gaps),
        required_upgrade_exp=required_upgrade_exp,
    )


class ForkItemDisplayNameService:
    """Project shared formal terms while keeping stable IDs out of the main UI."""

    UNKNOWN_NAME = "名称暂未提供"

    def __init__(
        self,
        terminology_service: StaticCatalogTerminologyService | None,
    ) -> None:
        self._terminology = terminology_service

    def quality_name(self, quality_id: str) -> str:
        if self._terminology is None:
            return "品质名称暂未提供"
        term = self._terminology.resolve("item_quality", str(quality_id))
        if not term.display_name:
            return "品质名称暂未提供"
        return (
            term.display_name
            if term.display_name.endswith("级")
            else f"{term.display_name}级"
        )

    def present_costs(
        self,
        costs: Iterable[ForkCost],
    ) -> tuple[ForkCostDisplay, ...]:
        presented = []
        for cost in costs:
            item_id = str(cost.item_id)
            term = (
                self._terminology.resolve(
                    "item",
                    item_id,
                    context="progression_cost",
                )
                if self._terminology is not None
                else None
            )
            presented.append(ForkCostDisplay(
                display_name=(term.display_name if term else None) or self.UNKNOWN_NAME,
                amount_text=str(
                    cost.amount if cost.amount is not None else cost.raw_value
                ),
                raw_item_id=item_id,
                canonical_item_id=term.canonical_id if term else None,
                text_table=term.text_table if term else None,
                text_key=term.text_key if term else None,
                resolved_locale=term.resolved_locale if term else None,
            ))
        return tuple(presented)

    def present_raw(self, raw_value: str | None) -> tuple[ForkCostDisplay, ...]:
        return self.present_costs(parse_fork_costs(raw_value))

    @staticmethod
    def player_text(costs: Iterable[ForkCostDisplay]) -> str:
        return "、".join(
            f"{cost.display_name} × {cost.amount_text}" for cost in costs
        )

    @staticmethod
    def raw_id_text(costs: Iterable[ForkCostDisplay]) -> str:
        rows = []
        for cost in costs:
            identity = cost.raw_item_id
            if cost.canonical_item_id and cost.canonical_item_id != cost.raw_item_id:
                identity += f" → {cost.canonical_item_id}"
            if cost.text_table and cost.text_key:
                identity += f" · {cost.text_table} / {cost.text_key}"
            if cost.resolved_locale:
                identity += f" · {cost.resolved_locale}"
            rows.append(identity)
        return "、".join(rows)


_QUALITY_ORDER = {"ORANGE": 0, "PURPLE": 1, "BLUE": 2}


def sort_fork_catalog(
    summaries: Iterable[ForkCatalogSummary],
    campaigns: tuple[LocalizedForkCampaign, ...],
) -> tuple[ForkCatalogSummary, ...]:
    """Put limited forks first, then keep the launch batch deterministic."""

    campaign_order: dict[str, int] = {}
    for item in campaigns:
        campaign_order[item.featured_fork_id] = max(
            item.release_ordinal,
            campaign_order.get(item.featured_fork_id, -1),
        )

    def key(item: ForkCatalogSummary) -> tuple[object, ...]:
        release_ordinal = campaign_order.get(item.fork_id)
        if release_ordinal is not None:
            return (0, -release_ordinal, item.fork_id)
        return (
            1,
            _QUALITY_ORDER.get(item.quality.upper(), 99),
            item.fork_type_id if item.fork_type_id is not None else 999,
            item.name_zh,
            item.fork_id,
        )

    return tuple(sorted(summaries, key=key))


__all__ = [
    "ForkCatalogRoute",
    "ForkCostDisplay",
    "ForkItemDisplayNameService",
    "ForkProgressionMaterialRequirement",
    "ForkProgressionRequest",
    "ForkProgressionRequirementGap",
    "ForkProgressionState",
    "build_fork_progression_request",
    "fork_character_catalog_link",
    "fork_mechanics_catalog_routes",
    "parse_fork_costs",
    "sort_fork_catalog",
]
