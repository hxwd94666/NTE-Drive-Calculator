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

    with patch("src.services.official_role_page_service.UserDataDao", FakeDao):
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
