# 在整数副本规划中加入三合一材料流，同时保留无 SciPy 时的有界精确搜索。
"""Exact stamina planning with acyclic, zero-stamina 3:1 crafting actions."""

from __future__ import annotations

import heapq
from collections import Counter

from src.domain.progression_material_conversion import allocate_owned
from src.domain.progression_stamina import (
    FarmingRun,
    FarmingStage,
    IdentificationLevelProjection,
    MaterialDeficit,
    MaterialRequirement,
    MaterialYield,
    ProgressionStaminaRequest,
    ProgressionStaminaResult,
    StaminaPlanStatus,
)


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
    from src.domain.progression_material_conversion import material_tier

    seen: set[tuple[str, str]] = set()
    for lower, higher in edges:
        low, high = material_tier(lower), material_tier(higher)
        if low is None or high is None or low[0] != high[0] or high[1] != low[1] + 1:
            raise ValueError("材料转换只能使用正式三档材料的相邻层级")
        if (lower, higher) in seen:
            raise ValueError("材料转换关系不能重复")
        seen.add((lower, higher))
    return edges


_SCIPY_UNAVAILABLE = object()


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
    from src.domain.progression_material_conversion import material_tier

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


__all__ = ["calculate_conversion_stamina"]
