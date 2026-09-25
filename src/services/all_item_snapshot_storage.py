# 在同步任务的冻结账号与取消边界内保存全部物品，失败保留重试候选。
import sqlite3

from src.observability import log_event
from src.services.inventory_capture_wait import require_inventory_operation
from src.storage.sqlite.user_data_support import UserDataError


def store_all_item_snapshot(service, dao, client, status):
    error_type = status.get("native_all_item_error")
    if error_type:
        log_event("WARNING", "inventory_sync.all_items_read_failed", "全部物品归档等待重试，装备同步继续",
                  service._operation_context, error_type=error_type)
    snapshot = status.get("native_all_item_snapshot")
    if snapshot is None:
        return
    require_inventory_operation(service)
    try:
        saved_id = dao.save_all_item_snapshot(
            snapshot, account_id=service.account_id, check=lambda: require_inventory_operation(service),
        )
    except (sqlite3.Error, UserDataError) as error:
        # The lease retains this immutable candidate until an acknowledged commit.
        log_event("WARNING", "inventory_sync.all_items_save_failed", "全部物品归档未保存，保留候选稍后重试",
                  service._operation_context, error_type=type(error).__name__)
        return
    client.confirm_all_item_snapshot_saved(snapshot)
    log_event("INFO", "inventory_sync.all_items_saved", "已保存独立物品快照",
              service._operation_context, snapshot_id=saved_id, item_count=snapshot["recordCount"])
