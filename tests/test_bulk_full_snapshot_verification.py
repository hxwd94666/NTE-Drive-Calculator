"""Full inventory snapshots are preferred over residual post-apply events."""

from __future__ import annotations

from types import SimpleNamespace

from src.services.bulk_equipment_apply_postcheck import (
    postcheck_and_repair,
    wait_for_guarded_full_snapshot,
)


def _applied() -> list[dict]:
    return [{
        "role_name": "测试角色",
        "character_id": 1001,
        "character_uid": {"slot": 1, "serial": 2},
        "plan_id": 10,
        "job_item_id": 20,
        "already_applied": False,
        "scoped_snapshot_cursor": 3,
        "scoped_required_uids": frozenset({(8, 9)}),
    }]


class _Dao:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.marked: list[dict] = []

    def inventory_snapshot_summary(self, snapshot_id: int) -> dict:
        assert snapshot_id == 8
        return {"source": "nte_core", "complete": 1}

    def list_inventory_items(self, snapshot_id: int) -> list[dict]:
        assert snapshot_id == 8
        return self.items

    def mark_equipment_apply_job_item(self, _job_item_id: int, **values) -> None:
        self.marked.append(values)


class _Sync:
    def wait_for_snapshot(self, *, after_snapshot_id: int, timeout: float):
        assert after_snapshot_id == 7
        assert timeout >= 0.0
        return SimpleNamespace(last_snapshot_id=8)


def test_complete_snapshot_verifies_full_plan_and_records_snapshot_id() -> None:
    class ApplyService:
        def verify_plan_in_snapshot(self, _plan_id: int, **values):
            assert values["stable_snapshot_id"] == 8
            assert values["exact_loadout"] is True
            return None

    dao = _Dao([{"uid_slot": 8, "uid_serial": 9}, {"uid_slot": 8, "uid_serial": 10}])
    applied = _applied()
    result = postcheck_and_repair(
        _Sync(),
        dao,
        ApplyService(),
        [{"plan_id": 10}],
        applied,
        stable_snapshot_id=7,
        frozen_inventory_uids=frozenset({(8, 9), (8, 10)}),
        timeout=1.0,
        max_attempts=3,
        report_progress=lambda *_args: None,
    )

    assert result["postcheck_snapshot_id"] == 8
    assert result["full_snapshot_verification_count"] == 1
    assert applied[0]["verification_source"] == "full_inventory_snapshot"
    assert dao.marked[-1]["after_snapshot_id"] == 8


def test_partial_snapshot_never_qualifies_as_complete_postcheck() -> None:
    dao = _Dao([{"uid_slot": 8, "uid_serial": 9}])

    assert wait_for_guarded_full_snapshot(
        _Sync(),
        dao,
        after_snapshot_id=7,
        frozen_inventory_uids=frozenset({(8, 9), (8, 10)}),
        timeout=1.0,
    ) is None
