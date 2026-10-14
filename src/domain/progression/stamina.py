# 定义养成材料缺口与体力规划的纯计算契约。
"""统一普通掉落与三合一材料流的体力规划，不读取 Qt、数据库或账号状态。"""

from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from src.domain.progression.materials import allocate_owned, material_tier


class StaminaPlanStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class HunterLevelPolicy:
    """猎人等级与鉴别等级的版本化映射，冻结请求后不追随界面变化。"""

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
    conversions: tuple[tuple[str, str], ...] = ()


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
    """只规划已证实且已开放的确定产出；无产出或超出搜索预算保留未知总体力。"""

    if maximum_search_states <= 0:
        raise ValueError("体力规划搜索上限必须为正整数")
    identification = project_identification_level(
        request.hunter_level,
        effective_level=request.effective_identification_level,
        policy=policy,
    )
    requirements = _validate_requirements(request.requirements)
    stages = _validate_stages(request.stages)
    if request.conversions:
        return calculate_conversion_stamina(
            request, identification, requirements, stages,
            maximum_search_states=maximum_search_states,
        )
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
    optimized = _minimum_stamina_runs_milp(deficits, stages)
    if optimized is not _SCIPY_UNAVAILABLE:
        return optimized
    return _minimum_stamina_runs_search(
        deficits,
        stages,
        maximum_search_states=maximum_search_states,
    )


_SCIPY_UNAVAILABLE = object()


def _minimum_stamina_runs_milp(
    deficits: tuple[MaterialDeficit, ...],
    stages: tuple[FarmingStage, ...],
) -> tuple[tuple[FarmingRun, ...], int] | None | object:
    """整数规划先最小体力再最少次数；仅 SciPy 缺失才走有界搜索，失败不伪装完整。"""

    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ImportError:
        return _SCIPY_UNAVAILABLE
    item_ids = tuple(deficit.item_id for deficit in deficits)
    useful = tuple(
        stage
        for stage in stages
        if any(_stage_yield(stage, item_id) for item_id in item_ids)
    )
    if not useful:
        return None
    yields = np.asarray(
        [
            [_stage_yield(stage, item_id) for stage in useful]
            for item_id in item_ids
        ],
        dtype=float,
    )
    required = np.asarray(
        [deficit.deficit_quantity for deficit in deficits],
        dtype=float,
    )
    stamina = np.asarray([stage.stamina_cost for stage in useful], dtype=float)
    integrality = np.ones(len(useful), dtype=int)
    bounds = Bounds(np.zeros(len(useful)), np.full(len(useful), np.inf))
    production = LinearConstraint(yields, required, np.full(len(required), np.inf))
    first = milp(
        stamina,
        integrality=integrality,
        bounds=bounds,
        constraints=production,
        options={"time_limit": 2.0},
    )
    if not first.success or first.x is None or first.fun is None:
        return None
    minimum_stamina = int(round(float(first.fun)))
    stamina_lock = LinearConstraint(stamina, minimum_stamina, minimum_stamina)
    second = milp(
        np.ones(len(useful), dtype=float),
        integrality=integrality,
        bounds=bounds,
        constraints=(production, stamina_lock),
        options={"time_limit": 2.0},
    )
    solution = second.x if second.success and second.x is not None else first.x
    counts = np.rint(solution).astype(int)
    if np.any(counts < 0) or np.any(yields @ counts < required):
        return None
    runs = tuple(
        FarmingRun(
            stage_id=stage.stage_id,
            label=stage.label,
            runs=int(count),
            stamina_cost_per_run=stage.stamina_cost,
            total_stamina=int(count) * stage.stamina_cost,
            produced=tuple(
                MaterialYield(item.item_id, item.quantity * int(count))
                for item in stage.yields
            ),
            source=stage.source,
        )
        for stage, count in zip(useful, counts, strict=True)
        if count > 0
    )
    return runs, minimum_stamina


def _minimum_stamina_runs_search(
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


# 三合一材料流与普通副本共用结果契约；模型差异在本模块内处理，避免相互延迟导入。
def calculate_conversion_stamina(
    request: ProgressionStaminaRequest,
    identification: IdentificationLevelProjection,
    requirements: tuple[MaterialRequirement, ...],
    stages: tuple[FarmingStage, ...],
    *,
    maximum_search_states: int,
) -> ProgressionStaminaResult:
    edges = _validate_edges(request.conversions)
    required = {item.item_id: item.required_quantity for item in requirements if item.required_quantity}
    owned = {item.item_id: item.owned_quantity for item in requirements if item.owned_quantity}
    credited = allocate_owned(required, dict(owned))
    deficits = tuple(
        MaterialDeficit(item_id, quantity, credited.get(item_id, 0),
                        quantity - credited.get(item_id, 0))
        for item_id, quantity in required.items()
    )
    if all(item.deficit_quantity == 0 for item in deficits):
        return ProgressionStaminaResult(
            StaminaPlanStatus.COMPLETE, identification, deficits, (), 0, 0, (), (),
        )
    accessible = tuple(
        stage for stage in stages
        if stage.minimum_hunter_level <= request.hunter_level
        and stage.minimum_identification_level <= identification.effective_level
    )
    ids = tuple(dict.fromkeys((
        *required, *owned,
        *(item_id for edge in edges for item_id in edge),
    )))
    relevant = tuple(
        stage for stage in accessible
        if any(item.item_id in ids for item in stage.yields)
    )
    reachable = {
        item.item_id for stage in relevant for item in stage.yields
    }
    for lower, higher in edges:
        if lower in reachable:
            reachable.add(higher)
    unresolved = tuple(
        item.item_id for item in deficits
        if item.deficit_quantity and item.item_id not in reachable
    )
    solvable = {item_id: quantity for item_id, quantity in required.items()
                if item_id not in unresolved}
    if not solvable:
        return ProgressionStaminaResult(
            StaminaPlanStatus.UNAVAILABLE, identification, deficits, (), 0, None,
            unresolved, ("material_yield_unavailable",),
        )
    solution = _solve_milp(ids, solvable, owned, relevant, edges)
    if solution is _SCIPY_UNAVAILABLE:
        solution = _solve_search(
            ids, solvable, owned, relevant,
            maximum_search_states=maximum_search_states,
        )
    if solution is None:
        return ProgressionStaminaResult(
            StaminaPlanStatus.UNAVAILABLE, identification, deficits, (), 0, None,
            unresolved or tuple(solvable), ("optimization_limit",),
        )
    counts, stamina = solution
    produced = dict(owned)
    for stage, count in zip(relevant, counts, strict=True):
        for item in stage.yields:
            produced[item.item_id] = produced.get(item.item_id, 0) + item.quantity * count
    final_credit = allocate_owned(solvable, produced)
    if any(final_credit.get(item_id, 0) < quantity for item_id, quantity in solvable.items()):
        return ProgressionStaminaResult(
            StaminaPlanStatus.UNAVAILABLE, identification, deficits, (), 0, None,
            tuple(solvable), ("optimization_limit",),
        )
    runs = tuple(
        FarmingRun(
            stage.stage_id, stage.label, count, stage.stamina_cost,
            count * stage.stamina_cost,
            tuple(MaterialYield(item.item_id, item.quantity * count) for item in stage.yields),
            stage.source,
        )
        for stage, count in zip(relevant, counts, strict=True) if count
    )
    return ProgressionStaminaResult(
        StaminaPlanStatus.PARTIAL if unresolved else StaminaPlanStatus.COMPLETE,
        identification, deficits, runs, stamina,
        None if unresolved else stamina,
        unresolved,
        ("material_yield_unavailable",) if unresolved else (),
    )


def _validate_edges(edges: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    seen: set[tuple[str, str]] = set()
    for lower, higher in edges:
        low, high = material_tier(lower), material_tier(higher)
        if low is None or high is None or low[0] != high[0] or high[1] != low[1] + 1:
            raise ValueError("材料转换只能使用正式三档材料的相邻层级")
        if (lower, higher) in seen:
            raise ValueError("材料转换关系不能重复")
        seen.add((lower, higher))
    return edges


def _solve_milp(
    ids: tuple[str, ...], required: dict[str, int], owned: dict[str, int],
    stages: tuple[FarmingStage, ...], edges: tuple[tuple[str, str], ...],
) -> tuple[tuple[int, ...], int] | None | object:
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ImportError:
        return _SCIPY_UNAVAILABLE
    count = len(stages) + len(edges)
    if count == 0:
        return None
    matrix = np.zeros((len(ids), count), dtype=float)
    index = {item_id: row for row, item_id in enumerate(ids)}
    for column, stage in enumerate(stages):
        for item in stage.yields:
            if item.item_id in index:
                matrix[index[item.item_id], column] += item.quantity
    for offset, (lower, higher) in enumerate(edges, start=len(stages)):
        matrix[index[lower], offset] -= 3
        matrix[index[higher], offset] += 1
    target = np.asarray(
        [required.get(item_id, 0) - owned.get(item_id, 0) for item_id in ids],
        dtype=float,
    )
    cost = np.asarray(
        [stage.stamina_cost for stage in stages] + [0] * len(edges),
        dtype=float,
    )
    bounds = Bounds(np.zeros(count), np.full(count, np.inf))
    production = LinearConstraint(matrix, target, np.full(len(ids), np.inf))
    first = milp(
        cost, integrality=np.ones(count), bounds=bounds,
        constraints=production, options={"time_limit": 2.0},
    )
    if not first.success or first.x is None:
        return None
    minimum = int(round(float(first.fun)))
    secondary = np.asarray([1] * len(stages) + [0] * len(edges), dtype=float)
    second = milp(
        secondary, integrality=np.ones(count), bounds=bounds,
        constraints=(production, LinearConstraint(cost, minimum, minimum)),
        options={"time_limit": 2.0},
    )
    values = second.x if second.success and second.x is not None else first.x
    integers = np.rint(values).astype(int)
    if np.any(integers < 0) or np.any(matrix @ integers < target - 1e-7):
        return None
    return tuple(int(value) for value in integers[:len(stages)]), minimum


def _solve_search(
    ids: tuple[str, ...], required: dict[str, int], owned: dict[str, int],
    stages: tuple[FarmingStage, ...], *, maximum_search_states: int,
) -> tuple[tuple[int, ...], int] | None:
    caps: dict[str, int] = {}
    for item_id in ids:
        tier = material_tier(item_id)
        if tier is None:
            caps[item_id] = required.get(item_id, 0)
        else:
            family, level = tier
            caps[item_id] = sum(
                required.get(f"{family}_lv{upper}", 0) * (3 ** (upper - level))
                for upper in range(level, 4)
            )
    cap = tuple(max(owned.get(item_id, 0), caps[item_id]) for item_id in ids)
    initial = tuple(min(owned.get(item_id, 0), maximum) for item_id, maximum in zip(ids, cap))
    vectors = tuple(
        tuple(sum(item.quantity for item in stage.yields if item.item_id == item_id)
              for item_id in ids)
        for stage in stages
    )
    distance: dict[tuple[int, ...], tuple[int, int]] = {initial: (0, 0)}
    previous: dict[tuple[int, ...], tuple[tuple[int, ...], int]] = {}
    queue: list[tuple[int, int, tuple[int, ...]]] = [(0, 0, initial)]
    visited = 0
    while queue:
        stamina, run_count, state = heapq.heappop(queue)
        if distance.get(state) != (stamina, run_count):
            continue
        available = dict(zip(ids, state, strict=True))
        credited = allocate_owned(required, available)
        if all(credited.get(item_id, 0) >= quantity for item_id, quantity in required.items()):
            counts: Counter[int] = Counter()
            cursor = state
            while cursor != initial:
                prior, stage_index = previous[cursor]
                counts[stage_index] += 1
                cursor = prior
            return tuple(counts[index] for index in range(len(stages))), stamina
        visited += 1
        if visited > maximum_search_states:
            return None
        for index, (stage, vector) in enumerate(zip(stages, vectors, strict=True)):
            next_state = tuple(
                min(maximum, current + yield_count)
                for current, yield_count, maximum in zip(state, vector, cap, strict=True)
            )
            if next_state == state:
                continue
            candidate = (stamina + stage.stamina_cost, run_count + 1)
            if candidate >= distance.get(next_state, (2**63, 2**63)):
                continue
            distance[next_state] = candidate
            previous[next_state] = (state, index)
            heapq.heappush(queue, (*candidate, next_state))
    return None
