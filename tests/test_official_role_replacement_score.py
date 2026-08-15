# 验证角色页替换不会把直伤数值写入配装总评分。
from pathlib import Path
from unittest.mock import patch

from src.services.official_role_page_service import save_official_role_replacement


def test_role_replacement_updates_equipment_score_not_direct_damage() -> None:
    captured: dict = {}

    class FakeDao:
        def __init__(self, _path) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def replace_active_loadout_plans(self, plans):
            captured["plan"] = plans[0]
            return (88,)

    target = {
        "uid_slot": 10,
        "uid_serial": 20,
        "kind": "module",
        "geometry": "Hen2",
        "grid_count": 2,
    }
    detail = {
        "character": {"name_zh": "测试角色"},
        "equipment_contexts": {
            "saved": {
                "plan": {
                    "plan_id": 7,
                    "character_id": 1003,
                    "source_snapshot_id": 1,
                    "score": 261.0,
                    "payload": {
                        "schema": "game-observed-loadout-v1",
                        "source_role_name": "测试角色",
                        "assignment_scores": {
                            "nte-module-10-20": 25.0,
                            "nte-module-11-21": 236.0,
                        },
                    },
                    "assignments": [
                        {"uid_slot": 10, "uid_serial": 20, "kind": "module"},
                        {"uid_slot": 11, "uid_serial": 21, "kind": "module"},
                    ],
                },
            },
        },
    }
    replacement = {
        "uid_slot": 12,
        "uid_serial": 22,
        "kind": "module",
        "geometry": "Hen2",
        "grid_count": 2,
    }

    with patch("src.services.official_role_replacement_service.UserDataDao", FakeDao):
        plan_id = save_official_role_replacement(
            Path("test.sqlite3"),
            detail,
            target,
            replacement,
            replacement_score=31.0,
            current_score=25.0,
        )

    assert plan_id == 88
    plan = captured["plan"]
    assert plan["score"] == 267.0
    assert plan["score"] != 2127.0
    assert plan["payload"]["assignment_scores"] == {
        "nte-module-11-21": 236.0,
        "nte-module-12-22": 31.0,
    }
    assert plan["payload"]["schema"] == "game-observed-loadout-v1"
    assert plan["payload"]["source_role_name"] == "测试角色"


def test_role_replacement_keeps_the_selected_secondary_slot() -> None:
    captured: dict = {}

    class FakeDao:
        def __init__(self, _path) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def save_plan_to_slot(self, slot_id, **plan):
            captured["slot_id"] = slot_id
            captured["plan"] = plan
            return 99

    target = {
        "uid_slot": 10,
        "uid_serial": 20,
        "kind": "module",
        "geometry": "Hen2",
    }
    detail = {
        "character": {"name_zh": "测试角色"},
        "equipment_contexts": {
            "saved:27": {
                "slot_id": 27,
                "plan": {
                    "plan_id": 7,
                    "character_id": 1003,
                    "source_snapshot_id": 1,
                    "score": 25.0,
                    "payload": {"source_role_name": "测试角色"},
                    "assignments": [dict(target)],
                },
            },
        },
    }
    replacement = {
        "uid_slot": 12,
        "uid_serial": 22,
        "kind": "module",
        "geometry": "Hen2",
    }

    with patch("src.services.official_role_replacement_service.UserDataDao", FakeDao):
        saved_plan_id = save_official_role_replacement(
            Path("test.sqlite3"),
            detail,
            target,
            replacement,
            context_key="saved:27",
            replacement_score=31.0,
            current_score=25.0,
        )

    assert saved_plan_id == 99
    assert captured["slot_id"] == 27
    assert captured["plan"]["assignments"][0]["uid_slot"] == 12


def test_virtual_drive_replacement_rebuilds_stale_plan_total() -> None:
    _assert_virtual_replacement_rebuilds_total("module")


def test_virtual_tape_replacement_rebuilds_stale_plan_total() -> None:
    _assert_virtual_replacement_rebuilds_total("core")


def test_incomplete_legacy_scores_are_rebuilt_from_current_items() -> None:
    captured: dict = {}

    class FakeDao:
        def __init__(self, _path) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def replace_active_loadout_plans(self, plans):
            captured["plan"] = plans[0]
            return (100,)

    virtual_core = {
        "uid_slot": 0,
        "uid_serial": 101,
        "kind": "core",
        "virtual": True,
        "virtual_equipment": {"kind": "core"},
    }
    drives = [
        {"uid_slot": index, "uid_serial": index + 1000, "kind": "module"}
        for index in range(1, 8)
    ]
    replacement = {
        "uid_slot": 9,
        "uid_serial": 303,
        "kind": "core",
    }
    detail = {
        "character": {"name_zh": "测试角色"},
        "equipment_contexts": {
            "saved": {
                "plan": {
                    "plan_id": 54,
                    "character_id": 1003,
                    "source_snapshot_id": 43,
                    "score": 234.39,
                    "payload": {
                        "assignment_scores": {"nte-core-0-101": 0.0},
                    },
                    "assignments": [*drives, virtual_core],
                },
            },
        },
    }
    drive_scores = [21.43, 21.94, 21.94, 21.94, 23.14, 24.0, 16.0]
    current_scores = {
        f"nte-module-{item['uid_slot']}-{item['uid_serial']}": score
        for item, score in zip(drives, drive_scores)
    }
    current_scores["nte-core-0-101"] = 0.0

    with patch(
        "src.services.official_role_replacement_service.UserDataDao",
        FakeDao,
    ):
        save_official_role_replacement(
            Path("test.sqlite3"),
            detail,
            virtual_core,
            replacement,
            replacement_score=132.86,
            current_score=0.0,
            current_assignment_scores=current_scores,
        )

    plan = captured["plan"]
    assert sum(drive_scores) == 150.39
    assert plan["score"] == 283.25
    assert plan["score"] != 367.25
    assert len(plan["payload"]["assignment_scores"]) == 8


def _assert_virtual_replacement_rebuilds_total(target_kind: str) -> None:
    captured: dict = {}

    class FakeDao:
        def __init__(self, _path) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def replace_active_loadout_plans(self, plans):
            captured["plan"] = plans[0]
            return (99,)

    other_kind = "core" if target_kind == "module" else "module"
    target = {
        "uid_slot": 0,
        "uid_serial": 101,
        "kind": target_kind,
        "virtual": True,
        "virtual_equipment": {"kind": target_kind},
    }
    other = {"uid_slot": 8, "uid_serial": 202, "kind": other_kind}
    replacement = {
        "uid_slot": 9,
        "uid_serial": 303,
        "kind": target_kind,
        "geometry": "Hen2" if target_kind == "module" else None,
        "grid_count": 2 if target_kind == "module" else None,
    }
    detail = {
        "character": {"name_zh": "测试角色"},
        "equipment_contexts": {
            "saved": {
                "plan": {
                    "plan_id": 8,
                    "character_id": 1003,
                    "source_snapshot_id": 1,
                    # Simulate the historical bug: the removed real item is
                    # still present in the persisted aggregate score.
                    "score": 140.0,
                    "payload": {
                        "assignment_scores": {
                            f"nte-{target_kind}-0-101": 0.0,
                            f"nte-{other_kind}-8-202": 45.0,
                        },
                    },
                    "assignments": [target, other],
                },
            },
        },
    }

    with patch("src.services.official_role_replacement_service.UserDataDao", FakeDao):
        save_official_role_replacement(
            Path("test.sqlite3"),
            detail,
            target,
            replacement,
            replacement_score=35.0,
            current_score=0.0,
        )

    plan = captured["plan"]
    assert plan["score"] == 80.0
    assert plan["score"] != 175.0
    assert plan["status"] == "saved"
