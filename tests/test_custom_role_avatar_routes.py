# 验证已保存自建角色的正式身份能进入当前配装导航。
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from src.features.inventory.equipment import display_loaders as equipment_display_loaders
from src.features.inventory.equipment.master_detail_view import (
    sorted_equipment_role_states,
)
from src.optimizer.contracts import ROLE_TOTAL_SCORE


def test_saved_custom_role_identity_reaches_equipment_navigator(
    monkeypatch, tmp_path: Path,
) -> None:
    class UserDao:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_current_loadout_slot_plans(self):
            return [{
                "slot": {"slot_id": 7, "slot_key": "primary", "slot_name": "主力"},
                "plan": {
                    "character_id": 9001,
                    "source_snapshot_id": 1,
                    "payload": {"source_role_name": "自定义"},
                    "assignments": [],
                },
            }]

        def list_visible_loadout_slots(self):
            return []

        def list_loadout_plans(self):
            return []

        def list_inventory_items(self, *_args, **_kwargs):
            return []

        def inventory_snapshot_summary(self, *_args):
            return {"source": "nte_core"}

        def list_custom_characters(self):
            return [{"character_id": 9001, "name_zh": "自定义"}]

    class StaticDao(UserDao):
        def list_shapes(self):
            return []

        def list_suits(self):
            return []

        def list_equipment_attributes(self):
            return []

        def list_characters(self):
            return [{"character_id": 1001, "name_zh": "正式"}]

    monkeypatch.setattr(equipment_display_loaders, "UserDataDao", UserDao)
    monkeypatch.setattr(equipment_display_loaders, "StaticGameDataDao", StaticDao)
    monkeypatch.setattr(
        equipment_display_loaders,
        "_sqlite_plan_display_state",
        lambda *_args, **_kwargs: {ROLE_TOTAL_SCORE: 0.0},
    )

    states = equipment_display_loaders._load_sqlite_equipment_display_states(
        tmp_path / "account.sqlite3",
        static_database_path=tmp_path / "static.sqlite3",
    )
    roles = sorted_equipment_role_states(states)

    assert roles[0][0] == "自定义"
    assert roles[0][1]["_is_custom_role"] is True


