# 在后台构造固定上下文并保存词条配装方案。
"""Pinned, background-safe entry point for the weighted allocation page."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from src.optimizer.contracts import (
    EQUIP_SHAPE_ID,
    EQUIP_UID,
    ROLE_BLUEPRINT_LAYOUT,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
)
from src.services.allocation_context import (
    ALLOCATION_CONTEXT_SOLVER_VERSION,
    AllocationContext,
    StaticDatasetReference,
    build_allocation_context,
)
from src.services.allocation_solver import (
    AllocationAssignment,
    AllocationSolveResult,
    RoleAllocationOption,
    solve_allocation_context,
)
from src.services.saved_state_loadout_bridge import SavedStateLoadoutBridge
from src.services.official_role_page_service import load_official_role_detail
from src.services.sqlite_allocation_inventory import legacy_shape_id
from src.services.virtual_equipment_service import (
    grid_count_from_geometry,
    virtual_equipment_item_id,
    virtual_equipment_uid,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True, slots=True)
class WeightedAllocationRequest:
    """The exact user choices made before a background calculation starts."""

    user_database_path: Path
    snapshot_id: int
    profile_id: int
    profile_version: int
    top_k: int
    include_role_top_k: bool = True


@dataclass(frozen=True, slots=True)
class WeightedAllocationPreview:
    """Solver output plus the static dataset fixed at calculation start."""

    result: AllocationSolveResult
    static_dataset: StaticDatasetReference
    account_id: str
    user_database_path: Path
    context: AllocationContext
    # Built on the solver worker from the same frozen request.  These details
    # intentionally exclude current/saved inventory contexts: result cards
    # provide the immutable AllocationContext candidates themselves.
    role_details: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WeightedSavedPlanSignature:
    """The persisted identifiers needed to verify one restored role result."""

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
    """Latest SQLite-only preferences and an optional reproducible saved run."""

    user_database_path: Path
    profile_id: int | None
    profile_version: int | None
    characters: tuple[dict[str, Any], ...]
    restore_request: WeightedAllocationRequest | None
    saved_plans: tuple[WeightedSavedPlanSignature, ...]
    static_dataset: StaticDatasetReference | None


def read_weighted_allocation_persistence(
    user_database_path: Path,
) -> WeightedAllocationPersistence:
    """Read the latest weighted preferences and saved plans from user SQLite.

    This deliberately does not consult the old JSON priority files.  A restore
    request is exposed only when every role in the latest immutable preference
    version has one active weighted-allocation plan from the same snapshot and
    static dataset.
    """

    database_path = Path(user_database_path)
    if not database_path.is_file():
        return WeightedAllocationPersistence(database_path, None, None, (), None, (), None)
    with UserDataDao(database_path) as user_dao:
        profile = next(
            (
                row for row in user_dao.list_optimization_profiles()
                if row.get("name") == "__weighted_allocation_role_priority__"
            ),
            None,
        )
        if profile is None or not isinstance(profile.get("version"), dict):
            return WeightedAllocationPersistence(database_path, None, None, (), None, (), None)
        version = profile["version"]
        profile_id = int(profile["profile_id"])
        profile_version = int(version["version_number"])
        characters = tuple(dict(row) for row in version.get("characters") or ())
        expected_character_ids = {int(row["character_id"]) for row in characters}
        matching: dict[int, dict[str, Any]] = {}
        for plan in user_dao.list_loadout_plans():
            payload = plan.get("payload")
            character_id = int(plan["character_id"])
            if (
                plan.get("is_active")
                and character_id in expected_character_ids
                and isinstance(payload, dict)
                and payload.get("schema") == "allocation-official-snapshot-v1"
                and payload.get("source") == "weighted_allocation"
                and payload.get("profile_id") == profile_id
                and payload.get("profile_version") == profile_version
            ):
                matching.setdefault(character_id, plan)

    if not characters or set(matching) != expected_character_ids:
        return WeightedAllocationPersistence(
            database_path, profile_id, profile_version, characters, None, (), None,
        )
    snapshots = {int(plan["source_snapshot_id"]) for plan in matching.values()}
    solver_versions = {
        str(plan["payload"].get("solver_version") or "") for plan in matching.values()
    }
    dataset_rows = [plan["payload"].get("static_dataset") for plan in matching.values()]
    if (
        len(snapshots) != 1
        or solver_versions != {ALLOCATION_CONTEXT_SOLVER_VERSION}
        or not dataset_rows
        or not isinstance(dataset_rows[0], dict)
        or any(row != dataset_rows[0] for row in dataset_rows[1:])
    ):
        return WeightedAllocationPersistence(
            database_path, profile_id, profile_version, characters, None, (), None,
        )
    dataset = dataset_rows[0]
    try:
        static_dataset = StaticDatasetReference(
            schema_version=int(dataset["schema_version"]),
            dataset_id=str(dataset["dataset_id"]),
            importer_version=int(dataset["importer_version"]),
            built_at_utc=str(dataset["built_at_utc"]),
        )
    except (KeyError, TypeError, ValueError):
        return WeightedAllocationPersistence(
            database_path, profile_id, profile_version, characters, None, (), None,
        )
    signatures = tuple(
        WeightedSavedPlanSignature(
            plan_id=int(plan["plan_id"]),
            character_id=character_id,
            score=float(plan.get("score") or 0.0),
            uids=frozenset(
                (int(item["uid_slot"]), int(item["uid_serial"]))
                for item in plan.get("assignments") or ()
            ),
            assignments=tuple(
                WeightedSavedAssignmentSignature(
                    uid=(int(item["uid_slot"]), int(item["uid_serial"])),
                    kind=str(item["kind"]),
                    target_row=(int(item["target_row"]) if item.get("target_row") is not None else None),
                    target_column=(int(item["target_column"]) if item.get("target_column") is not None else None),
                    score=(
                        float((plan["payload"].get("assignment_scores") or {}).get(
                            f"nte-{item['kind']}-{item['uid_slot']}-{item['uid_serial']}"
                        ))
                        if f"nte-{item['kind']}-{item['uid_slot']}-{item['uid_serial']}"
                        in (plan["payload"].get("assignment_scores") or {})
                        else None
                    ),
                    virtual=bool(
                        (item.get("raw_assignment") or {}).get("virtual")
                    ),
                    item_id=(
                        (item.get("raw_assignment") or {})
                        .get("virtual_equipment", {})
                        .get("item_id")
                    ),
                    suit_id=(
                        (item.get("raw_assignment") or {})
                        .get("virtual_equipment", {})
                        .get("suit_id")
                    ),
                    geometry=(
                        (item.get("raw_assignment") or {}).get("geometry")
                        or (item.get("raw_assignment") or {})
                        .get("virtual_equipment", {})
                        .get("geometry")
                    ),
                    grid_count=(
                        int(
                            (item.get("raw_assignment") or {}).get("grid_count")
                            or (item.get("raw_assignment") or {})
                            .get("virtual_equipment", {})
                            .get("grid_count")
                        )
                        if (
                            (item.get("raw_assignment") or {}).get("grid_count")
                            or (item.get("raw_assignment") or {})
                            .get("virtual_equipment", {})
                            .get("grid_count")
                        ) is not None
                        else None
                    ),
                )
                for item in plan.get("assignments") or ()
            ),
        )
        for character_id, plan in sorted(matching.items())
    )
    request = WeightedAllocationRequest(
        database_path,
        snapshot_id=next(iter(snapshots)),
        profile_id=profile_id,
        profile_version=profile_version,
        top_k=1,
        include_role_top_k=False,
    )
    return WeightedAllocationPersistence(
        database_path, profile_id, profile_version, characters,
        request, signatures, static_dataset,
    )


def restore_weighted_allocation_preview(
    persistence: WeightedAllocationPersistence,
) -> WeightedAllocationPreview | None:
    """Rebuild a saved result against the current official static constraints."""

    if not isinstance(persistence, WeightedAllocationPersistence):
        raise TypeError("restoring requires WeightedAllocationPersistence")
    if persistence.restore_request is None:
        return None
    preview = run_weighted_allocation(persistence.restore_request)
    expected = {plan.character_id: plan for plan in persistence.saved_plans}
    selected = {option.character_id: option for option in preview.result.unified.selected}
    if set(selected) != set(expected):
        raise RuntimeError("已保存方案与当前固定快照的可用角色不一致。")
    updated_options = []
    for option in preview.result.unified.selected:
        character_id = option.character_id
        signature = expected[character_id]
        updated_options.append(_restore_saved_option(preview.context, option, signature))
    unified = replace(
        preview.result.unified,
        total_score=sum(float(option.score) for option in updated_options),
        selected=tuple(updated_options),
        explanation=tuple(preview.result.unified.explanation) + ("已按当前官方蓝图恢复保存方案",),
    )
    return replace(preview, result=replace(preview.result, unified=unified))


def _restore_saved_option(context, option, signature: WeightedSavedPlanSignature):
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
            (candidate is None and not saved.virtual)
            or (candidate is not None and candidate.kind != saved.kind)
            or saved.score is None
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
    return replace(
        option,
        score=signature.score,
        assignments=tuple(restored),
        generated_board=tuple(tuple(row) for row in generated_board),
    )


def run_weighted_allocation(request: WeightedAllocationRequest) -> WeightedAllocationPreview:
    """Build one immutable Context and solve it without touching legacy UI state.

    This is deliberately a small UI-facing facade.  It does not translate,
    score, or solve equipment itself; those behaviours remain in the audited
    Context and solver modules.
    """

    if not isinstance(request, WeightedAllocationRequest):
        raise TypeError("weighted allocation requires a WeightedAllocationRequest")
    if not request.user_database_path.is_file():
        raise RuntimeError("没有可用的账号背包数据库，请先完成背包同步。")
    if not 1 <= int(request.top_k) <= 20:
        raise ValueError("Top-K 必须在 1 到 20 之间。")

    with UserDataDao(request.user_database_path) as user_dao, StaticGameDataDao() as static_dao:
        context = build_allocation_context(
            user_dao,
            static_dao,
            snapshot_id=int(request.snapshot_id),
            profile_id=int(request.profile_id),
            profile_version=int(request.profile_version),
            solver_version=ALLOCATION_CONTEXT_SOLVER_VERSION,
        )
    result = solve_allocation_context(
        context, top_k=int(request.top_k), include_role_top_k=request.include_role_top_k,
        allow_missing_core=True,
    )
    role_details: dict[int, Mapping[str, Any]] = {}
    for option in result.unified.selected:
        try:
            role_details[option.character_id] = load_official_role_detail(
                request.user_database_path,
                option.character_id,
                include_inventory_contexts=False,
            )
        except (OSError, ValueError):
            # The allocation itself remains valid when an optional UI-only
            # damage summary cannot be prepared for one official role.
            continue
    return WeightedAllocationPreview(
        result=result,
        static_dataset=context.static_dataset,
        account_id=context.account_id,
        user_database_path=request.user_database_path,
        context=context,
        role_details=role_details,
    )


def save_weighted_allocation_preview(preview: WeightedAllocationPreview) -> tuple[int, ...]:
    """Persist the unified result as SQLite plans, without performing equip RPCs.

    The selected Context identifiers are stored in each plan payload so a later
    consumer can distinguish this reproducible recommendation from a live game
    action.  This function never imports or invokes an equipment-apply service.
    """

    if not isinstance(preview, WeightedAllocationPreview):
        raise TypeError("saving requires a WeightedAllocationPreview")
    result = preview.result
    if not result.unified.selected:
        raise RuntimeError("没有可保存的统一分配方案。")
    with UserDataDao(preview.user_database_path) as user_dao, StaticGameDataDao() as static_dao:
        role_names = {
            int(character["character_id"]): str(character.get("name_zh") or character["character_id"])
            for character in static_dao.list_characters()
        }
        bridge = SavedStateLoadoutBridge(user_dao, static_dao)
        prepared_plans: list[dict[str, Any]] = []
        for option in result.unified.selected:
            role_name = role_names.get(option.character_id)
            if role_name is None:
                raise RuntimeError(f"静态数据集中找不到角色 {option.character_id}。")
            prepared = bridge.prepare_role_plan(
                role_name=role_name,
                role_state=_role_state(option),
                character_id=option.character_id,
                snapshot_id=result.snapshot_id,
                name=f"词条配装：{role_name}",
                score=option.score,
                payload={
                    "schema": "allocation-official-snapshot-v1",
                    "source": "weighted_allocation",
                    "source_role_name": role_name,
                    "allocation_strategy": result.unified.strategy,
                    "profile_id": result.profile_id,
                    "profile_version": result.profile_version,
                    "solver_version": result.solver_version,
                    "assignment_scores": {
                        f"nte-{assignment.kind}-{assignment.uid[0]}-{assignment.uid[1]}": assignment.score
                        for assignment in option.assignments
                    },
                    "static_dataset": {
                        "schema_version": preview.static_dataset.schema_version,
                        "dataset_id": preview.static_dataset.dataset_id,
                        "importer_version": preview.static_dataset.importer_version,
                        "built_at_utc": preview.static_dataset.built_at_utc,
                    },
                },
            )
            prepared_plans.append(prepared.as_record())
        return user_dao.replace_active_loadout_plans(prepared_plans)


def replace_weighted_allocation_assignment(
    preview: WeightedAllocationPreview,
    *,
    old_uid: tuple[int, int],
    new_uid: tuple[int, int],
    new_score: float,
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

    changed_count = 0
    displaced_count = 0
    updated_options: list[RoleAllocationOption] = []
    for option in preview.result.unified.selected:
        updated_assignments = []
        option_score = float(option.score)
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
                    score=float(new_score),
                    contributions=(),
                    virtual=False,
                    grid_count=candidate.grid_count,
                ))
                option_score += float(new_score) - float(assignment.score)
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
                option_score -= float(assignment.score)
                option_changed = True
                displaced_count += 1
                continue
            updated_assignments.append(assignment)
        updated_options.append(
            replace(
                option,
                score=option_score,
                assignments=tuple(updated_assignments),
            )
            if option_changed else option
        )
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
    return replace(preview, result=replace(preview.result, unified=unified))


def _role_state(option: RoleAllocationOption) -> dict[str, object]:
    """Project a Context result into the existing SQLite plan bridge input."""

    drives = [
        {
            EQUIP_UID: f"nte-module-{assignment.uid[0]}-{assignment.uid[1]}",
            EQUIP_SHAPE_ID: str(legacy_shape_id(assignment.geometry or "")),
            "geometry": assignment.geometry,
            "grid_count": assignment.grid_count,
            "virtual": assignment.virtual,
            "virtual_equipment": (
                {
                    "item_id": assignment.item_id,
                    "kind": "module",
                    "suit_id": assignment.suit_id,
                    "geometry": assignment.geometry,
                    "grid_count": assignment.grid_count,
                    "quality": "orange",
                }
                if assignment.virtual
                else None
            ),
        }
        for assignment in option.assignments
        if assignment.kind == "module"
    ]
    core = next((assignment for assignment in option.assignments if assignment.kind == "core"), None)
    return {
        ROLE_BLUEPRINT_LAYOUT: [list(row) for row in option.generated_board],
        ROLE_EQUIPPED_DRIVES: drives,
        ROLE_EQUIPPED_TAPE: (
            {
                EQUIP_UID: f"nte-core-{core.uid[0]}-{core.uid[1]}",
                "virtual": core.virtual,
                "virtual_equipment": (
                    {
                        "item_id": core.item_id,
                        "kind": "core",
                        "suit_id": core.suit_id,
                        "geometry": None,
                        "grid_count": None,
                        "quality": "orange",
                    }
                    if core.virtual
                    else None
                ),
            }
            if core is not None
            else None
        ),
    }
