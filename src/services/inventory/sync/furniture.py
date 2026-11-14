# 原生家具仅静默写入账号；取消与账号代次失效撤销，实际写入失败有界重试。
from time import monotonic

from src.domain.characters.furniture import native_furniture_values
from src.observability import log_event
from src.services.inventory.sync.capture_wait import InventorySyncCancelled, require_inventory_operation


def apply_native_furniture(service, client, status):
    snapshot = status.get('native_furniture_snapshot')
    apply = service._native_furniture_apply
    if apply is None or snapshot is None or monotonic() < service._native_furniture_retry_at:
        return
    try:
        require_inventory_operation(service)
        values = native_furniture_values(snapshot['records'])
        changed = apply(values, check=lambda: require_inventory_operation(service))
        require_inventory_operation(service)
        client.confirm_furniture_snapshot_saved(snapshot)
    except (InventorySyncCancelled, PermissionError):
        raise
    except Exception as error:
        service._native_furniture_retry_at = monotonic() + 10
        code = type(error).__name__
        if code != service._native_furniture_save_error:
            log_event('WARNING', 'inventory_sync.furniture_save_failed', '家具自动同步未保存，稍后重试',
                      service._operation_context, error_type=code)
        service._native_furniture_save_error = code
    else:
        service._native_furniture_save_error = None
        if changed:
            # 内部版本仅触发已有页面静默刷新，不增加提示、弹窗或前端通知文本。
            service._publish(service.state.phase, service.state.message,
                             character_sync_revision=service.state.character_sync_revision + 1)
