# 验证直伤目标独立保存、冻结槽位占用变化拒绝以及失败后原方案保持。
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.integrations.static_catalog_release import StaticCatalogReleaseReader
from src.services.allocation.locks import build_allocation_lock_snapshot
from src.services.allocation.plan_save import save_allocation_plans
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.equipment.test_saved_state_loadout_bridge import _inventory_item, _snapshot


ROOT = Path(__file__).resolve().parents[2]
NTE_TEST_TIER = "core"


def setup_case(tmp_path):
    path = tmp_path / "synthetic-user.sqlite3"
    with UserDataDao(path, account_id="direct-save-fixture") as dao:
        snapshot = dao.import_inventory_snapshot(_snapshot([
            _inventory_item(slot=41, serial=410, kind="module", geometry="ZhiJiao2"),
            _inventory_item(slot=51, serial=510, kind="core"),
        ]))
        locks = build_allocation_lock_snapshot(dao, inventory_snapshot_id=snapshot)
    static_path = ROOT / "data/game_static.sqlite3"
    release = StaticCatalogReleaseReader().freeze(static_path)
    stat = static_path.stat()
    payload = {
        "schema": "allocation-official-snapshot-v1", "source": "allocation", "strategy": "direct_priority",
        "source_role_name": "早雾", "assignment_scores": {"nte-module-41-410": 25.0, "nte-core-51-510": 40.0},
        "damage_optimization": {
            "objective": "standard_direct_v1", "model_id": "role_panel_standard_direct_v1", "status": "completed",
            "baseline_value": 1000.0, "candidate_value": 1100.0, "delta": 100.0,
            "source_snapshot_id": snapshot, "dataset_id": release.dataset_id, "character_id": 1003,
            "owners_sha256": hashlib.sha256(b"[]").hexdigest(),
            "input_identity": {"request_id": "public-save-job", "input_sha256": "a" * 64},
        },
    }
    row = dict(
        role_name="早雾", character_id=1003, snapshot_id=snapshot, name="冻结直伤方案", score=65.0,
        payload=payload, slot_id=None,
        role_state={
            "blueprint_layout": [["XX"] * 5, ["XX", "L_3_TL", "L_3_TL", "XX", "XX"],
                                 ["XX", "L_3_TL", "XX", "XX", "XX"], ["XX"] * 5, ["XX"] * 5],
            "equipped_drives": [{"uid": "nte-module-41-410", "shape_id": "L_3_TL"}],
            "equipped_tape": {"uid": "nte-core-51-510"},
        },
    )
    kwargs = dict(database_path=path, static_database_path=static_path,
                  static_identity=(static_path, release.dataset_id, (stat.st_size, stat.st_mtime_ns)),
                  static_release=release, lock_snapshot=locks, rows=[row],
                  checkpoint=lambda: None, progress=lambda _event: None)
    return path, snapshot, payload, kwargs


def test_frozen_damage_metadata_and_base_assignment_scores_round_trip(tmp_path):
    path, snapshot, payload, kwargs = setup_case(tmp_path)
    assert save_allocation_plans(**kwargs) == 1
    with UserDataDao(path) as dao:
        plan = dao.list_loadout_slots(1003)[0]["current_plan"]
    assert plan["source_snapshot_id"] == snapshot
    assert plan["score"] == 65.0
    assert plan["payload"]["assignment_scores"] == payload["assignment_scores"]
    assert plan["payload"]["damage_optimization"] == payload["damage_optimization"]


@pytest.mark.parametrize("locked", [False, True])
def test_only_locked_old_loadout_blocks_borrowing_on_save(tmp_path, locked):
    path, snapshot, _payload, kwargs = setup_case(tmp_path)
    with UserDataDao(path) as dao:
        old = dao.save_loadout_plan(name="公开占用夹具", character_id=1004,
            assignments=[{"uid_slot": 41, "uid_serial": 410, "kind": "module", "ordinal": 0,
                          "target_row": 2, "target_column": 2, "rotation": 0}],
            source_snapshot_id=snapshot, is_active=True, status="ready",
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "旧角色"})
        if locked:
            dao.set_allocation_lock(old, True)
    if locked:
        with pytest.raises(RuntimeError, match="锁定状态|锁定配装"):
            save_allocation_plans(**kwargs)
    else:
        assert save_allocation_plans(**kwargs) == 1
    with UserDataDao(path) as dao:
        if locked:
            assert dao.list_loadout_slots(1003) == []
            assert dao.list_loadout_slots(1004)[0]["current_plan_id"] == old
        else:
            assert dao.list_loadout_slots(1003)[0]["current_plan"]
            owners = dao.list_current_loadout_equipment_owners()
            assert {r["character_id"] for r in owners if r["uid_slot"] == 41 and r["uid_serial"] == 410} == {1003}
            # 借出只生成新当前方案，历史方案仍保留原真实装备。
            assert dao.get_loadout_plan(old)["assignments"][0]["uid_serial"] == 410


@pytest.mark.parametrize("field,value", [("source_snapshot_id", 999), ("dataset_id", "different"),
                                         ("character_id", 1004), ("model_id", "unknown")])
def test_damage_identity_mismatch_never_creates_a_plan(tmp_path, field, value):
    path, _snapshot, payload, kwargs = setup_case(tmp_path)
    payload["damage_optimization"][field] = value
    with pytest.raises(RuntimeError, match="冻结方案身份不一致"):
        save_allocation_plans(**kwargs)
    with UserDataDao(path) as dao:
        assert dao.list_loadout_slots(1003) == []


@pytest.mark.parametrize("status", ["failed", "not_ready"])
def test_unoptimized_b0_requires_explicit_original_strategy_save(tmp_path, status):
    path, _snapshot, payload, kwargs = setup_case(tmp_path)
    payload["damage_optimization"]["status"] = status
    with pytest.raises(RuntimeError, match="角色优先重新计算"):
        save_allocation_plans(**kwargs)
    with UserDataDao(path) as dao:
        assert dao.list_loadout_slots(1003) == []
