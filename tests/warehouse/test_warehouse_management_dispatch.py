# 在临时真实库验证派发前运行态冻结，并允许自身已接受状态变化继续批量处理及后验确认。
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from src.domain.equipment.eligibility.management_settings import MatchingScope
from src.services.inventory.management.management_settings import ManagementSettingsService
from src.services.inventory.recommendation.warehouse_recommendation_preview import RecommendationGuard, inventory_state_identity
from src.services.inventory.management.warehouse_state_management import (
    WarehouseStateManagementService,
    WarehouseStateManagementError,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.inventory.warehouse_recommendation_inputs import recommendation_account_identity
from tests.storage.test_user_data_inventory_dao import item
from tests.storage.test_user_data_inventory_dao import snapshot


def test_runtime_overlay_is_frozen_before_first_dispatch_but_own_changes_do_not_cancel_batch(tmp_path):
    path = tmp_path / "user.sqlite3"
    rows = [item(11, 11, "core"), item(12, 12, "core")]
    for row in rows:
        row["locked"] = False
    with UserDataDao(path, account_id="test") as dao:
        snapshot_id = dao.import_inventory_snapshot(snapshot(1, rows), source="nte_core")

    def observe(index, locked):
        rows[index]["locked"] = locked
        with UserDataDao(path) as dao:
            dao.apply_inventory_runtime_state_delta(snapshot_id, [rows[index]], force=True)

    observe(0, True)
    config, matching = ManagementSettingsService(path, scope=MatchingScope.WAREHOUSE).load()
    guard = RecommendationGuard(
        path,
        snapshot_id,
        recommendation_account_identity(path),
        (),
        (),
        matching,
        config,
        lambda: None,
        state_identity=inventory_state_identity(path, snapshot_id),
    )
    calls = []

    class Sync:
        is_running = True
        state = SimpleNamespace(phase="listening")
        core_hello_result = {"capabilities": ["equipment"]}

        def equipment_batch(self):
            return nullcontext()

        def set_item_locked(self, *, equipment, locked):
            calls.append((equipment["serial"], locked))
            observe(0 if equipment["serial"] == 11 else 1, locked)

        def set_item_discarded(self, **kwargs):
            raise AssertionError("本例不应派发弃置 RPC")

        def wait_for_snapshot(self, *, after_snapshot_id, timeout):
            assert after_snapshot_id == snapshot_id
            with UserDataDao(path) as dao:
                latest = dao.import_inventory_snapshot(snapshot(2, rows), source="nte_core")
            return SimpleNamespace(last_snapshot_id=latest)

    service = WarehouseStateManagementService(path, Sync(), input_guard=guard)
    targets = {"nte-core-11-11": "normal", "nte-core-12-12": "locked"}
    observe(1, True)
    with pytest.raises(WarehouseStateManagementError, match="装备状态已变化"):
        service.plan_manual_changes(snapshot_id, targets)
    assert not calls
    observe(1, False)
    plan = service.plan_manual_changes(snapshot_id, targets)
    assert len(plan.changes) == 2  # 第1件只在运行态中锁定；读取旧不可变快照会错误漏掉解锁。
    result = service.apply(plan)
    assert calls == [(11, False), (12, True)]
    assert result.verified and result.after_snapshot_id > snapshot_id
    with UserDataDao(path) as dao:
        original = dao.list_inventory_items(snapshot_id)
        assert [row["locked"] for row in original] == [False, False]
