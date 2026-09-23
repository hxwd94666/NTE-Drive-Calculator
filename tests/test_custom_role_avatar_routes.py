# 验证自定义角色身份在计算选择、配装列表与临时归属头像之间传递。
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from src.features.inventory import equipment_display_loaders
from src.features.inventory import warehouse
from src.features.inventory.equipment_master_detail_view import (
    _equipment_role_icon,
    sorted_equipment_role_states,
)
from src.features.inventory.warehouse import (
    _equipped_owner_portrait,
    warehouse_item_view,
)
from src.features.weighted_allocation.weighted_shell import _weighted_selector_roles
from src.optimizer.contracts import ROLE_TOTAL_SCORE


def test_weighted_selector_passes_only_verified_custom_identity() -> None:
    entries = _weighted_selector_roles(
        {"自定义": 9001, "正式": 1001},
        {9001: "set"},
        {"set": "测试套装"},
        frozenset({9001}),
    )

    assert entries["自定义"] == {
        "character_id": 9001,
        "default_set": "测试套装",
        "is_custom": True,
    }
    assert entries["正式"]["is_custom"] is False


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


def test_equipment_navigator_uses_shared_custom_portrait_without_official_lookup() -> None:
    QApplication.instance() or QApplication([])

    class Catalog:
        def character_icon(self, _character_id):
            raise AssertionError("custom role must not use an official portrait")

    icon = _equipment_role_icon(
        {"_character_id": 9001, "_is_custom_role": True},
        Catalog(),
        2.0,
    )

    assert not icon.isNull()
    assert not icon.pixmap(42, 42).isNull()
    assert _equipment_role_icon(
        {"_character_id": 9001, "_is_custom_role": False}, None, 1.0
    ).isNull()


def test_temporary_custom_owner_portrait_survives_card_projection(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    monkeypatch.setattr(warehouse, "_warehouse_item_icon", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(warehouse, "_character_icon", lambda *_args, **_kwargs: None)
    row = warehouse_item_view({
        "kind": "module",
        "quality": "orange",
        "equipped": True,
        "equipped_character_id": 9001,
        "equipped_character_name": "自定义",
        "equipped_character_is_custom": True,
    })

    assert row["equipped_character_is_custom"] is True
    assert not _equipped_owner_portrait(row, 2.0).isNull()
    assert isinstance(_equipped_owner_portrait(row, 2.0), QPixmap)
