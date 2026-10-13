# 保存冻结加权配装与对应槽位的同权重变动记录。
from __future__ import annotations

from typing import Any, Mapping
from dataclasses import replace
from pathlib import Path

from src.optimizer.contracts import (
    EQUIP_SHAPE_ID, EQUIP_UID, ROLE_BLUEPRINT_LAYOUT, ROLE_EQUIPPED_DRIVES, ROLE_EQUIPPED_TAPE,
)
from src.services.allocation_solver import RoleAllocationOption
from src.services.allocation.inventory import legacy_shape_id

from src.domain.allocation.inputs import AllocationContext
from src.services.allocation.locks import (
    build_allocation_lock_snapshot, verify_allocation_lock_snapshot,
)
from src.services.equipment.saved_state_loadout_bridge import SavedStateLoadoutBridge
from src.services.equipment.weighted_loadout_comparison_service import select_frozen_comparison
from src.services.allocation.slot_binding import assert_slot_bindings_current, assert_binding_comparisons
from src.integrations.static_catalog_release import StaticCatalogReleaseReader, static_catalog_commit_unit
from src.services.allocation.plan_save import require_allocation_release
from src.storage.sqlite.user_data_dao import UserDataDao

def require_weighted_preview_release(preview, database_path=None):
    """来源许可只取运行时完整release；dataset展示字段与Context也必须一致。"""
    release = require_allocation_release(getattr(preview, "static_release", None), preview.static_database_path)
    require_allocation_release(release, database_path)
    fields = ("schema_version", "dataset_id", "importer_version", "built_at_utc")
    if (preview.context.static_dataset != preview.static_dataset
            or tuple(getattr(preview.static_dataset, key) for key in fields)
            != tuple(getattr(release, key) for key in fields)):
        raise RuntimeError("预览数值与冻结静态发行身份不一致，请重新计算。")
    return release


def save_weighted_preview_records(
    preview,
    *,
    checkpoint,
    commit_context,
    slot_ids_by_character: Mapping[int, int | None] | None = None,
) -> tuple[int, ...]:
    # 只消费本次预览的逐件分数、来源快照及槽位比较；不从当前权重补算。
    checkpoint()
    release = require_weighted_preview_release(preview)
    if preview.ownership is None or preview.lock_snapshot is None:
        raise RuntimeError("预览缺少冻结保存边界，请重新计算")
    identity = preview.ownership.identity
    if (preview.account_id != identity.account_id
            or preview.context.account_id != identity.account_id
            or Path(preview.user_database_path).resolve() != identity.user_database_path
            or preview.result.snapshot_id != preview.lock_snapshot.inventory_snapshot_id
            or preview.context.snapshot.snapshot_id != preview.result.snapshot_id):
        raise RuntimeError("预览的账号或冻结库存身份不一致")
    result = preview.result
    bindings = tuple(preview.slot_bindings)
    bound_targets = {row.character_id: row.slot_id for row in bindings}
    explicit_writer = slot_ids_by_character is not None or preview.explicit_slots
    if bindings:
        expected = {option.character_id for option in result.unified.selected}
        if (set(bound_targets) != expected or len(bindings) != len(expected)
                or any(row.source_snapshot_id != result.snapshot_id
                       or row.profile_id != result.profile_id or row.profile_version != result.profile_version
                       for row in bindings)
                or (slot_ids_by_character is not None and dict(slot_ids_by_character) != bound_targets)):
            raise RuntimeError('恢复预览的槽位、来源或偏好绑定不一致，请重新计算。')
        assert_binding_comparisons(bindings, preview.loadout_comparisons)
    if slot_ids_by_character is not None:
        slot_ids_by_character = dict(slot_ids_by_character)
    reader = StaticCatalogReleaseReader()
    # 正式unit持pin覆盖准备至DAO真实commit；最终validate仍在原账号提交锁内。
    with UserDataDao(preview.user_database_path) as user_dao, static_catalog_commit_unit(release.database_path, expected_release=release) as unit:
        static_dao = unit.dao
        def validate():
            checkpoint()
            if user_dao.profile()["account_id"] != identity.account_id:
                raise RuntimeError("保存数据库不属于冻结账号")
            reader.ensure_unchanged(release)
            verify_allocation_lock_snapshot(user_dao, preview.lock_snapshot)
        validate()
        assert_slot_bindings_current(user_dao, bindings)
        role_names = {
            int(character["character_id"]): str(character.get("name_zh") or character["character_id"])
            for character in static_dao.list_characters()
        }
        bridge = SavedStateLoadoutBridge(user_dao, static_dao)
        prepared_plans: list[dict[str, Any]] = []
        for option in result.unified.selected:
            checkpoint()
            role_name = role_names.get(option.character_id)
            if role_name is None:
                raise RuntimeError(f"静态数据集中找不到角色 {option.character_id}。")
            comparisons = preview.loadout_comparisons.get(int(option.character_id), ())
            if bindings:
                slot_id = bound_targets[int(option.character_id)]
            elif slot_ids_by_character is not None:
                if int(option.character_id) not in slot_ids_by_character:
                    raise RuntimeError(f"角色 [{role_name}] 缺少明确的配装槽位保存目标。")
                slot_id = slot_ids_by_character[int(option.character_id)]
            else:
                primary = next((row for row in comparisons if row.slot_key == "primary"), None)
                slot_id = primary.slot_id if primary is not None else None
            diff = select_frozen_comparison(comparisons, slot_id)
            prepared = bridge.prepare_role_plan(
                role_name=role_name,
                role_state=role_state(option),
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
                    "last_diff": diff,
                    "assignment_scores": {
                        f"nte-{assignment.kind}-{assignment.uid[0]}-{assignment.uid[1]}": assignment.score
                        for assignment in option.assignments
                    },
                    "tape_main_values": weighted_option_tape_main_values(preview.context, option),
                    "static_dataset": {
                        "schema_version": preview.static_dataset.schema_version,
                        "dataset_id": preview.static_dataset.dataset_id,
                        "importer_version": preview.static_dataset.importer_version,
                        "built_at_utc": preview.static_dataset.built_at_utc,
                    },
                },
            )
            record = prepared.as_record()
            record["comparison_baseline"] = diff
            if explicit_writer or bindings:
                # None是用户确认的“首个槽位”意图，只能随整批方案同事务创建。
                record["slot_id"] = int(slot_id) if slot_id is not None else None
                if slot_id is None:
                    record["create_slot_name"] = role_name
            prepared_plans.append(record)
        if explicit_writer:
            return user_dao.save_plans_to_slots(
                prepared_plans, checkpoint=checkpoint, validate=validate, commit_context=commit_context,
            )
        return user_dao.replace_active_loadout_plans(
            prepared_plans, checkpoint=checkpoint, validate=validate, commit_context=commit_context,
        )

def freeze_weighted_allocation_locks(user_dao, context):
    """同一读取事务冻结锁并以正式UID/角色ID排除保留，不改求解与评分公式。"""
    snapshot = build_allocation_lock_snapshot(
        user_dao, inventory_snapshot_id=context.snapshot.snapshot_id,
    )
    locked_ids = set()
    for role in context.roles:
        slots = user_dao.list_loadout_slots(role.character_id)
        if slots and all((slot.get("current_plan") or {}).get("allocation_locked") for slot in slots):
            locked_ids.add(role.character_id)
    return replace(
        context,
        roles=tuple(role for role in context.roles if role.character_id not in locked_ids),
        candidates=tuple(item for item in context.candidates
                         if f"nte-{item.kind}-{item.uid_slot}-{item.uid_serial}" not in snapshot.reserved_uids),
    ), snapshot


def role_state(option: RoleAllocationOption) -> dict[str, object]:
    """将冻结求解结果投影为既有 SQLite 方案桥输入。"""

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


# 保存同一预览中的正式卡带主词条；虚拟卡带不补造持久事实。
def weighted_option_tape_main_values(
    context: AllocationContext, option: RoleAllocationOption,
) -> dict[str, float]:
    candidates = {candidate.uid: candidate for candidate in context.candidates}
    for assignment in option.assignments:
        if assignment.kind != "core" or assignment.virtual:
            continue
        candidate = candidates.get(assignment.uid)
        stat = next(iter(candidate.main_stats), None) if candidate is not None else None
        if stat is None:
            continue
        value = float(stat.value) * (100.0 if stat.percent else 1.0)
        uid = f"nte-core-{assignment.uid[0]}-{assignment.uid[1]}"
        return {uid: round(value, 6)}
    return {}
