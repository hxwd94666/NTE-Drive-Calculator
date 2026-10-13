# 验证仓库与极速装配的同步关闭提示统一导航工作台，且不创建执行任务。

NTE_TEST_TIER = "core"
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.features.inventory.equipment import assembly_controller as equipment_assembly_controller
from src.features.inventory.warehouse import controller as warehouse_controller


@pytest.mark.parametrize("sync", [None, SimpleNamespace(is_running=False)])
@pytest.mark.parametrize("operation", ["fast", "manual", "management"])
def test_closed_sync_guidance_preserves_inputs_and_does_not_dispatch(monkeypatch, sync, operation):
    pending = {"test-item": "locked"}
    owner = SimpleNamespace(
        operation_entry=lambda *_: True,
        operation_unavailable=Mock(),
        _inventory_sync_service=sync,
        _warehouse_pending_state_changes=pending,
        _warehouse_snapshot_id=1,
        _warehouse_source="nte_core",
        app_context=SimpleNamespace(
            account=SimpleNamespace(user_config_dir="unused", user_database_path="unused.sqlite3"),
            paths=SimpleNamespace(config_dir="unused", equipment_allocation_database_path="unused.sqlite3",
                                  equipment_allocation_asset_root="unused"),
        ),
    )
    warehouse_service = Mock()
    apply_service = Mock()
    monkeypatch.setattr(warehouse_controller, "WarehouseStateManagementService", warehouse_service)
    monkeypatch.setattr(equipment_assembly_controller, "BulkEquipmentApplyService", apply_service)
    monkeypatch.setattr(warehouse_controller, "show_scan_post_action_dialog", lambda *_a, **_k: True)
    monkeypatch.setattr(warehouse_controller, "load_scan_post_action_config", lambda *_a, **_k: {})
    monkeypatch.setattr(warehouse_controller, "validate_post_action_config", lambda _: None)
    if operation == "fast":
        equipment_assembly_controller._start_nte_core_equipment_apply(owner, ["测试角色"])
    elif operation == "manual":
        warehouse_controller._save_warehouse_state_changes(owner)
    else:
        warehouse_controller._open_warehouse_state_manager(owner)
    _label, detail, target = owner.operation_unavailable.call_args.args
    assert "同步连接未开启" in detail and "开启“自动同步”" in detail
    assert "进入游戏场景" in detail and target == "home"
    assert "部署" not in detail
    warehouse_service.assert_not_called()
    apply_service.assert_not_called()
    assert owner._warehouse_pending_state_changes == pending
