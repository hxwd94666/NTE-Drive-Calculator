# 测试批量装配局部事件复核。
"""Verify role-scoped residual events without replacing a full snapshot."""

from __future__ import annotations

from types import SimpleNamespace

from src.services.bulk_equipment_apply_service import BulkEquipmentApplyService


def _applied() -> list[dict]:
    return [{
        "role_name": "测试角色",
        "character_id": 1001,
        "character_uid": {"slot": 1, "serial": 2},
        "plan_id": 10,
        "job_item_id": 20,
        "module_count": 1,
        "already_applied": False,
        "scoped_snapshot_cursor": 3,
        "scoped_required_uids": frozenset({(8, 9)}),
    }]


class _Dao:
    def __init__(self) -> None:
        self.marked: list[dict] = []

    def get_sync_settings(self):
        return {"inventory_settle_seconds": 0.01}

    def mark_equipment_apply_job_item(self, _item_id, **values):
        self.marked.append(values)


def test_scoped_equipment_event_confirms_role_without_global_snapshot() -> None:
    class ApplyService:
        def __init__(self) -> None:
            self.calls = []

        def verify_plan_in_items(self, _plan_id, **values):
            self.calls.append(values)
            return None

    class Sync:
        def __init__(self) -> None:
            self.global_waits = 0
            self.scoped_waits = []

        def wait_for_equipment_snapshot(self, required_uids, *, after_cursor, timeout):
            self.scoped_waits.append((required_uids, after_cursor))
            return SimpleNamespace(items=({"uid": {"slot": 8, "serial": 9}},))

        def wait_for_snapshot(self, *_args, **_kwargs):
            self.global_waits += 1
            raise AssertionError("不应等待完整库存快照")

    dao = _Dao()
    sync = Sync()
    apply_service = ApplyService()
    applied = _applied()
    result = BulkEquipmentApplyService("unused.sqlite3", sync)._postcheck_and_repair(
        dao, apply_service, [{"plan_id": 10}], applied, 7, None,
    )

    assert result["scoped_verification_count"] == 1
    assert applied[0]["verified"] is True
    assert applied[0]["verification_source"] == "scoped_equipment_event"
    assert sync.scoped_waits == [(frozenset({(8, 9)}), 3)]
    assert sync.global_waits == 0
    assert dao.marked[-1]["verified"] is True
    assert apply_service.calls[-1]["fragment_only"] is True


def test_missing_scoped_event_keeps_dispatch_result_without_global_snapshot_wait() -> None:
    class ApplyService:
        def verify_plan_in_items(self, *_args, **_kwargs):
            raise AssertionError("没有局部事件时不应调用复核器")

    class Sync:
        def __init__(self) -> None:
            self.global_waits = 0
            self.scoped_waits = 0

        def wait_for_equipment_snapshot(self, *_args, **_kwargs):
            self.scoped_waits += 1
            raise TimeoutError

        def wait_for_snapshot(self, *_args, **_kwargs):
            self.global_waits += 1
            raise AssertionError("不应等待完整库存快照")

    dao = _Dao()
    sync = Sync()
    applied = _applied()
    result = BulkEquipmentApplyService("unused.sqlite3", sync)._postcheck_and_repair(
        dao, ApplyService(), [{"plan_id": 10}], applied, 7, None,
    )

    assert result["scoped_snapshot_wait_timed_out"] is True
    assert result["scoped_unverified_count"] == 1
    assert sync.scoped_waits == 1
    assert sync.global_waits == 0


def test_scoped_mismatch_retries_only_that_role_and_confirms_without_global_snapshot() -> None:
    class ApplyService:
        def __init__(self) -> None:
            self.apply_calls = 0

        def verify_plan_in_items(self, _plan_id, *, items, **_values):
            return None if items[0]["equipped"] else "驱动未装备"

        def apply_plan(self, _plan_id, **_values):
            self.apply_calls += 1
            return SimpleNamespace(already_applied=False)

    class Sync:
        def __init__(self) -> None:
            self.events = [False, True]
            self.cursor = 3
            self.global_waits = 0

        def scoped_equipment_snapshot_cursor(self):
            self.cursor += 1
            return self.cursor

        def wait_for_equipment_snapshot(self, *_args, **_kwargs):
            return SimpleNamespace(items=({"equipped": self.events.pop(0)},))

        def wait_for_snapshot(self, *_args, **_kwargs):
            self.global_waits += 1
            raise AssertionError("不应等待完整库存快照")

    dao = _Dao()
    sync = Sync()
    apply_service = ApplyService()
    applied = _applied()
    result = BulkEquipmentApplyService("unused.sqlite3", sync)._postcheck_and_repair(
        dao, apply_service, [{"plan_id": 10}], applied, 7, None,
    )

    assert result["repair_errors"] == []
    assert result["scoped_verification_count"] == 1
    assert applied[0]["repair_verified"] is True
    assert apply_service.apply_calls == 1
    assert sync.events == []
    assert sync.global_waits == 0


def test_dispatched_role_keeps_job_item_id_for_later_scoped_confirmation() -> None:
    class Dao:
        def mark_equipment_apply_job_item(self, *_args, **_kwargs):
            return None

    class ApplyService:
        def plan_equipment_uid_pairs(self, _plan_id):
            return frozenset({(8, 9)})

        def apply_plan(self, _plan_id, **_values):
            return SimpleNamespace(
                before_snapshot_id=7,
                after_snapshot_id=None,
                verified=False,
                already_applied=False,
                character_uid={"slot": 1, "serial": 2},
            )

    class Sync:
        def scoped_equipment_snapshot_cursor(self):
            return 3

    prepared = [{
        "job_item_id": 20,
        "role_name": "测试角色",
        "character_id": 1001,
        "character_uid": {"slot": 1, "serial": 2},
        "plan_id": 10,
        "module_count": 1,
        "core_count": 0,
    }]
    applied: list[dict] = []
    service = BulkEquipmentApplyService("unused.sqlite3", Sync())

    assert service._execute_prepared(
        Dao(), ApplyService(), prepared, 7, applied, [], 99, None
    ) is None
    assert applied[0]["job_item_id"] == 20


def test_dispatched_roles_project_target_loadouts_before_any_snapshot_arrives() -> None:
    class Dao:
        def __init__(self) -> None:
            self.projected = []

        def list_inventory_items(self, snapshot_id):
            assert snapshot_id == 7
            return [
                {
                    "uid_slot": 8, "uid_serial": 9, "kind": "module",
                    "locked": False, "discarded": False, "equipped": False,
                    "equipped_character_id": None, "equipped_character_uid": None,
                    "equipped_placement": None,
                },
                {
                    "uid_slot": 8, "uid_serial": 10, "kind": "module",
                    "locked": False, "discarded": False, "equipped": True,
                    "equipped_character_id": 1001,
                    "equipped_character_uid": {"slot": 1, "serial": 2},
                    "equipped_placement": {"row": 5, "column": 5},
                },
            ]

        def get_loadout_plan(self, plan_id):
            assert plan_id == 10
            return {"assignments": [{
                "uid_slot": 8, "uid_serial": 9, "kind": "module",
                "target_row": 2, "target_column": 3,
            }]}

        def apply_inventory_command_state_projection(self, snapshot_id, rows):
            self.projected.append((snapshot_id, rows))
            return len(rows)

    dao = Dao()
    service = BulkEquipmentApplyService("unused.sqlite3", object())
    updated = service._project_dispatched_loadouts(
        dao,
        [{
            "plan_id": 10,
            "character_id": 1001,
            "character_uid": {"slot": 1, "serial": 2},
        }],
        snapshot_id=7,
    )

    assert updated == 2
    assert dao.projected[0][0] == 7
    by_uid = {(row["uid"]["slot"], row["uid"]["serial"]): row for row in dao.projected[0][1]}
    assert by_uid[(8, 9)]["equipped"] is True
    assert by_uid[(8, 9)]["equipped_character_id"] == 1001
    assert by_uid[(8, 9)]["equipped_placement"] == {"row": 2, "column": 3}
    assert by_uid[(8, 10)]["equipped"] is False
