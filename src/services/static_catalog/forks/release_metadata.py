# 维护弧盘图鉴的正式消耗投影和稳定展示顺序。
"""弧盘养成与活动目录的只读投影。

限定归属、名称与发行顺序来自注入的正式活动记录；
首发弧盘使用已确认的稳定质量、类型、名称顺序，不猜测发行日期。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.domain.static_catalog import CatalogLink
from src.domain.static_catalog_terminology import LocalizedForkCampaign
from src.services.static_catalog.mechanics.models import encode_record
from src.services.static_catalog.forks.service import (
    ForkBuffDefinition,
    ForkCatalogDetail,
    ForkCatalogSummary,
    ForkCost,
    ForkRefinementLevel,
)
from src.services.static_catalog.forks.progression_models import (
    ForkExperienceMaterialCost,
)
from src.services.progression.fork_requirements import (
    project_fork_level_requirements,
)
from src.services.static_catalog.common.terminology import (
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
    """玩家明确选定的一份完整弧盘养成状态。"""

    level: int
    breakthrough_stage: int | None
    mixing_level: int


@dataclass(frozen=True, slots=True)
class ForkProgressionMaterialRequirement:
    """聚合正式成本；数量未知时保留 None，不按零计算。"""

    item_id: str
    required_quantity: int | None
    known_quantity: int
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ForkProgressionRequirementGap:
    """保留正式来源缺项，避免将不完整证据当作精确材料数量。"""

    code: str
    source_ref: str
    item_id: str | None = None
    raw_value: str | None = None


@dataclass(frozen=True, slots=True)
class ForkProgressionRequest:
    """页面冻结的弧盘养成请求，交由公共养成编排消费。"""

    kind: str
    fork_id: str
    current: ForkProgressionState
    target: ForkProgressionState
    requirements: tuple[ForkProgressionMaterialRequirement, ...]
    requirement_gaps: tuple[ForkProgressionRequirementGap, ...]
    required_upgrade_exp: int | None


@dataclass(frozen=True, slots=True)
class ForkCatalogRoute:
    """将玩家可读动作与一个正式、可路由的资料身份绑定。"""

    label: str
    link: CatalogLink


def fork_character_catalog_link(
    character_id: int,
    *,
    owner: bool,
) -> CatalogLink:
    """以正式角色身份打开归属或兼容角色，不以显示名定位。"""

    return CatalogLink(
        domain_key="character",
        record_id=str(int(character_id)),
        relation_kind="owner" if owner else "compatible",
    )


def fork_mechanics_catalog_routes(
    refinement: ForkRefinementLevel | None,
    buffs: tuple[ForkBuffDefinition, ...],
) -> tuple[ForkCatalogRoute, ...]:
    """只有精确结构化效果身份才生成机制关联，不解析文案猜测规则。"""

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
    """保留正式 DTO 的材料 ID 与精确整数数量，不以展示名重新归并。"""

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
    """只聚合已证实的正式成本；材料或数量缺失时留下缺口。"""

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

    def add_requirement(
        item_id: str,
        quantity: int,
        source_ref: str,
        *,
        unknown_total: bool = False,
    ) -> None:
        entry = aggregated.setdefault(item_id, {
            "known": 0,
            "unknown": False,
            "sources": [],
        })
        sources = entry["sources"]
        assert isinstance(sources, list)
        sources.append(source_ref)
        entry["known"] = int(entry["known"]) + int(quantity)
        entry["unknown"] = bool(entry["unknown"]) or unknown_total

    current_stage = current.breakthrough_stage or 0
    target_stage = target.breakthrough_stage or 0
    level_projection = project_fork_level_requirements(
        detail,
        current_level=current.level,
        current_stage=current_stage,
        target_level=target.level,
        target_stage=target_stage,
    )
    incomplete_upgrade = any(
        gap.reason_code == "fork_level_exp_row_unavailable"
        for gap in level_projection.gaps
    )
    for requirement in level_projection.experience_materials:
        add_requirement(
            requirement.item_id,
            requirement.required_quantity,
            "level_materials",
            unknown_total=incomplete_upgrade,
        )
    for requirement in level_projection.experience_costs:
        add_requirement(
            requirement.item_id,
            requirement.required_quantity,
            "level_material_use_cost",
            unknown_total=incomplete_upgrade,
        )
    for requirement in level_projection.breakthrough_materials:
        add_requirement(
            requirement.item_id,
            requirement.required_quantity,
            "breakthrough_materials",
        )
    for requirement in level_projection.breakthrough_costs:
        add_requirement(
            requirement.item_id,
            requirement.required_quantity,
            "breakthrough_costs",
        )
    gaps.extend(
        ForkProgressionRequirementGap(
            code=gap.reason_code,
            source_ref=(
                f"level:{gap.level}" if gap.level is not None else "progression"
            ),
            item_id=gap.item_id,
        )
        for gap in level_projection.gaps
    )

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

    required_upgrade_exp: int | None = (
        None if incomplete_upgrade else level_projection.required_experience
    )

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
    """通过公共术语投影材料名称；正式 ID 仍用于关联而非主要界面文案。"""

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
        costs: Iterable[ForkCost | ForkExperienceMaterialCost],
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
    """限定弧盘按正式活动记录排序，其余首发弧盘保持稳定质量、类型和名称顺序。"""

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
