# 定义养成材料缺口与体力规划的纯计算契约。
"""Pure progression-stamina planning without Qt, SQLite, or account state."""

from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum


class StaminaPlanStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class HunterLevelPolicy:
    """Versionable hunter-level to identification-level mapping."""

    thresholds: tuple[int, ...] = (10, 20, 30, 40, 45, 50, 55)
    minimum_hunter_level: int = 1
    maximum_hunter_level: int = 60
    lower_adjustment_unlock_level: int = 3


@dataclass(frozen=True, slots=True)
class IdentificationLevelProjection:
    hunter_level: int
    native_level: int
    effective_level: int
    lowered: bool


@dataclass(frozen=True, slots=True)
class MaterialRequirement:
    item_id: str
    required_quantity: int
    owned_quantity: int = 0


@dataclass(frozen=True, slots=True)
class MaterialYield:
    item_id: str
    quantity: int


@dataclass(frozen=True, slots=True)
class FarmingStage:
    stage_id: str
    label: str
    minimum_hunter_level: int
    minimum_identification_level: int
    stamina_cost: int
    yields: tuple[MaterialYield, ...]
    source: str = "user_supplied"


@dataclass(frozen=True, slots=True)
class ProgressionStaminaRequest:
    hunter_level: int
    requirements: tuple[MaterialRequirement, ...]
    stages: tuple[FarmingStage, ...]
    effective_identification_level: int | None = None


@dataclass(frozen=True, slots=True)
class MaterialDeficit:
    item_id: str
    required_quantity: int
    owned_quantity: int
    deficit_quantity: int


@dataclass(frozen=True, slots=True)
class FarmingRun:
    stage_id: str
    label: str
    runs: int
    stamina_cost_per_run: int
    total_stamina: int
    produced: tuple[MaterialYield, ...]
    source: str


@dataclass(frozen=True, slots=True)
class ProgressionStaminaResult:
    status: StaminaPlanStatus
    identification: IdentificationLevelProjection
    deficits: tuple[MaterialDeficit, ...]
    runs: tuple[FarmingRun, ...]
    known_stamina: int
    total_stamina: int | None
    unresolved_item_ids: tuple[str, ...]
    gaps: tuple[str, ...]


def project_identification_level(
    hunter_level: int,
    *,
    effective_level: int | None = None,
    policy: HunterLevelPolicy = HunterLevelPolicy(),
) -> IdentificationLevelProjection:
    _validate_policy(policy)
    level = int(hunter_level)
    if not policy.minimum_hunter_level <= level <= policy.maximum_hunter_level:
        raise ValueError(
            "猎人等级必须位于 "
            f"{policy.minimum_hunter_level}–{policy.maximum_hunter_level}"
        )
    native = sum(level >= threshold for threshold in policy.thresholds)
    effective = native if effective_level is None else int(effective_level)
    allowed = {native}
    if native >= policy.lower_adjustment_unlock_level:
        allowed.add(native - 1)
    if effective not in allowed:
        raise ValueError(
            f"鉴别等级 {native} 只能保持不变"
            + ("或下调一级" if native >= policy.lower_adjustment_unlock_level else "")
        )
    return IdentificationLevelProjection(
        hunter_level=level,
        native_level=native,
        effective_level=effective,
        lowered=effective != native,
    )


def calculate_progression_stamina(
    request: ProgressionStaminaRequest,
    *,
    policy: HunterLevelPolicy = HunterLevelPolicy(),
    maximum_search_states: int = 300_000,
) -> ProgressionStaminaResult:
    """Return an exact minimum for deterministic accessible drop bundles."""

    if maximum_search_states <= 0:
        raise ValueError("体力规划搜索上限必须为正整数")
    identification = project_identification_level(
        request.hunter_level,
        effective_level=request.effective_identification_level,
        policy=policy,
    )
    requirements = _validate_requirements(request.requirements)
    stages = _validate_stages(request.stages)
    deficits = tuple(
        MaterialDeficit(
            item_id=requirement.item_id,
            required_quantity=requirement.required_quantity,
            owned_quantity=requirement.owned_quantity,
            deficit_quantity=max(
                0, requirement.required_quantity - requirement.owned_quantity
            ),
        )
        for requirement in requirements
    )
    pending = tuple(deficit for deficit in deficits if deficit.deficit_quantity > 0)
    if not pending:
        return ProgressionStaminaResult(
            status=StaminaPlanStatus.COMPLETE,
            identification=identification,
            deficits=deficits,
            runs=(),
            known_stamina=0,
            total_stamina=0,
            unresolved_item_ids=(),
            gaps=(),
        )

    accessible = tuple(
        stage
        for stage in stages
        if stage.minimum_hunter_level <= request.hunter_level
        and stage.minimum_identification_level <= identification.effective_level
    )
    yield_ids = {
        item.item_id
        for stage in accessible
        for item in stage.yields
        if item.quantity > 0
    }
    unresolved = tuple(
        deficit.item_id for deficit in pending if deficit.item_id not in yield_ids
    )
    solvable = tuple(
        deficit for deficit in pending if deficit.item_id not in unresolved
    )
    if not solvable:
        return ProgressionStaminaResult(
            status=StaminaPlanStatus.UNAVAILABLE,
            identification=identification,
            deficits=deficits,
            runs=(),
            known_stamina=0,
            total_stamina=None,
            unresolved_item_ids=unresolved,
            gaps=("material_yield_unavailable",),
        )

    planned = _minimum_stamina_runs(
        solvable,
        accessible,
        maximum_search_states=maximum_search_states,
    )
    if planned is None:
        return ProgressionStaminaResult(
            status=StaminaPlanStatus.UNAVAILABLE,
            identification=identification,
            deficits=deficits,
            runs=(),
            known_stamina=0,
            total_stamina=None,
            unresolved_item_ids=unresolved,
            gaps=("optimization_limit",),
        )
    runs, known_stamina = planned
    if unresolved:
        return ProgressionStaminaResult(
            status=StaminaPlanStatus.PARTIAL,
            identification=identification,
            deficits=deficits,
            runs=runs,
            known_stamina=known_stamina,
            total_stamina=None,
            unresolved_item_ids=unresolved,
            gaps=("material_yield_unavailable",),
        )
    return ProgressionStaminaResult(
        status=StaminaPlanStatus.COMPLETE,
        identification=identification,
        deficits=deficits,
        runs=runs,
        known_stamina=known_stamina,
        total_stamina=known_stamina,
        unresolved_item_ids=(),
        gaps=(),
    )


def _minimum_stamina_runs(
    deficits: tuple[MaterialDeficit, ...],
    stages: tuple[FarmingStage, ...],
    *,
    maximum_search_states: int,
) -> tuple[tuple[FarmingRun, ...], int] | None:
    item_ids = tuple(deficit.item_id for deficit in deficits)
    initial = tuple(deficit.deficit_quantity for deficit in deficits)
    zero = (0,) * len(initial)
    vectors = tuple(
        tuple(_stage_yield(stage, item_id) for item_id in item_ids)
        for stage in stages
    )
    useful = tuple(
        (index, stage, vector)
        for index, (stage, vector) in enumerate(zip(stages, vectors, strict=True))
        if any(vector)
    )
    distance: dict[tuple[int, ...], tuple[int, int]] = {initial: (0, 0)}
    previous: dict[tuple[int, ...], tuple[tuple[int, ...], int]] = {}
    queue: list[tuple[int, int, tuple[int, ...]]] = [(0, 0, initial)]
    visited = 0
    while queue:
        stamina, run_count, state = heapq.heappop(queue)
        if distance.get(state) != (stamina, run_count):
            continue
        if state == zero:
            break
        visited += 1
        if visited > maximum_search_states:
            return None
        for stage_index, stage, vector in useful:
            next_state = tuple(
                max(0, remaining - produced)
                for remaining, produced in zip(state, vector, strict=True)
            )
            if next_state == state:
                continue
            candidate = (stamina + stage.stamina_cost, run_count + 1)
            if candidate >= distance.get(next_state, (2**63 - 1, 2**63 - 1)):
                continue
            distance[next_state] = candidate
            previous[next_state] = (state, stage_index)
            heapq.heappush(queue, (*candidate, next_state))
    if zero not in distance:
        return None

    counts: Counter[int] = Counter()
    state = zero
    while state != initial:
        prior, stage_index = previous[state]
        counts[stage_index] += 1
        state = prior
    runs = tuple(
        FarmingRun(
            stage_id=stages[index].stage_id,
            label=stages[index].label,
            runs=count,
            stamina_cost_per_run=stages[index].stamina_cost,
            total_stamina=count * stages[index].stamina_cost,
            produced=tuple(
                MaterialYield(item.item_id, item.quantity * count)
                for item in stages[index].yields
            ),
            source=stages[index].source,
        )
        for index, count in sorted(counts.items(), key=lambda item: stages[item[0]].stage_id)
    )
    return runs, distance[zero][0]


def _stage_yield(stage: FarmingStage, item_id: str) -> int:
    return next(
        (item.quantity for item in stage.yields if item.item_id == item_id),
        0,
    )


def _validate_policy(policy: HunterLevelPolicy) -> None:
    if not policy.thresholds or tuple(sorted(set(policy.thresholds))) != policy.thresholds:
        raise ValueError("猎人等级阈值必须严格递增且不可重复")
    if policy.minimum_hunter_level < 0:
        raise ValueError("最低猎人等级不能为负数")
    if policy.maximum_hunter_level < policy.thresholds[-1]:
        raise ValueError("最高猎人等级不能低于最后一个鉴别等级阈值")
    if not 0 <= policy.lower_adjustment_unlock_level <= len(policy.thresholds):
        raise ValueError("鉴别等级下调解锁级别超出范围")


def _validate_requirements(
    requirements: tuple[MaterialRequirement, ...],
) -> tuple[MaterialRequirement, ...]:
    seen: set[str] = set()
    for requirement in requirements:
        if not requirement.item_id.strip() or requirement.item_id in seen:
            raise ValueError("材料 ID 不能为空或重复")
        if requirement.required_quantity < 0 or requirement.owned_quantity < 0:
            raise ValueError("材料需求和持有数量不能为负数")
        seen.add(requirement.item_id)
    return requirements


def _validate_stages(stages: tuple[FarmingStage, ...]) -> tuple[FarmingStage, ...]:
    seen: set[str] = set()
    for stage in stages:
        if not stage.stage_id.strip() or stage.stage_id in seen:
            raise ValueError("副本档位 ID 不能为空或重复")
        if stage.minimum_hunter_level < 0 or stage.minimum_identification_level < 0:
            raise ValueError("副本开放等级不能为负数")
        if stage.stamina_cost <= 0:
            raise ValueError("参与体力规划的副本单次体力必须大于 0")
        yield_ids: set[str] = set()
        for item in stage.yields:
            if not item.item_id.strip() or item.item_id in yield_ids:
                raise ValueError("同一副本的材料产出 ID 不能为空或重复")
            if item.quantity <= 0:
                raise ValueError("确定材料产出必须为正整数")
            yield_ids.add(item.item_id)
        seen.add(stage.stage_id)
    return stages
