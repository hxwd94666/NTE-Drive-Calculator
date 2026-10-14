# 在后台构造固定上下文并保存词条配装方案。
"""后台冻结计算输入；恢复重绑owner，保存只消费同一预览。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from src.integrations.bundled_resources import bundled_config_dir
from src.observability import OperationContext, operation_scope
from src.services.allocation.context_builder import ALLOCATION_CONTEXT_SOLVER_VERSION, build_allocation_context, read_allocation_scoring_rules
from src.domain.allocation.inputs import AllocationContext, StaticDatasetReference
from src.domain.allocation.slot_binding import WeightedSlotBinding, WeightedSaveReceipt
from src.services.allocation.slot_binding import assert_slot_bindings_current, assert_binding_comparisons
from src.services.allocation.legacy_adapter import score_allocation_candidate
from src.services.allocation_solver import (
    AllocationAssignment,
    AllocationSolveResult,
    RoleAllocationOption,
    UnifiedAllocation,
    solve_allocation_context,
)
from src.services.allocation.weighted_save import (
    save_weighted_preview_records, freeze_weighted_allocation_locks,
)
from src.services.allocation.locks import AllocationLockSnapshot, verify_allocation_lock_snapshot

if TYPE_CHECKING:
    from .save_owner import WeightedOperation
from src.domain.equipment.virtual_equipment import (
    grid_count_from_geometry,
    virtual_equipment_item_id,
    virtual_equipment_uid,
)
from src.services.equipment.weighted_loadout_comparison_service import (
    WeightedLoadoutComparison,
    freeze_weighted_loadout_comparisons,
    refresh_weighted_loadout_comparisons,
)
from src.domain.static_catalog import StaticCatalogRelease
from src.integrations.static_catalog_release import StaticCatalogReleaseReader
from src.services.allocation.plan_save import require_allocation_release
from src.storage.sqlite.user_data_dao import UserDataDao

from .role_weight_freeze import freeze_official_role_details


@dataclass(frozen=True, slots=True)
class WeightedAllocationRequest:
    """计算派发前固定的用户选择；owner代次不等同于库存generation。"""

    user_database_path: Path
    snapshot_id: int
    profile_id: int
    profile_version: int
    top_k: int
    include_role_top_k: bool = True
    operation_context: OperationContext | None = None
    shared_database_path: Path | None = None
    static_database_path: Path | None = None
    equipment_only: bool = False
    ownership: WeightedOperation | None = None
    scoring_rules_path: Path | None = None
    slot_bindings: tuple[WeightedSlotBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class WeightedAllocationPreview:
    """求解结果及其冻结静态、库存、锁和账号任务身份。"""

    result: AllocationSolveResult
    static_dataset: StaticDatasetReference
    account_id: str
    user_database_path: Path
    context: AllocationContext
    # 角色详情与求解共用同一冻结请求；卡片直接消费不可变Context候选，
    # 不用当前/保存库存上下文补齐计算事实。
    role_details: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
    loadout_comparisons: Mapping[int, tuple[WeightedLoadoutComparison, ...]] = field(default_factory=dict)
    operation_context: OperationContext | None = None
    shared_database_path: Path | None = None
    static_database_path: Path | None = None
    equipment_only: bool = False
    # 完整发行字节身份只在运行时运输，不伪造旧预览许可或改保存payload。
    static_release: StaticCatalogRelease | None = None
    # AppContext代次与库存generation分属两个身份，恢复必须绑定本次新owner。
    ownership: WeightedOperation | None = None
    lock_snapshot: AllocationLockSnapshot | None = None
    slot_bindings: tuple[WeightedSlotBinding, ...] = ()
    explicit_slots: bool = False
    save_receipt: WeightedSaveReceipt | None = None


@dataclass(frozen=True, slots=True)
class WeightedSavedPlanSignature:
    """用于核验恢复方案布局、装备身份和历史评分的持久签名。"""

    plan_id: int
    character_id: int
    score: float
    uids: frozenset[tuple[int, int]]
    assignments: tuple["WeightedSavedAssignmentSignature", ...]


@dataclass(frozen=True, slots=True)
class WeightedSavedAssignmentSignature:
    uid: tuple[int, int]
    kind: str
    target_row: int | None
    target_column: int | None
    score: float | None
    virtual: bool = False
    item_id: str | None = None
    suit_id: str | None = None
    geometry: str | None = None
    grid_count: int | None = None


@dataclass(frozen=True, slots=True)
class WeightedAllocationPersistence:
    """账号SQLite中的最新偏好及可验证恢复请求，不保存旧owner能力。"""

    user_database_path: Path
    profile_id: int | None
    profile_version: int | None
    characters: tuple[dict[str, Any], ...]
    restore_request: WeightedAllocationRequest | None
    saved_plans: tuple[WeightedSavedPlanSignature, ...]
    static_dataset: StaticDatasetReference | None
    slot_bindings: tuple[WeightedSlotBinding, ...] = ()
    selected_slots: tuple[tuple[int, int], ...] | None = None
    selection_identity: tuple[tuple[object, ...], ...] = ()


def read_weighted_allocation_persistence(
    user_database_path: Path, static_database_path: Path | None = None,
    equipment_only: bool = False, *, slot_ids_by_character: Mapping[int, int] | None = None,
    ownership: WeightedOperation | None = None,
) -> WeightedAllocationPersistence:
    from .persistence_reader import read_persistence
    return read_persistence(user_database_path, static_database_path, equipment_only,
                            slot_ids_by_character=slot_ids_by_character, ownership=ownership)


def restore_weighted_allocation_preview(
    persistence: WeightedAllocationPersistence,
    *,
    ownership: WeightedOperation,
) -> WeightedAllocationPreview | None:
    """以当前校验静态约束验证历史保存布局，逐件分数仍取保存事实，绑定新owner。"""

    if not isinstance(persistence, WeightedAllocationPersistence):
        raise TypeError("restoring requires WeightedAllocationPersistence")
    if persistence.restore_request is None:
        return None
    request = replace(persistence.restore_request, ownership=ownership)
    _validate_weighted_request(request)
    if Path(persistence.user_database_path).resolve() != ownership.identity.user_database_path:
        raise RuntimeError("恢复请求与冻结账号数据库不一致")
    operation = request.operation_context or OperationContext.create(
        "allocation", snapshot_id=request.snapshot_id,
    )
    with operation_scope(operation, started_event="allocation.restore_started",
                         succeeded_event="allocation.restore_succeeded",
                         failed_event="allocation.restore_failed", message="恢复保存配装方案"):
        return _run_weighted_allocation(request, operation, persistence=persistence)


def _restore_saved_result(context, persistence, checkpoint):
    """恢复是验证历史事实，不搜索新最优解，也不继承新求解器的约束说明。"""
    if context.static_dataset != persistence.static_dataset:
        raise RuntimeError("已保存方案的数据集已更新，请重新计算。")
    expected = {plan.character_id: plan for plan in persistence.saved_plans}
    bindings = {row.character_id: row for row in persistence.slot_bindings}
    if (not expected or len(expected) != len(persistence.saved_plans)
            or set(expected) != {role.character_id for role in context.roles}
            or len(bindings) != len(persistence.slot_bindings) or set(bindings) != set(expected)
            or (context.profile_id, context.profile_version)
            != (persistence.profile_id, persistence.profile_version)):
        raise RuntimeError("已保存方案与当前固定快照的可用角色不一致。")
    if (persistence.selected_slots is not None
            and dict(persistence.selected_slots) != {cid: row.slot_id for cid, row in bindings.items()}):
        raise RuntimeError("已保存方案与冻结槽位选择不一致。")
    updated_options = []
    claimed = set()
    for role in context.roles:
        checkpoint()
        signature = expected[role.character_id]
        binding = bindings[role.character_id]
        if (binding.plan_id != signature.plan_id or binding.source_snapshot_id != context.snapshot.snapshot_id
                or binding.profile_id != context.profile_id or binding.profile_version != context.profile_version):
            raise RuntimeError("已保存方案的来源、偏好或槽位绑定不一致。")
        option = _restore_saved_option(context, signature)
        real_uids = {assignment.uid for assignment in option.assignments if not assignment.virtual}
        if claimed.intersection(real_uids):
            raise RuntimeError("已保存方案重复使用真实装备 UID。")
        claimed.update(real_uids)
        updated_options.append(option)
    # 手动替换可以偏离选优偏好；只承诺冻结来源、历史分数和合法布局，不宣称重新选优。
    return AllocationSolveResult(
        context.snapshot.snapshot_id, context.profile_id, context.profile_version,
        context.solver_version, 1, (),
        UnifiedAllocation(context.allocation_strategy, sum(option.score for option in updated_options),
                          tuple(updated_options), (), ("已验证保存布局；历史评分未重算",)),
    )


def _restore_saved_option(context, signature: WeightedSavedPlanSignature):
    uids = [saved.uid for saved in signature.assignments]
    if (len(uids) != len(set(uids)) or frozenset(uids) != signature.uids
            or not isfinite(signature.score)
            or sum(saved.kind == "core" for saved in signature.assignments) > 1):
        raise RuntimeError("已保存方案的装备 UID、卡带数量或历史评分无效。")
    candidates = {candidate.uid: candidate for candidate in context.candidates}
    shapes = {
        str(shape.shape_id).removeprefix("EquipmentGeometry_").casefold(): shape
        for shape in context.shapes
    }
    role = next(
        (role for role in context.roles if role.character_id == signature.character_id),
        None,
    )
    if role is None:
        raise RuntimeError(f"角色 {signature.character_id} 缺少当前官方蓝图。")
    official_cells = {
        (int(cell.row), int(cell.column))
        for cell in role.equipment.cells
    }
    restored = []
    occupied_cells: set[tuple[int, int]] = set()
    board_labels: list[tuple[str, tuple[tuple[int, int], ...]]] = []
    for saved in signature.assignments:
        candidate = candidates.get(saved.uid)
        if (
            saved.kind not in {"core", "module"}
            or (candidate is None and not saved.virtual)
            or (candidate is not None and candidate.kind != saved.kind)
            or saved.score is None
            or not isfinite(saved.score)
            or saved.uid[1] <= 0
            or (saved.virtual and (saved.uid[0] != 0 or candidate is not None or saved.score != 0))
            or (not saved.virtual and saved.uid[0] <= 0)
        ):
            raise RuntimeError(
                f"角色 {signature.character_id} 的手动替换方案缺少可验证的装备或评分。"
            )
        geometry = candidate.geometry if candidate is not None else saved.geometry
        if saved.kind == "core":
            board_cells = ()
        else:
            shape = shapes.get(
                str(geometry or "").removeprefix("EquipmentGeometry_").casefold()
            )
            if shape is None or saved.target_row is None or saved.target_column is None:
                raise RuntimeError(f"角色 {signature.character_id} 的手动驱动缺少官方坐标。")
            board_cells = tuple(sorted(
                (saved.target_row + int(cell.x), saved.target_column + int(cell.y))
                for cell in shape.cells
            ))
            if (
                not set(board_cells) <= official_cells
                or occupied_cells.intersection(board_cells)
            ):
                raise RuntimeError(
                    f"角色 {signature.character_id} 的手动替换布局不符合当前官方蓝图。"
                )
            occupied_cells.update(board_cells)
            board_labels.append((
                str(shape.legacy_shape_id or geometry or shape.shape_id),
                board_cells,
            ))
        restored.append(AllocationAssignment(
            uid=saved.uid,
            kind=saved.kind,
            item_id=(
                candidate.item_id
                if candidate is not None
                else saved.item_id
                or virtual_equipment_item_id(saved.kind, geometry)
            ),
            suit_id=candidate.suit_id if candidate is not None else saved.suit_id,
            geometry=geometry,
            board_cells=board_cells,
            official_recommendation_item_id=None,
            score=float(saved.score),
            contributions=(),
            compatibility=("已保存方案", "当前官方蓝图校验"),
            virtual=saved.virtual,
            grid_count=(
                candidate.grid_count if candidate is not None else saved.grid_count
            ),
        ))
    if occupied_cells != official_cells:
        raise RuntimeError(
            f"角色 {signature.character_id} 的手动替换布局未完整覆盖当前官方蓝图。"
        )
    generated_board: list[list[str | int]] = [[-1] * 5 for _ in range(5)]
    for label, board_cells in board_labels:
        for row, column in board_cells:
            generated_board[row - 1][column - 1] = label
    return RoleAllocationOption(
        character_id=signature.character_id,
        rank=1,
        score=signature.score,
        priority_values=(),
        assignments=tuple(restored),
        generated_board=tuple(tuple(row) for row in generated_board),
        satisfied_constraints=("已保存装备身份与历史评分", "当前官方蓝图完整覆盖"),
        missing_core_reason=(None if any(item.kind == "core" for item in restored) else "保存方案未包含卡带"),
    )


def run_weighted_allocation(request: WeightedAllocationRequest) -> WeightedAllocationPreview:
    """只编排不可变Context与唯一求解入口，不读取旧UI状态。

    装备转换、评分与求解仍属于既有领域模块；账号owner只约束任务权限。
    """

    _validate_weighted_request(request)

    operation = request.operation_context or OperationContext.create(
        "allocation",
        snapshot_id=request.snapshot_id,
    )
    with operation_scope(
        operation,
        started_event="allocation.solve_started",
        succeeded_event="allocation.solve_succeeded",
        failed_event="allocation.solve_failed",
        message="执行词条配装计算",
        snapshot_id=request.snapshot_id,
        profile_id=request.profile_id,
        profile_version=request.profile_version,
        top_k=request.top_k,
    ) as span:
        preview = _run_weighted_allocation(request, operation)
        span.annotate(
            role_count=len(preview.context.roles),
            candidate_count=len(preview.context.candidates),
            selected_role_count=len(preview.result.unified.selected),
            unassigned_role_count=len(preview.result.unified.unassigned_character_ids),
            allocation_strategy=preview.result.unified.strategy,
        )
        return preview


def _validate_weighted_request(request):
    """计算与恢复共用请求门禁；每次仍读取 owner 的账号和代次，不缓存授权。"""
    if not isinstance(request, WeightedAllocationRequest):
        raise TypeError("weighted allocation requires a WeightedAllocationRequest")
    if request.ownership is not None:
        request.ownership.checkpoint()
        if Path(request.user_database_path).resolve() != request.ownership.identity.user_database_path:
            raise RuntimeError("计算请求与冻结账号数据库不一致")
    if not request.user_database_path.is_file():
        raise RuntimeError("没有可用的账号背包数据库，请先完成背包同步。")
    if not 1 <= int(request.top_k) <= 20:
        raise ValueError("Top-K 必须在 1 到 20 之间。")


def _run_weighted_allocation(
    request: WeightedAllocationRequest,
    operation: OperationContext,
    *, persistence: WeightedAllocationPersistence | None = None,
) -> WeightedAllocationPreview:
    def cancel_check():
        if request.ownership is not None:
            request.ownership.checkpoint()
        return False
    cancel_check()
    # 加权默认继续选择发行bundled目录，AppContext本机偏好不替换公式来源。
    rules = read_allocation_scoring_rules(request.scoring_rules_path or bundled_config_dir() / "stats.json")
    cancel_check()
    reader = StaticCatalogReleaseReader()
    # 从物化到求解或历史布局恢复结束持有同一拒写 pin；两条路径不复制冻结流程。
    with reader.read_unit(request.static_database_path) as unit:
        static_dao = unit.dao
        release = require_allocation_release(unit.release, request.static_database_path)
        static_database_path = release.database_path
        with UserDataDao(request.user_database_path) as user_dao:
            with user_dao.read_consistent_state():
                context = build_allocation_context(
                    user_dao, static_dao, scoring_rules=rules,
                    snapshot_id=int(request.snapshot_id), profile_id=int(request.profile_id),
                    profile_version=int(request.profile_version),
                    solver_version=ALLOCATION_CONTEXT_SOLVER_VERSION,
                    shared_database_path=request.shared_database_path,
                )
                context, lock_snapshot = freeze_weighted_allocation_locks(user_dao, context)
        cancel_check()
        if request.ownership is not None and context.account_id != request.ownership.identity.account_id:
            raise RuntimeError("计算数据库不属于冻结账号")
        role_details: dict[int, Mapping[str, Any]] = {}
        if not request.equipment_only:
            context, role_details = freeze_official_role_details(
                context, user_database_path=request.user_database_path,
                shared_database_path=request.shared_database_path,
                static_database_path=static_database_path, static_read_unit=unit,
            )
        with UserDataDao(request.user_database_path) as user_dao, user_dao.read_consistent_state():
            assert_slot_bindings_current(user_dao, request.slot_bindings)
            baselines = freeze_weighted_loadout_comparisons(user_dao, static_dao, context, (), checkpoint=cancel_check,
                slot_ids_by_character=({row.character_id: row.slot_id for row in request.slot_bindings}
                                       if request.slot_bindings else None))
            assert_binding_comparisons(request.slot_bindings, baselines)
        cancel_check()
        if persistence is None:
            result = solve_allocation_context(
                context, top_k=int(request.top_k), include_role_top_k=request.include_role_top_k,
                allow_missing_core=True, cancel_check=cancel_check,
            )
        else:
            if request.slot_bindings != persistence.slot_bindings:
                raise RuntimeError("恢复请求与冻结槽位绑定不一致。")
            result = _restore_saved_result(context, persistence, cancel_check)
        loadout_comparisons = refresh_weighted_loadout_comparisons(baselines, context, result.unified.selected)
        cancel_check()
        if persistence is not None:
            with UserDataDao(request.user_database_path) as dao, dao.read_consistent_state():
                assert_slot_bindings_current(dao, persistence.slot_bindings)
                verify_allocation_lock_snapshot(dao, lock_snapshot)
        reader.ensure_unchanged(release)
        cancel_check()
        return WeightedAllocationPreview(
            result=result, static_dataset=context.static_dataset, account_id=context.account_id,
            user_database_path=request.user_database_path, context=context,
            role_details=role_details, loadout_comparisons=loadout_comparisons,
            operation_context=operation, shared_database_path=request.shared_database_path,
            static_database_path=static_database_path, equipment_only=request.equipment_only,
            static_release=release, ownership=request.ownership, lock_snapshot=lock_snapshot,
            slot_bindings=persistence.slot_bindings if persistence is not None else (),
            explicit_slots=persistence is not None and persistence.selected_slots is not None,
        )


def save_weighted_allocation_preview(
    preview: WeightedAllocationPreview,
    operation_context: OperationContext | None = None,
    *,
    ownership: WeightedOperation,
    slot_ids_by_character: Mapping[int, int | None] | None = None,
) -> tuple[int, ...]:
    """Persist the unified result as SQLite plans, without performing equip RPCs.

    The selected Context identifiers are stored in each plan payload so a later
    consumer can distinguish this reproducible recommendation from a live game
    action.  This function never imports or invokes an equipment-apply service.
    """

    if not isinstance(preview, WeightedAllocationPreview):
        raise TypeError("saving requires a WeightedAllocationPreview")
    if ownership.preview is not preview:
        raise RuntimeError("保存任务未绑定本次预览")
    ownership.checkpoint()
    result = preview.result
    if not result.unified.selected:
        raise RuntimeError("没有可保存的统一分配方案。")
    operation = operation_context or OperationContext.create(
        "allocation",
        account_id=preview.account_id,
        snapshot_id=result.snapshot_id,
    )
    with operation_scope(
        operation,
        started_event="allocation.save_started",
        succeeded_event="allocation.save_succeeded",
        failed_event="allocation.save_failed",
        message="保存词条配装方案",
        role_count=len(result.unified.selected),
        snapshot_id=result.snapshot_id,
    ) as span:
        plan_ids = save_weighted_preview_records(
            preview,
            slot_ids_by_character=slot_ids_by_character,
            checkpoint=ownership.checkpoint,
            commit_context=ownership.commit_context,
        )
        span.annotate(saved_plan_count=len(plan_ids))
        return plan_ids


def confirm_weighted_allocation_save(preview: WeightedAllocationPreview, plan_ids: tuple[int, ...],
                                    *, ownership: WeightedOperation,
                                    slot_ids_by_character: Mapping[int, int | None] | None = None
                                    ) -> WeightedAllocationPreview:
    """DAO真实提交后返回新游标；原预览、历史评分与写入口保持不变。"""
    from src.services.allocation.weighted_cursor import read_weighted_save_receipt
    receipt, comparisons = read_weighted_save_receipt(preview, plan_ids, ownership=ownership,
        slot_ids_by_character=slot_ids_by_character)
    return replace(preview, slot_bindings=receipt.slot_bindings, explicit_slots=receipt.explicit_slots,
                   save_receipt=receipt, loadout_comparisons=comparisons)


def replace_weighted_allocation_assignment(
    preview: WeightedAllocationPreview,
    *,
    old_uid: tuple[int, int],
    new_uid: tuple[int, int],
) -> WeightedAllocationPreview:
    """Move one real item and leave a virtual placeholder in its old role."""

    if not isinstance(preview, WeightedAllocationPreview):
        raise TypeError("replacement requires a WeightedAllocationPreview")
    candidate = next(
        (item for item in preview.context.candidates if item.uid == new_uid),
        None,
    )
    if candidate is None:
        raise RuntimeError(f"替换装备 UID {new_uid} 不在计算固定的背包快照中。")
    target_owner = next(
        (
            option
            for option in preview.result.unified.selected
            if any(assignment.uid == old_uid for assignment in option.assignments)
        ),
        None,
    )
    displaced_owner = next(
        (
            option
            for option in preview.result.unified.selected
            if any(assignment.uid == new_uid for assignment in option.assignments)
        ),
        None,
    )
    if target_owner is None:
        raise RuntimeError(f"当前临时方案中不存在待替换 UID {old_uid}。")
    if displaced_owner is target_owner:
        raise RuntimeError("不能用同一角色当前方案中的另一件装备重复占位。")
    target_role = next(
        (role for role in preview.context.roles if role.character_id == target_owner.character_id),
        None,
    )
    if target_role is None:
        raise RuntimeError(f"当前方案中不存在角色 {target_owner.character_id} 的评分配置。")
    replacement_score = score_allocation_candidate(preview.context, target_role, candidate)

    changed_count = 0
    displaced_count = 0
    updated_options: list[RoleAllocationOption] = []
    for option in preview.result.unified.selected:
        updated_assignments = []
        option_changed = False
        for ordinal, assignment in enumerate(option.assignments):
            if assignment.uid == old_uid:
                if candidate.kind != assignment.kind:
                    raise RuntimeError("替换装备类型与当前位置不一致。")
                if (
                    assignment.kind == "module"
                    and str(candidate.geometry or "").casefold()
                    != str(assignment.geometry or "").casefold()
                ):
                    raise RuntimeError("替换驱动形状与当前位置不一致。")
                updated_assignments.append(replace(
                    assignment,
                    uid=new_uid,
                    item_id=candidate.item_id,
                    suit_id=candidate.suit_id,
                    geometry=candidate.geometry,
                    score=replacement_score,
                    contributions=(),
                    virtual=False,
                    grid_count=candidate.grid_count,
                ))
                option_changed = True
                changed_count += 1
                continue
            if assignment.uid == new_uid:
                virtual_uid = virtual_equipment_uid(
                    character_id=option.character_id,
                    displaced_uid=new_uid,
                    ordinal=ordinal,
                    kind=assignment.kind,
                )
                geometry = assignment.geometry
                updated_assignments.append(replace(
                    assignment,
                    uid=virtual_uid,
                    item_id=assignment.item_id,
                    suit_id=assignment.suit_id,
                    score=0.0,
                    contributions=(),
                    compatibility=("跨角色借装占位", "不属于背包候选池"),
                    virtual=True,
                    grid_count=(
                        assignment.grid_count
                        or grid_count_from_geometry(geometry)
                        or None
                    ),
                ))
                option_changed = True
                displaced_count += 1
                continue
            updated_assignments.append(assignment)
        if option_changed:
            # A role score is the sum of every concrete slot, not merely the
            # previous total plus a delta.  Rebuilding it also protects an
            # older or overlay plan whose stored total was absent/stale.
            updated_options.append(replace(
                option,
                score=sum(float(item.score) for item in updated_assignments),
                assignments=tuple(updated_assignments),
            ))
        else:
            updated_options.append(option)
    if changed_count != 1:
        raise RuntimeError(
            f"当前方案中应恰好存在一个待替换 UID {old_uid}，实际为 {changed_count} 个。"
        )
    if displaced_owner is not None and displaced_count != 1:
        raise RuntimeError(
            f"当前临时方案中的被借装备 UID {new_uid} 无法唯一定位。"
        )
    unified = replace(
        preview.result.unified,
        total_score=sum(float(option.score) for option in updated_options),
        selected=tuple(updated_options),
        explanation=tuple(preview.result.unified.explanation) + (
            "用户手动优化替换；跨角色借装时原槽位使用虚拟占位",
        ),
    )
    return replace(
        preview,
        result=replace(preview.result, unified=unified),
        loadout_comparisons=refresh_weighted_loadout_comparisons(
            preview.loadout_comparisons, preview.context, updated_options,
        ),
    )
