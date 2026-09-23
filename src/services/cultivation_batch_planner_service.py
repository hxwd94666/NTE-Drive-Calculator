# 编排多角色养成目标、共享已有材料与跨角色最低体力。
"""Qt-free batch cultivation planning built on the single-target service."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from time import perf_counter

from src.domain.progression_material_conversion import allocate_owned
from src.domain.progression_stamina import FarmingStage, ProgressionStaminaResult
from src.services.cultivation_planner_service import (
    CultivationMaterial,
    CultivationPlan,
    CultivationPlannerService,
    CultivationRequest,
    CultivationSectionStamina,
    CultivationStaminaPlan,
)
from src.services.cultivation_stamina_planner import (
    calculate_stamina_result,
    normalize_owned_quantities,
    stamina_material_ids,
)
from src.utils.cultivation_trace import trace_cultivation


@dataclass(frozen=True, slots=True)
class CultivationTargetDraft:
    line_id: str
    character_id: int
    request: CultivationRequest


@dataclass(frozen=True, slots=True)
class CultivationBatchRequest:
    account_id: str
    generation: object
    dataset_identity: str
    hunter_level: int
    effective_identification_level: int | None
    ordered_targets: tuple[CultivationTargetDraft, ...]
    owned_quantities: tuple[tuple[str, int], ...] = ()
    trace_id: int = 0


@dataclass(frozen=True, slots=True)
class CultivationMaterialSource:
    line_id: str
    section_index: int
    section_label: str
    item_id: str
    required_quantity: int
    allocated_owned: int
    remaining_quantity: int


@dataclass(frozen=True, slots=True)
class CultivationBatchGap:
    line_id: str | None
    reason_code: str
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class CultivationTargetPlan:
    line_id: str
    character_id: int
    plan: CultivationPlan
    owned_allocation: tuple[tuple[str, int], ...]
    remaining_totals: tuple[CultivationMaterial, ...]
    stamina: CultivationStaminaPlan


@dataclass(frozen=True, slots=True)
class CultivationBatchPlan:
    account_id: str
    generation: object
    dataset_identity: str
    target_plans: tuple[CultivationTargetPlan, ...]
    merged_totals: tuple[CultivationMaterial, ...]
    remaining_totals: tuple[CultivationMaterial, ...]
    combined_stamina: ProgressionStaminaResult
    source_ledger: tuple[CultivationMaterialSource, ...]
    gaps: tuple[CultivationBatchGap, ...]
    separate_stamina_total: int | None
    saved_stamina: int | None
    stamina_item_ids: frozenset[str] = frozenset()
    owned_inputs: tuple[CultivationMaterial, ...] = ()
    trace_id: int = 0


class CultivationBatchPlannerService:
    """Merge ordered target plans while consuming account materials once."""

    def __init__(self, single_service: CultivationPlannerService) -> None:
        self._single = single_service

    def calculate(self, request: CultivationBatchRequest) -> CultivationBatchPlan:
        started = perf_counter()
        trace_cultivation(request.trace_id, "worker.begin", targets=len(request.ordered_targets))
        _validate_request(request)
        owned = normalize_owned_quantities(dict(request.owned_quantities))
        stages = self._single.load_farming_stages()
        trace_cultivation(request.trace_id, "worker.stages_loaded", stages=len(stages))
        raw_plans_list = []
        for index, target in enumerate(request.ordered_targets, start=1):
            raw_plans_list.append((target, self._single.calculate(target.request)))
            trace_cultivation(request.trace_id, "worker.target_planned", index=index)
        raw_plans = tuple(raw_plans_list)
        merged = _merge_materials(plan.totals for _target, plan in raw_plans)
        input_catalog = _merge_materials(
            plan.owned_inputs or plan.totals for _target, plan in raw_plans
        )
        available = dict(owned)
        target_plans: list[CultivationTargetPlan] = []
        ledger: list[CultivationMaterialSource] = []
        gaps: list[CultivationBatchGap] = []
        for target, plan in raw_plans:
            allocated, sources = _allocate_plan(target.line_id, plan, available)
            ledger.extend(sources)
            for gap in plan.gaps:
                gaps.append(CultivationBatchGap(
                    target.line_id,
                    gap.reason_code,
                    gap.item_id,
                ))
            stamina = _calculate_target_stamina(
                plan,
                allocated,
                stages,
                hunter_level=request.hunter_level,
                identification_level=request.effective_identification_level,
            )
            target_plans.append(CultivationTargetPlan(
                line_id=target.line_id,
                character_id=target.character_id,
                plan=plan,
                owned_allocation=tuple(
                    (item_id, quantity)
                    for item_id, quantity in allocated.items()
                    if quantity
                ),
                remaining_totals=_subtract_materials(plan.totals, allocated),
                stamina=stamina,
            ))
        combined = calculate_stamina_result(
            merged,
            owned,
            stages,
            hunter_level=request.hunter_level,
            effective_identification_level=request.effective_identification_level,
        )
        trace_cultivation(
            request.trace_id, "worker.combined_solved",
            elapsed_ms=int((perf_counter() - started) * 1000),
            materials=len(merged),
        )
        separate = _separate_stamina_total(target_plans)
        saved = (
            max(0, separate - combined.total_stamina)
            if separate is not None and combined.total_stamina is not None
            else None
        )
        for gap in combined.gaps:
            gaps.append(CultivationBatchGap(None, gap))
        combined_allocated = allocate_owned(
            {material.item_id: material.quantity for material in merged},
            dict(owned),
        )
        return CultivationBatchPlan(
            account_id=request.account_id,
            generation=request.generation,
            dataset_identity=request.dataset_identity,
            target_plans=tuple(target_plans),
            merged_totals=merged,
            remaining_totals=_subtract_materials(merged, combined_allocated),
            combined_stamina=combined,
            source_ledger=tuple(ledger),
            gaps=tuple(gaps),
            separate_stamina_total=separate,
            saved_stamina=saved,
            stamina_item_ids=stamina_material_ids(stages),
            owned_inputs=input_catalog,
            trace_id=request.trace_id,
        )


def _validate_request(request: CultivationBatchRequest) -> None:
    targets = request.ordered_targets
    if not targets:
        raise ValueError("请至少添加一个角色目标")
    line_ids = [target.line_id.strip() for target in targets]
    if not all(line_ids) or len(set(line_ids)) != len(line_ids):
        raise ValueError("多角色目标的页面身份必须非空且唯一")
    character_ids = [target.character_id for target in targets]
    if len(set(character_ids)) != len(character_ids):
        raise ValueError("同一个角色只能加入一次")
    for target in targets:
        if target.character_id != target.request.character_id:
            raise ValueError("角色目标与养成请求身份不一致")


def _merge_materials(
    groups: Iterable[tuple[CultivationMaterial, ...]],
) -> tuple[CultivationMaterial, ...]:
    quantities: dict[str, int] = defaultdict(int)
    identities: dict[str, CultivationMaterial] = {}
    for materials in groups:
        for material in materials:
            quantities[material.item_id] += material.quantity
            identities.setdefault(material.item_id, material)
    return tuple(
        _material_with_quantity(identities[item_id], quantity)
        for item_id, quantity in quantities.items()
    )


def _allocate_plan(
    line_id: str,
    plan: CultivationPlan,
    available: dict[str, int],
) -> tuple[dict[str, int], tuple[CultivationMaterialSource, ...]]:
    allocated: dict[str, int] = defaultdict(int)
    rows: list[CultivationMaterialSource] = []
    for section_index, section in enumerate(plan.sections):
        section_allocation = allocate_owned(
            {material.item_id: material.quantity for material in section.materials},
            available,
        )
        for material in section.materials:
            quantity = section_allocation.get(material.item_id, 0)
            allocated[material.item_id] += quantity
            rows.append(CultivationMaterialSource(
                line_id=line_id,
                section_index=section_index,
                section_label=section.label,
                item_id=material.item_id,
                required_quantity=material.quantity,
                allocated_owned=quantity,
                remaining_quantity=material.quantity - quantity,
            ))
    return dict(allocated), tuple(rows)


def _calculate_target_stamina(
    plan: CultivationPlan,
    allocated: dict[str, int],
    stages: tuple[FarmingStage, ...],
    *,
    hunter_level: int,
    identification_level: int | None,
) -> CultivationStaminaPlan:
    total = calculate_stamina_result(
        plan.totals,
        allocated,
        stages,
        hunter_level=hunter_level,
        effective_identification_level=identification_level,
    )
    available = dict(allocated)
    sections: list[CultivationSectionStamina] = []
    for section in plan.sections:
        section_owned = allocate_owned(
            {material.item_id: material.quantity for material in section.materials},
            available,
        )
        sections.append(CultivationSectionStamina(
            section.label,
            calculate_stamina_result(
                section.materials,
                section_owned,
                stages,
                hunter_level=hunter_level,
                effective_identification_level=identification_level,
            ),
        ))
    return CultivationStaminaPlan(total, tuple(sections))


def _subtract_materials(
    materials: tuple[CultivationMaterial, ...],
    owned: dict[str, int],
) -> tuple[CultivationMaterial, ...]:
    result: list[CultivationMaterial] = []
    for material in materials:
        quantity = max(0, material.quantity - owned.get(material.item_id, 0))
        if quantity:
            result.append(_material_with_quantity(material, quantity))
    return tuple(result)


def _material_with_quantity(
    material: CultivationMaterial,
    quantity: int,
) -> CultivationMaterial:
    return CultivationMaterial(
        material.item_id,
        material.name,
        quantity,
        material.quality,
        material.icon_path,
    )


def _separate_stamina_total(
    targets: list[CultivationTargetPlan],
) -> int | None:
    totals = [target.stamina.total.total_stamina for target in targets]
    if any(total is None for total in totals):
        return None
    return sum(int(total) for total in totals)


__all__ = [
    "CultivationBatchGap",
    "CultivationBatchPlan",
    "CultivationBatchPlannerService",
    "CultivationBatchRequest",
    "CultivationMaterialSource",
    "CultivationTargetDraft",
    "CultivationTargetPlan",
]
