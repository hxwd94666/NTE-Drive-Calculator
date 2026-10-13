# 在保存线程中复核冻结输入并保存配装，数据库连接不跨线程传递。
from __future__ import annotations

from typing import Any
from pathlib import Path
from src.domain.static_catalog import StaticCatalogRelease
from src.integrations.static_catalog_release import StaticCatalogReleaseReader, static_catalog_commit_unit

from src.optimizer.contracts import EQUIP_UID, PLAN_ASSIGNED_TAPE

from src.services.allocation.locks import verify_allocation_lock_snapshot
from src.services.equipment.saved_state_loadout_bridge import SavedStateLoadoutBridge
from src.storage.sqlite.user_data_dao import UserDataDao


def require_allocation_release(release, database_path):
    """只接受正式reader冻结的完整单目录身份；旧预览缺项明确要求重算。"""
    if (not isinstance(release, StaticCatalogRelease) or not release.sha256
            or not release.read_only or release.scope not in {"game", "reference"}):
        raise RuntimeError("预览缺少完整静态发行身份，请重新计算。")
    if database_path is not None and Path(database_path).expanduser().absolute() != release.database_path:
        raise RuntimeError("计算使用的静态来源路径已改变，请重新计算。")
    return release


def save_allocation_plans(*, database_path, static_database_path, static_identity,
                          lock_snapshot, rows, checkpoint, progress, static_release=None):
    checkpoint()
    release = require_allocation_release(static_release, static_database_path)
    if not static_identity or static_identity[:2] != (release.database_path, release.dataset_id):
        raise RuntimeError("计算结果与冻结静态来源不一致，请重新计算。")
    reader = StaticCatalogReleaseReader()
    # 同一拒写pin覆盖准备、暂存和真实commit；共享旧DAO连接不参与来源许可。
    with UserDataDao(database_path) as user_dao, static_catalog_commit_unit(release.database_path, expected_release=release) as unit:
        static_dao = unit.dao
        verify_allocation_lock_snapshot(user_dao, lock_snapshot)
        bridge = SavedStateLoadoutBridge(
            user_dao, static_dao, frozen_snapshot_id=lock_snapshot.inventory_snapshot_id,
        )
        prepared = []
        for index, row in enumerate(rows, 1):
            checkpoint()
            arguments = {key: value for key, value in row.items() if key != "slot_id"}
            plan = bridge.prepare_role_plan(**arguments)
            slot_id = row["slot_id"]
            if slot_id is None:
                if user_dao.list_loadout_slots(plan.character_id):
                    raise RuntimeError("目标配装槽位已改变，请重新选择。")
            else:
                slot = user_dao.get_loadout_slot(slot_id)
                if slot is None or int(slot["character_id"]) != plan.character_id:
                    raise RuntimeError("目标配装槽位已改变，请重新选择。")
            prepared.append({
                **plan.as_record(), "slot_id": slot_id,
                "comparison_baseline": (row.get("payload") or {}).get("last_diff"),
                "create_slot_name": row["role_name"] if slot_id is None else None,
            })
            progress((f"已校验配装方案 {index}/{len(rows)}", index, len(rows) + 4))
        checkpoint()
        reader.ensure_unchanged(release)
        verify_allocation_lock_snapshot(user_dao, lock_snapshot)
        progress(("正在写入方案并核对装备占用…", len(rows), len(rows) + 4))

        def commit_checkpoint():
            checkpoint()
            reader.ensure_unchanged(release)

        user_dao.save_calculated_loadout_plans(
            prepared, checkpoint=commit_checkpoint,
            validate=lambda: verify_allocation_lock_snapshot(user_dao, lock_snapshot),
        )
        progress(("方案已保存，正在刷新页面…", len(rows) + 1, len(rows) + 4))
    return len(prepared)


# 沿用该方案生成时的满级主词条，不读取新库存或新评分配置。
def legacy_plan_tape_main_values(plan: dict[str, Any]) -> dict[str, float]:
    tape = plan.get(PLAN_ASSIGNED_TAPE)
    if tape is None:
        return {}
    uid = str(tape.get(EQUIP_UID, "") if isinstance(tape, dict) else getattr(tape, EQUIP_UID, ""))
    value = tape.get("main_value") if isinstance(tape, dict) else getattr(tape, "main_value", None)
    try:
        return {uid: float(value)} if uid and value is not None else {}
    except (TypeError, ValueError):
        return {}
