# 基于不可变上下文复用既有推荐算法，生成 Top-K 与 UID 唯一分配。
"""Public Top-K facade for the existing allocation recommendation pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from src.services.allocation_context import AllocationCandidate, AllocationContext, AllocationRolePreference


class AllocationSolverError(RuntimeError):
    """The frozen context cannot produce a safe, complete allocation."""


@dataclass(frozen=True, slots=True)
class StatContribution:
    property_id: str
    source: str
    raw_value: float
    normalized_value: float
    weight: float
    score: float


@dataclass(frozen=True, slots=True)
class AllocationAssignment:
    uid: tuple[int, int]
    kind: str
    item_id: str
    suit_id: str | None
    geometry: str | None
    board_cells: tuple[tuple[int, int], ...]
    official_recommendation_item_id: str | None
    score: float
    contributions: tuple[StatContribution, ...]
    compatibility: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoleAllocationOption:
    character_id: int
    rank: int
    score: float
    priority_values: tuple[float, ...]
    assignments: tuple[AllocationAssignment, ...]
    generated_board: tuple[tuple[str | int, ...], ...]
    satisfied_constraints: tuple[str, ...]

    @property
    def used_uids(self) -> frozenset[tuple[int, int]]:
        return frozenset(assignment.uid for assignment in self.assignments)


@dataclass(frozen=True, slots=True)
class RoleTopK:
    character_id: int
    options: tuple[RoleAllocationOption, ...]
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class UnifiedAllocation:
    strategy: str
    total_score: float
    selected: tuple[RoleAllocationOption, ...]
    unassigned_character_ids: tuple[int, ...]
    explanation: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AllocationSolveResult:
    snapshot_id: int
    profile_id: int
    profile_version: int
    solver_version: str
    top_k: int
    role_top_k: tuple[RoleTopK, ...]
    unified: UnifiedAllocation


def _contributions(candidate: AllocationCandidate, role: AllocationRolePreference) -> tuple[StatContribution, ...]:
    """Explain matching stat kinds without replacing ScoringEngine's total."""

    weights = dict(role.effective_property_weights)
    return tuple(
        StatContribution(stat.property_id, source, float(stat.value), 1.0, float(weights[stat.property_id]), 0.0)
        for source, stats in (("main", candidate.main_stats), ("sub", candidate.sub_stats))
        for stat in stats if stat.property_id in weights
    )


def _option(role: AllocationRolePreference, run, *, rank: int) -> RoleAllocationOption | None:
    plan = run.plans.get(run.role_key(role.character_id)) or {}
    if not plan.get("valid"):
        return None
    board = tuple(tuple(row) for row in (plan.get("blueprint") or {}).get("board", ()))
    cells_by_shape: dict[str, list[tuple[int, int]]] = {}
    for row, values in enumerate(board, start=1):
        for column, value in enumerate(values, start=1):
            if isinstance(value, str):
                cells_by_shape.setdefault(value, []).append((row, column))
    assignments: list[AllocationAssignment] = []
    role_key = run.role_key(role.character_id)
    tape = plan.get("assigned_tape")
    if tape is not None:
        item = run.candidates_by_legacy_uid[tape.uid]
        assignments.append(AllocationAssignment(item.uid, "core", item.item_id, item.suit_id, None, (), None,
            float(tape.role_scores.get(role_key, 0.0)), _contributions(item, role),
            ("ScoringEngine 标准化核心评分", "用户核心主词条过滤")))
    for drives, slot_label in ((plan.get("assigned_set_drives", ()) or (), "套装必要形状"),
                               (plan.get("assigned_extra_drives", ()) or (), "额外形状")):
        for drive in drives:
            item = run.candidates_by_legacy_uid[drive.uid]
            assignments.append(AllocationAssignment(item.uid, "module", item.item_id, item.suit_id, item.geometry,
                tuple(cells_by_shape.get(str(drive.shape_id), ())), None,
                float(drive.role_scores.get(role_key, 0.0)), _contributions(item, role),
                ("ScoringEngine 标准化驱动评分", "PuzzleCombinatorics + DFSPuzzleSolver", slot_label)))
    if not assignments:
        return None
    return RoleAllocationOption(role.character_id, rank, float(plan.get("score", 0.0)), (), tuple(assignments), board,
        ("官方 20 格底盘", "官方形状坐标", "PuzzleCombinatorics + DFSPuzzleSolver", "ScoringEngine", "官方配装预设未参与选优"))


def _top_k(
    context: AllocationContext, role: AllocationRolePreference, count: int, *, allow_missing_core: bool = False,
) -> RoleTopK:
    """Branch by UID exclusion and rerun the original one-role strategy."""

    from heapq import heappop, heappush
    from itertools import count as counter
    from src.services.allocation_legacy_adapter import run_legacy_allocation

    serial = counter()
    queue: list[tuple[float, int, frozenset[tuple[int, int]], RoleAllocationOption]] = []
    seen_exclusions: set[frozenset[tuple[int, int]]] = set()

    def enqueue(excluded: frozenset[tuple[int, int]]) -> None:
        if excluded in seen_exclusions:
            return
        seen_exclusions.add(excluded)
        option = _option(
            role,
            run_legacy_allocation(
                context, roles=(role,), excluded_uids=excluded, allow_missing_core=allow_missing_core,
            ),
            rank=0,
        )
        if option is not None:
            heappush(queue, (-option.score, next(serial), excluded, option))

    enqueue(frozenset())
    options: list[RoleAllocationOption] = []
    fingerprints: set[frozenset[tuple[int, int]]] = set()
    while queue and len(options) < count:
        _score, _serial, excluded, option = heappop(queue)
        if option.used_uids in fingerprints:
            continue
        fingerprints.add(option.used_uids)
        options.append(RoleAllocationOption(option.character_id, len(options) + 1, option.score,
            option.priority_values, option.assignments, option.generated_board, option.satisfied_constraints))
        for uid in option.used_uids:
            enqueue(excluded | {uid})
    return RoleTopK(role.character_id, tuple(options), None if options else "既有求解器未能生成满足约束的完整候选")


def solve_allocation_context(context: AllocationContext, *, top_k: int = 5, include_role_top_k: bool = True,
                             role_search_limit: int = 20_000, global_search_limit: int = 100_000,
                             allow_missing_core: bool = False) -> AllocationSolveResult:
    """Run the established scorer, puzzle solver and strategy dispatcher via Context."""

    del role_search_limit, global_search_limit
    if not isinstance(context, AllocationContext):
        raise TypeError("求解器只接受不可变 AllocationContext")
    if not isinstance(top_k, int) or not 1 <= top_k <= 20:
        raise ValueError("top_k 必须是 1 到 20 的整数")
    if not context.shapes or not context.suits or not context.attributes:
        raise AllocationSolverError("AllocationContext 缺少既有求解器所需的形状、套装或属性映射")
    from src.services.allocation_legacy_adapter import run_legacy_allocation

    role_top_k = (
        tuple(_top_k(context, role, top_k, allow_missing_core=allow_missing_core) for role in context.roles)
        if include_role_top_k else ()
    )
    run = run_legacy_allocation(context, allow_missing_core=allow_missing_core)
    selected = tuple(option for role in context.roles if (option := _option(role, run, rank=1)) is not None)
    uids = [uid for option in selected for uid in option.used_uids]
    if len(uids) != len(set(uids)):
        raise AllocationSolverError("既有分配策略返回了重复的原生装备 UID")
    selected_ids = {option.character_id for option in selected}
    return AllocationSolveResult(context.snapshot.snapshot_id, context.profile_id, context.profile_version,
        context.solver_version, top_k, role_top_k,
        UnifiedAllocation(context.allocation_strategy, sum(option.score for option in selected), selected,
            tuple(role.character_id for role in context.roles if role.character_id not in selected_ids),
            ("所有输入均来自同一不可变 AllocationContext",
             "评分、图纸和三种策略直接复用既有 ScoringEngine / 求解器 / DispatcherEngine",
             "官方配装预设未参与选优；跨角色原生 UID 不重复")))
