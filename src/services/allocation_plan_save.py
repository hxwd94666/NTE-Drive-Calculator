# 在保存线程中复核冻结输入并保存配装，数据库连接不跨线程传递。
from __future__ import annotations

from src.services.allocation_lock_service import verify_allocation_lock_snapshot
from src.services.saved_state_loadout_bridge import SavedStateLoadoutBridge
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


def save_allocation_plans(*, database_path, static_database_path, static_identity,
                          lock_snapshot, rows, checkpoint, progress):
    def verify_static():
        pinned_path, dataset_id, file_identity = static_identity
        current = static_database_path.stat()
        if static_database_path != pinned_path or (current.st_size, current.st_mtime_ns) != file_identity:
            raise RuntimeError("计算使用的静态数据集已更新，请重新执行计算。")
        return dataset_id

    checkpoint()
    dataset_id = verify_static()
    with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
        if static_dao.summary()["dataset"]["dataset_id"] != dataset_id:
            raise RuntimeError("计算使用的静态数据集身份已改变，请重新执行计算。")
        verify_allocation_lock_snapshot(user_dao, lock_snapshot)
        bridge = SavedStateLoadoutBridge(
            user_dao, static_dao, frozen_snapshot_id=lock_snapshot.inventory_snapshot_id,
        )
        prepared = []
        for index, row in enumerate(rows, 1):
            checkpoint()
            arguments = {key: value for key, value in row.items() if key != "slot_id"}
            plan = bridge.prepare_role_plan(**arguments)
            slot = user_dao.get_loadout_slot(row["slot_id"])
            if slot is None or int(slot["character_id"]) != plan.character_id:
                raise RuntimeError("目标配装槽位已改变，请重新选择。")
            prepared.append({**plan.as_record(), "slot_id": row["slot_id"]})
            progress((f"已校验配装方案 {index}/{len(rows)}", index, len(rows) + 4))
        checkpoint()
        verify_static()
        verify_allocation_lock_snapshot(user_dao, lock_snapshot)
        progress(("正在写入方案并核对装备占用…", len(rows), len(rows) + 4))

        def commit_checkpoint():
            checkpoint()
            verify_static()

        user_dao.save_calculated_loadout_plans(
            prepared, checkpoint=commit_checkpoint,
            validate=lambda: verify_allocation_lock_snapshot(user_dao, lock_snapshot),
        )
        progress(("方案已保存，正在刷新页面…", len(rows) + 1, len(rows) + 4))
    return len(prepared)
