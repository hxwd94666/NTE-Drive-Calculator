# 验证游戏导入配装的差异展示与快照边界。
"""Regression coverage for game-loadout score, badges, and diff hydration."""

from src.features.inventory import equipment_display_loaders


def test_saved_plan_minimal_diff_is_hydrated_from_its_source_snapshot() -> None:
    from src.features.inventory.equipment_plan_optimizer import (
        _sqlite_plan_display_state,
    )

    class StaticDao:
        pass

    old_item = {
        "kind": "module",
        "uid_serial": 583613500,
        "uid_slot": 1309351432,
        "item_id": "old-drive",
        "geometry": "hen2",
        "quality": "orange",
        "main_stats": [],
        "sub_stats": [{"property_id": "CritBase", "value": 0.032, "percent": True}],
    }
    new_item = {
        "kind": "module",
        "uid_serial": 742324049,
        "uid_slot": 1323360682,
        "item_id": "new-drive",
        "geometry": "shu3",
        "quality": "orange",
        "main_stats": [],
        "sub_stats": [{"property_id": "CritDamageBase", "value": 0.064, "percent": True}],
    }
    old_uid = "nte-module-1309351432-583613500"
    new_uid = "nte-module-1323360682-742324049"
    plan = {
        "plan_id": 1,
        "source_snapshot_id": 43,
        "score": 12.0,
        "payload": {
            "last_diff": {
                "changed": True,
                "removed": [{"uid": old_uid}],
                "added": [{"uid": new_uid}],
            },
        },
        "assignments": [{
            "uid_serial": new_item["uid_serial"],
            "uid_slot": new_item["uid_slot"],
            "target_row": 1,
            "target_column": 1,
            "raw_assignment": {},
        }],
        "allocation_locked": False,
    }
    state = _sqlite_plan_display_state(
        plan,
        object(),
        StaticDao(),
        inventory_by_snapshot={
            43: {
                (old_item["uid_serial"], old_item["uid_slot"]): old_item,
                (new_item["uid_serial"], new_item["uid_slot"]): new_item,
            },
        },
        shape_cells={
            "EquipmentGeometry_hen2": [{"x": 1, "y": 1}],
            "EquipmentGeometry_shu3": [{"x": 1, "y": 1}],
        },
        suit_names={},
        attribute_ids={"CritBase", "CritDamageBase"},
    )

    removed = state["last_diff"]["removed"][0]
    added = state["last_diff"]["added"][0]
    assert (removed["type"], removed["shape_id"], removed["sub_stats"]) == (
        "drive",
        "H_2",
        {"暴击率%": 3.2},
    )
    assert (added["type"], added["shape_id"], added["sub_stats"]) == (
        "drive",
        "V_3",
        {"暴击伤害%": 6.4},
    )


def test_diff_tape_card_renders_its_projected_item_icon(tmp_path) -> None:
    from types import SimpleNamespace

    from PySide6.QtGui import QColor, QPixmap
    from PySide6.QtWidgets import QApplication, QLabel

    from src.ui.equipment_presentation import EquipmentPresentation

    app = QApplication.instance() or QApplication([])
    icon_path = tmp_path / "core.png"
    pixmap = QPixmap(24, 24)
    pixmap.fill(QColor("#ffaa00"))
    assert pixmap.save(str(icon_path))
    presentation = EquipmentPresentation(
        app_context=SimpleNamespace(
            paths=SimpleNamespace(asset_dir=tmp_path),
            account=SimpleNamespace(user_database_path=tmp_path / "user.sqlite3"),
        ),
        dialog_parent=None,
    )
    presentation.update_catalog(
        roles_db={"角色一": {"weights": {}, "main_weights": {}}},
        scoring_engine=None,
        shape_areas={},
    )

    card = presentation._diff_item_card(
        "角色一",
        {
            "uid": "nte-core-2-1",
            "type": "tape",
            "set_name": "测试卡带",
            "main_stats": "暴击率%",
            "sub_stats": {},
            "quality": "Gold",
            "item_icon_path": str(icon_path),
        },
    )

    assert any(
        label.pixmap() is not None and not label.pixmap().isNull()
        for label in card.findChildren(QLabel)
    )
    card.deleteLater()
    app.processEvents()


def test_virtual_core_diff_is_hydrated_and_grouped_as_one_tape_swap() -> None:
    from pathlib import Path
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QLabel

    from src.features.inventory.equipment_plan_optimizer import (
        _sqlite_plan_display_state,
    )
    from src.ui.equipment_presentation import EquipmentPresentation

    app = QApplication.instance() or QApplication([])
    old_uid = "nte-core-22-11"
    virtual_uid = "nte-core-0-101"
    plan = {
        "plan_id": 1,
        "source_snapshot_id": 43,
        "score": 0.0,
        "payload": {
            "last_diff": {
                "changed": True,
                "removed": [{"uid": old_uid}],
                "added": [{"uid": virtual_uid, "is_changed": True}],
            },
        },
        "assignments": [{
            "uid_serial": 101,
            "uid_slot": 0,
            "kind": "core",
            "raw_assignment": {
                "virtual": True,
                "uid_serial": 101,
                "uid_slot": 0,
                "kind": "core",
                "virtual_equipment": {
                    "kind": "core",
                    "suit_id": "Suit1",
                    "item_id": "virtual-core",
                },
            },
        }],
        "allocation_locked": False,
    }
    old_core = {
        "kind": "core",
        "uid_serial": 11,
        "uid_slot": 22,
        "item_id": "Attack_orange",
        "suit_id": "Suit1",
        "quality": "orange",
        "main_stats": [],
        "sub_stats": [],
    }
    state = _sqlite_plan_display_state(
        plan,
        object(),
        object(),
        inventory_by_snapshot={43: {(11, 22): old_core}},
        shape_cells={},
        suit_names={"Suit1": "测试套装"},
        attribute_ids=set(),
    )

    removed = state["last_diff"]["removed"][0]
    added = state["last_diff"]["added"][0]
    assert removed["type"] == "tape"
    assert added["type"] == "tape"
    assert added["set_name"] == "空空幕"

    presentation = EquipmentPresentation(
        app_context=SimpleNamespace(
            paths=SimpleNamespace(asset_dir=Path(".")),
            account=SimpleNamespace(user_database_path=Path("user.sqlite3")),
        ),
        dialog_parent=None,
    )
    presentation.update_catalog(
        roles_db={"角色一": {"weights": {}, "main_weights": {}}},
        scoring_engine=None,
        shape_areas={},
    )
    dialog = presentation.plan_diff_dialog("角色一", state["last_diff"])
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert "变动 1：卡带" in texts
    assert not any("新增" in text or "未知驱动" in text for text in texts)
    assert sum(text.startswith("变动 ") for text in texts) == 1
    dialog.deleteLater()
    app.processEvents()


def test_removed_diff_item_uses_previous_plan_fixed_snapshot(monkeypatch) -> None:
    import src.features.inventory.equipment_display_view as display_view

    old_item = {
        "kind": "module",
        "uid_serial": 10,
        "uid_slot": 11,
        "item_id": "old-drive",
        "geometry": "hen2",
        "quality": "orange",
        "main_stats": [],
        "sub_stats": [{"property_id": "CritBase", "value": 0.032, "percent": True}],
    }
    new_item = {
        "kind": "module",
        "uid_serial": 20,
        "uid_slot": 21,
        "item_id": "new-drive",
        "geometry": "shu3",
        "quality": "orange",
        "main_stats": [],
        "sub_stats": [{"property_id": "CritDamageBase", "value": 0.064, "percent": True}],
    }
    old_uid = "nte-module-11-10"
    new_uid = "nte-module-21-20"
    active = {
        "plan_id": 2,
        "character_id": 1003,
        "source_snapshot_id": 43,
        "score": 12.0,
        "payload": {
            "source_role_name": "角色一",
            "last_diff": {
                "changed": True,
                "removed": [{"uid": old_uid}],
                "added": [{"uid": new_uid}],
            },
        },
        "assignments": [{
            "uid_serial": 20,
            "uid_slot": 21,
            "target_row": 1,
            "target_column": 1,
            "raw_assignment": {},
        }],
        "allocation_locked": False,
    }
    previous = {
        "plan_id": 1,
        "character_id": 1003,
        "source_snapshot_id": 42,
        "payload": {"source_role_name": "角色一"},
        "assignments": [{
            "uid_serial": 10,
            "uid_slot": 11,
            "raw_assignment": {},
        }],
    }

    class UserDao:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_active_loadout_plans_by_role(self):
            return {"角色一": active}

        def list_loadout_plans(self):
            return [active, previous]

        def list_inventory_items(self, snapshot_id, *, uids):
            available = {43: [new_item], 42: [old_item]}[snapshot_id]
            return [
                dict(item)
                for item in available
                if (item["uid_serial"], item["uid_slot"]) in uids
            ]

        def inventory_snapshot_summary(self, _snapshot_id):
            return {"source": "nte_core"}

    class StaticDao:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_shapes(self):
            return [
                {"shape_id": "EquipmentGeometry_hen2", "cells": [{"x": 1, "y": 1}]},
                {"shape_id": "EquipmentGeometry_shu3", "cells": [{"x": 1, "y": 1}]},
            ]

        def list_suits(self):
            return []

        def list_equipment_attributes(self):
            return [
                {"attribute_id": "CritBase"},
                {"attribute_id": "CritDamageBase"},
            ]

    monkeypatch.setattr(equipment_display_loaders, "UserDataDao", UserDao)
    monkeypatch.setattr(equipment_display_loaders, "StaticGameDataDao", StaticDao)

    states = display_view._load_sqlite_equipment_display_states(
        "user.sqlite3",
        static_database_path="static.sqlite3",
    )

    removed = states["角色一"]["last_diff"]["removed"][0]
    assert removed["uid"] == old_uid
    assert removed["type"] == "drive"
    assert removed["shape_id"] == "H_2"
    assert removed["sub_stats"] == {"暴击率%": 3.2}


def test_persisted_virtual_slot_is_not_queried_as_snapshot_uid(
    monkeypatch,
) -> None:
    import src.features.inventory.equipment_display_view as display_view

    plan = {
        "plan_id": 1,
        "character_id": 1003,
        "source_snapshot_id": 7,
        "score": 0.0,
        "payload": {"source_role_name": "角色一"},
        "assignments": [{
            "uid_serial": 101,
            "uid_slot": 0,
            "kind": "module",
            "target_row": 1,
            "target_column": 1,
            "raw_assignment": {
                "virtual_equipment": {
                    "kind": "module",
                    "geometry": "EquipmentGeometry_Hen2",
                    "grid_count": 2,
                },
            },
        }],
        "allocation_locked": False,
    }

    class UserDao:
        queried_uids = []

        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_active_loadout_plans_by_role(self):
            return {"角色一": plan}

        def list_loadout_plans(self):
            return [plan]

        def list_inventory_items(self, _snapshot_id, *, uids):
            self.queried_uids.append(set(uids))
            return []

        def inventory_snapshot_summary(self, _snapshot_id):
            return {"source": "gamepad"}

    class StaticDao:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_shapes(self):
            return [{
                "shape_id": "EquipmentGeometry_Hen2",
                "cells": [{"x": 0, "y": 0}, {"x": 0, "y": 1}],
            }]

        def list_suits(self):
            return []

        def list_equipment_attributes(self):
            return [
                {"attribute_id": "AtkAdd"},
                {"attribute_id": "HPMaxAdd"},
            ]

    monkeypatch.setattr(equipment_display_loaders, "UserDataDao", UserDao)
    monkeypatch.setattr(equipment_display_loaders, "StaticGameDataDao", StaticDao)

    states = display_view._load_sqlite_equipment_display_states(
        "user.sqlite3",
        static_database_path="static.sqlite3",
    )

    state = states["角色一"]
    assert UserDao.queried_uids == [set()]
    assert state["equipped_drives"][0]["virtual"] is True
    assert state["blueprint_layout"][0][:2] == ["H_2", "H_2"]
    assert state["_sqlite_snapshot_source"] == "gamepad"


def test_saved_plan_display_accepts_packet_and_scan_snapshots(
    monkeypatch,
) -> None:
    import src.features.inventory.equipment_display_view as display_view

    item = {
        "kind": "module",
        "uid_serial": 10,
        "uid_slot": 11,
        "item_id": "drive",
        "geometry": "Hen2",
        "quality": "orange",
        "main_stats": [],
        "sub_stats": [],
    }
    plan = {
        "plan_id": 1,
        "character_id": 1003,
        "source_snapshot_id": 7,
        "score": 20.0,
        "payload": {"source_role_name": "角色一"},
        "assignments": [{
            "uid_serial": 10,
            "uid_slot": 11,
            "kind": "module",
            "target_row": 1,
            "target_column": 1,
            "raw_assignment": {},
        }],
        "allocation_locked": False,
    }

    class UserDao:
        source = "nte_core"

        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_active_loadout_plans_by_role(self):
            return {"角色一": plan}

        def list_loadout_plans(self):
            return [plan]

        def list_inventory_items(self, _snapshot_id, *, uids):
            return [dict(item)] if (10, 11) in uids else []

        def inventory_snapshot_summary(self, _snapshot_id):
            return {"source": self.source}

    class StaticDao:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def list_shapes(self):
            return [{
                "shape_id": "EquipmentGeometry_Hen2",
                "cells": [{"x": 0, "y": 0}, {"x": 0, "y": 1}],
            }]

        def list_suits(self):
            return []

        def list_equipment_attributes(self):
            return []

    monkeypatch.setattr(equipment_display_loaders, "UserDataDao", UserDao)
    monkeypatch.setattr(equipment_display_loaders, "StaticGameDataDao", StaticDao)

    for source in ("nte_core", "gamepad"):
        UserDao.source = source
        states = display_view._load_sqlite_equipment_display_states(
            "user.sqlite3",
            static_database_path="static.sqlite3",
        )
        state = states["角色一"]
        assert state["_sqlite_snapshot_source"] == source
        assert state["equipped_drives"][0]["shape_id"] == "H_2"


def test_missing_tape_game_detail_keeps_blueprint(monkeypatch) -> None:
    import src.features.inventory.equipment_display_view as display_view

    module = {
        "uid_serial": 11,
        "uid_slot": 21,
        "kind": "module",
        "item_id": "module-a",
        "geometry": "Hen2",
        "grid_count": 2,
        "quality": "orange",
        "equipped": True,
        "equipped_character_id": 1003,
        "main_stats": [],
        "sub_stats": [],
    }

    class Dao:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def current_inventory_snapshot_id(self):
            return 7

        def inventory_snapshot_summary(self, _snapshot_id):
            return {
                "source": "nte_core",
                "captured_at_utc": "2026-08-10T00:00:00Z",
                "equipped_count": 1,
            }

        def list_inventory_items(self, _snapshot_id, *, equipped=None):
            return [dict(module)]

        def list_loadout_plans(self):
            return []

        def list_characters(self):
            return [{"character_id": 1003, "name_zh": "角色一"}]

        def list_shapes(self):
            return [{
                "shape_id": "EquipmentGeometry_Hen2",
                "cells": [{"x": 0, "y": 0}, {"x": 0, "y": 1}],
            }]

        def get_equipment_plan(self, _character_id):
            return {
                "cells": [
                    {"row": 1, "column": 1},
                    {"row": 1, "column": 2},
                ],
            }

        def list_suits(self):
            return []

        def list_equipment_attributes(self):
            return []

    monkeypatch.setattr(equipment_display_loaders, "UserDataDao", Dao)
    monkeypatch.setattr(equipment_display_loaders, "StaticGameDataDao", Dao)
    monkeypatch.setattr(
        equipment_display_loaders,
        "_load_sqlite_equipment_display_states",
        lambda *_args, **_kwargs: {},
    )

    result = display_view._load_game_equipment_display_states(
        "user.sqlite3",
        static_database_path="static.sqlite3",
    )

    state = result["states"]["角色一"]
    assert state["_game_importable"] is True
    assert state["_game_status"] == "missing_tape"
    assert state["equipped_tape"] is None
    assert state["blueprint_layout"][0][:2] == ["H_2", "H_2"]
