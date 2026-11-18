# 基于不可变上下文复用既有推荐算法，生成 Top-K 与 UID 唯一分配。
"""Public Top-K facade for the existing allocation recommendation pipeline."""

from __future__ import annotations

from concurrent.futures import CancelledError
from collections.abc import Callable
from dataclasses import dataclass, replace

from src.domain.equipment.geometry.drive_layout import matrix_groups_in_scan_order
from src.domain.allocation.inputs import AllocationCandidate, AllocationContext, AllocationRolePreference


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
    virtual: bool = False
    grid_count: int | None = None


@dataclass(frozen=True, slots=True)
class RoleAllocationOption:
    character_id: int
    rank: int
    score: float
    priority_values: tuple[float, ...]
    assignments: tuple[AllocationAssignment, ...]
    generated_board: tuple[tuple[str | int, ...], ...]
    satisfied_constraints: tuple[str, ...]
    missing_core_reason: str | None = None

    @property
    def used_uids(self) -> frozenset[tuple[int, int]]:
        return frozenset(assignment.uid for assignment in self.assignments)


@dataclass(frozen=True, slots=True)
class RoleTopK:
    character_id: int
    options: tuple[RoleAllocationOption, ...]
    rejection_reason: str | None
    search_limited: bool = False


@dataclass(frozen=True, slots=True)
class UnifiedAllocation:
    strategy: str
    total_score: float
    selected: tuple[RoleAllocationOption, ...]
    unassigned_character_ids: tuple[int, ...]
    explanation: tuple[str, ...]
    search_limited: bool = False


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


def _option(role: AllocationRolePreference, run, *, rank: int, native: bool = False) -> RoleAllocationOption | None:
    plan = run.plans.get(run.role_key(role.character_id)) or {}
    if not plan.get("valid"):
        return None
    board = tuple(tuple(row) for row in (plan.get("blueprint") or {}).get("board", ()))
    groups_by_shape: dict[str, list[tuple[tuple[int, int], ...]]] = {}
    for shape_id, cells in matrix_groups_in_scan_order(
        [[str(cell) for cell in row] for row in board]
    ):
        groups_by_shape.setdefault(
            str(shape_id).strip().casefold(), []
        ).append(tuple(cells))
    assignments: list[AllocationAssignment] = []
    blueprint_source = "Rust 图纸" if native else "PuzzleCombinatorics + DFSPuzzleSolver"
    scoring_source = "Rust 基础评分" if native else "ScoringEngine"
    role_key = run.role_key(role.character_id)
    tape = plan.get("assigned_tape")
    if tape is not None:
        item = run.candidates_by_legacy_uid[tape.uid]
        assignments.append(AllocationAssignment(item.uid, "core", item.item_id, item.suit_id, None, (), None,
            float(tape.role_scores.get(role_key, 0.0)), _contributions(item, role),
            (f"{scoring_source} 标准化核心评分", "用户核心主词条过滤"),
            grid_count=item.grid_count))
    for drives, slot_label in ((plan.get("assigned_set_drives", ()) or (), "套装必要形状"),
                               (plan.get("assigned_extra_drives", ()) or (), "额外形状")):
        for drive in drives:
            item = run.candidates_by_legacy_uid[drive.uid]
            shape_groups = groups_by_shape.get(
                str(drive.shape_id).strip().casefold(), []
            )
            if not shape_groups:
                raise AllocationSolverError(
                    f"角色 {role.character_id} 的图纸无法为驱动形状 {drive.shape_id} 分配独立格块"
                )  # ui-text: internal
            assignments.append(AllocationAssignment(item.uid, "module", item.item_id, item.suit_id, item.geometry,
                shape_groups.pop(0), None,
                float(drive.role_scores.get(role_key, 0.0)), _contributions(item, role),
                (f"{scoring_source} 标准化驱动评分", blueprint_source, slot_label),
                grid_count=item.grid_count))
    if any(groups for groups in groups_by_shape.values()):
        raise AllocationSolverError(f"角色 {role.character_id} 的图纸存在未绑定驱动格块")  # ui-text: internal
    if not assignments:
        return None
    return RoleAllocationOption(
        role.character_id,
        rank,
        float(plan.get("score", 0.0)),
        (),
        tuple(assignments),
        board,
        (
            "官方 20 格底盘",
            "官方形状坐标",
            blueprint_source,
            scoring_source,
            "官方配装预设未参与选优",
        ),
        str(plan.get("missing_core_reason") or "") or None,
    )


def _checkpoint(cancel_check):
    if cancel_check is not None and cancel_check():
        raise CancelledError("配装计算已取消")


def _top_k(
    context: AllocationContext, role: AllocationRolePreference, count: int, *, allow_missing_core: bool = False, cancel_check=None,
) -> RoleTopK:
    """Branch by UID exclusion and rerun the original one-role strategy."""

    from heapq import heappop, heappush
    from itertools import count as counter
    from src.services.allocation.execution.legacy_adapter import run_legacy_allocation

    serial = counter()
    queue: list[tuple[float, int, frozenset[tuple[int, int]], RoleAllocationOption]] = []
    seen_exclusions: set[frozenset[tuple[int, int]]] = set()

    def enqueue(excluded: frozenset[tuple[int, int]]) -> None:
        _checkpoint(cancel_check)
        if excluded in seen_exclusions:
            return
        seen_exclusions.add(excluded)
        option = _option(
            role,
            run_legacy_allocation(
                context, roles=(role,), excluded_uids=excluded, allow_missing_core=allow_missing_core,
                cancel_check=cancel_check,
            ),
            rank=0,
        )
        _checkpoint(cancel_check)
        if option is not None:
            heappush(queue, (-option.score, next(serial), excluded, option))

    enqueue(frozenset())
    options: list[RoleAllocationOption] = []
    fingerprints: set[frozenset[tuple[int, int]]] = set()
    while queue and len(options) < count:
        _checkpoint(cancel_check)
        _score, _serial, excluded, option = heappop(queue)
        if option.used_uids in fingerprints:
            continue
        fingerprints.add(option.used_uids)
        options.append(RoleAllocationOption(option.character_id, len(options) + 1, option.score,
            option.priority_values, option.assignments, option.generated_board,
            option.satisfied_constraints, option.missing_core_reason))
        for uid in option.used_uids:
            _checkpoint(cancel_check)
            enqueue(excluded | {uid})
    _checkpoint(cancel_check)
    return RoleTopK(role.character_id, tuple(options), None if options else "既有求解器未能生成满足约束的完整候选")


def _validate_context(context: AllocationContext, top_k: int) -> None:
    if not isinstance(context, AllocationContext):
        raise TypeError("求解器只接受不可变 AllocationContext")  # ui-text: internal
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("top_k 必须是 1 到 20 的整数")  # ui-text: internal
    if not context.shapes or not context.suits or not context.attributes:
        raise AllocationSolverError("AllocationContext 缺少求解器所需的形状、套装或属性映射")  # ui-text: internal


def solve_native_allocation_context(
    context: AllocationContext, *, top_k: int = 5, include_role_top_k: bool = True,
    allow_missing_core: bool = False, cancel_check: Callable[[], bool] | None = None,
) -> AllocationSolveResult:
    """Calculate the weighted preview from its frozen context in the analysis component."""
    from src.integrations.allocation.native_blueprints import create_blueprint_executor
    from src.integrations.allocation.native_weighted_allocation import CAPABILITY, execute_weighted_allocation
    from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
    from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisCancelled, NativeAnalysisError
    from src.services.allocation.execution.legacy_adapter import prepare_allocation

    _checkpoint(cancel_check)
    _validate_context(context, top_k)
    try:
        client = create_bundled_analysis_client(
            static_database_path=context.static_database_path, cancelled=cancel_check,
        )
        if client is None or CAPABILITY not in client.version().get("capabilities", ()):
            raise NativeAnalysisError("分析组件缺少加权配装能力，请更新配套组件")
        if client.dataset_version != context.static_dataset.dataset_id:
            raise NativeAnalysisError("加权配装使用的静态数据集已变更")
        blueprints = create_blueprint_executor(client, cancel_check=cancel_check)
    except NativeAnalysisCancelled:
        raise CancelledError("配装计算已取消") from None
    request, scorer, run = prepare_allocation(
        context, allow_missing_core=allow_missing_core, cancel_check=cancel_check,
        blueprint_executor=blueprints,
    )
    plans, top, limited = execute_weighted_allocation(
        client, request, scorer, top_k=top_k if include_role_top_k else 0, cancel_check=cancel_check,
    )
    run = replace(run, plans=plans)
    selected = []
    role_top_k = []
    for role in context.roles:
        _checkpoint(cancel_check)
        option = _option(role, run, rank=1, native=True)
        if option is not None:
            selected.append(option)
        if include_role_top_k:
            name = run.role_key(role.character_id)
            batch = top[name]
            options = []
            for rank, plan in enumerate(batch["plans"], 1):
                option = _option(role, replace(run, plans={name: plan}), rank=rank, native=True)
                if option is None:
                    raise AllocationSolverError("配装候选缺少有效装备")
                options.append(option)
            role_top_k.append(RoleTopK(
                role.character_id, tuple(options),
                None if options else (
                    "配装搜索达到预算" if batch["limited"] else batch["reason"]
                    or "未生成满足约束的完整候选"
                ),
                batch["limited"],
            ))

    selected_ids = {option.character_id for option in selected}
    _checkpoint(cancel_check)
    return AllocationSolveResult(
        context.snapshot.snapshot_id, context.profile_id, context.profile_version, context.solver_version,
        top_k, tuple(role_top_k),
        UnifiedAllocation(
            context.allocation_strategy, sum(option.score for option in selected), tuple(selected),
            tuple(role.character_id for role in context.roles if role.character_id not in selected_ids),
            ("Rust 从同一不可变库存和角色配置求解", "统一分配的原生装备 UID 不重复"),
            limited,
        ),
    )


def solve_allocation_context(context: AllocationContext, *, top_k: int = 5, include_role_top_k: bool = True,
                             role_search_limit: int = 20_000, global_search_limit: int = 100_000,
                             allow_missing_core: bool = False,
                             cancel_check: Callable[[], bool] | None = None) -> AllocationSolveResult:
    """Run the Python reference implementation for comparison and offline callers."""

    _checkpoint(cancel_check)
    del role_search_limit, global_search_limit
    _validate_context(context, top_k)
    from src.services.allocation.execution.legacy_adapter import run_legacy_allocation

    role_top_k = (
        tuple(_top_k(context, role, top_k, allow_missing_core=allow_missing_core, cancel_check=cancel_check) for role in context.roles)
        if include_role_top_k else ()
    )
    run = run_legacy_allocation(context, allow_missing_core=allow_missing_core, cancel_check=cancel_check)
    selected = tuple(option for role in context.roles if (option := _option(role, run, rank=1)) is not None)
    uids = [uid for option in selected for uid in option.used_uids]
    if len(uids) != len(set(uids)):
        raise AllocationSolverError("既有分配策略返回了重复的原生装备 UID")
    selected_ids = {option.character_id for option in selected}
    _checkpoint(cancel_check)
    return AllocationSolveResult(context.snapshot.snapshot_id, context.profile_id, context.profile_version,
        context.solver_version, top_k, role_top_k,
        UnifiedAllocation(context.allocation_strategy, sum(option.score for option in selected), selected,
            tuple(role.character_id for role in context.roles if role.character_id not in selected_ids),
            ("所有输入均来自同一不可变 AllocationContext",
             "评分、图纸和三种策略直接复用既有 ScoringEngine / 求解器 / DispatcherEngine",
             "官方配装预设未参与选优；跨角色原生 UID 不重复")))
