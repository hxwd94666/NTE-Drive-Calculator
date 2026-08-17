# 测试装配任务的稳定快照约束。
"""Stable snapshot pinning must not depend on delayed UI-state projection."""

from types import SimpleNamespace

from src.services.equipment_apply_service import EquipmentApplyService


def test_follow_up_apply_uses_current_sqlite_snapshot_when_sync_state_lags() -> None:
    class Dao:
        def current_inventory_snapshot_id(self):
            return 17

    class Sync:
        is_running = True
        state = SimpleNamespace(phase="listening", last_snapshot_id=16)

    service = EquipmentApplyService(Dao(), Sync())

    assert service.require_stable_snapshot() == 17


def test_follow_up_apply_allows_collecting_state_with_a_stable_snapshot() -> None:
    class Dao:
        def current_inventory_snapshot_id(self):
            return 17

    class Sync:
        is_running = True
        state = SimpleNamespace(phase="collecting", last_snapshot_id=17)

    service = EquipmentApplyService(Dao(), Sync())

    assert service.require_stable_snapshot() == 17
