# 验证游戏导入配装到统一方案的投影。
"""Cover game-equipped snapshot projection and plan import."""

from src.services.game_loadout_projection_service import (
    GameLoadoutImportRequest,
    GameLoadoutProjectionService,
)


def _module(
    serial: int = 11,
    slot: int = 21,
    character_id: int = 1003,
) -> dict:
    return {
        "uid_serial": serial,
        "uid_slot": slot,
        "kind": "module",
        "item_id": "module-a",
        "geometry": "Hen2",
        "grid_count": 2,
        "equipped": True,
        "equipped_character_id": character_id,
        "main_stats": [],
        "sub_stats": [],
    }


def _core(
    serial: int = 12,
    slot: int = 22,
    character_id: int = 1003,
) -> dict:
    return {
        "uid_serial": serial,
        "uid_slot": slot,
        "kind": "core",
        "item_id": "core-a",
        "geometry": None,
        "equipped": True,
        "equipped_character_id": character_id,
        "main_stats": [],
        "sub_stats": [],
    }


class _UserDao:
    def __init__(self, *, source: str = "nte_core") -> None:
        self.source = source
        self.items = [_module(), _core()]
        self.saved = None
        self.saved_plans = []
        self.replace_calls = 0
        self.slot_save_calls = 0

    def current_inventory_snapshot_id(self):
        return 7

    def inventory_snapshot_summary(self, _snapshot_id):
        return {
            "source": self.source,
            "captured_at_utc": "2026-08-09T00:00:00Z",
            "equipped_count": len(self.items),
        }

    def list_inventory_items(
        self,
        _snapshot_id,
        *,
        equipped=None,
        character_id=None,
    ):
        rows = list(self.items)
        if equipped is not None:
            rows = [row for row in rows if bool(row["equipped"]) == equipped]
        if character_id is not None:
            rows = [
                row for row in rows
                if row["equipped_character_id"] == character_id
            ]
        return [dict(row) for row in rows]

    def list_loadout_plans(self):
        return []

    def replace_active_loadout_plans(self, plans):
        self.replace_calls += 1
        self.saved_plans = list(plans)
        self.saved = plans[0]
        return tuple(88 + index for index, _plan in enumerate(plans))

    def save_plans_to_slots(self, plans):
        self.slot_save_calls += 1
        self.saved_plans = list(plans)
        self.saved = plans[0]
        return tuple(188 + index for index, _plan in enumerate(plans))


class _StaticDao:
    def list_characters(self):
        return [
            {"character_id": 1003, "name_zh": "测试角色"},
            {"character_id": 1004, "name_zh": "测试角色二"},
        ]

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


def test_projects_game_equipment_into_canonical_importable_plan() -> None:
    service = GameLoadoutProjectionService(_UserDao(), _StaticDao())

    result = service.project_current()

    assert result.supported
    assert result.snapshot_id == 7
    assert result.equipped_item_count == 2
    assert len(result.roles) == 1
    role = result.roles[0]
    assert role.importable
    assert role.status == "ready"
    assert role.assignments[0]["target_row"] == 1
    assert role.assignments[0]["target_column"] == 1
    assert role.assignments[-1]["kind"] == "core"


def test_imports_projection_as_normal_active_loadout() -> None:
    user_dao = _UserDao()
    service = GameLoadoutProjectionService(user_dao, _StaticDao())
    role = service.project_current().roles[0]
    scores = {
        "nte-module-21-11": 20.0,
        "nte-core-22-12": 80.0,
    }

    plan_id = service.import_role(role, score=100.0, assignment_scores=scores)

    assert plan_id == 88
    assert user_dao.saved["name"] == "游戏内方案：测试角色"
    assert user_dao.saved["payload"]["source"] == "game_inventory"
    assert user_dao.saved["payload"]["assignment_scores"] == scores
    assert len(user_dao.saved["assignments"]) == 2


def test_imports_complete_drive_blueprint_when_only_tape_is_missing() -> None:
    user_dao = _UserDao()
    user_dao.items = [_module()]
    service = GameLoadoutProjectionService(user_dao, _StaticDao())

    role = service.project_current().roles[0]

    assert role.importable
    assert role.status == "missing_tape"
    assert "可先导入完整驱动图纸" in role.reason
    assert [assignment["kind"] for assignment in role.assignments] == [
        "module"
    ]

    plan_id = service.import_role(
        role,
        score=20.0,
        assignment_scores={"nte-module-21-11": 20.0},
    )

    assert plan_id == 88
    assert user_dao.saved["status"] == "incomplete"
    assert user_dao.saved["payload"]["missing_tape"] is True
    assert len(user_dao.saved["assignments"]) == 1


def test_imports_multiple_game_loadouts_in_one_transaction() -> None:
    user_dao = _UserDao()
    user_dao.items.extend([
        _module(13, 23, 1004),
        _core(14, 24, 1004),
    ])
    service = GameLoadoutProjectionService(user_dao, _StaticDao())
    roles = service.project_current().roles
    requests = [
        GameLoadoutImportRequest(
            projection=role,
            score=100.0,
            assignment_scores={
                f"nte-module-{23 if role.character_id == 1004 else 21}-"
                f"{13 if role.character_id == 1004 else 11}": 20.0,
                f"nte-core-{24 if role.character_id == 1004 else 22}-"
                f"{14 if role.character_id == 1004 else 12}": 80.0,
            },
        )
        for role in roles
    ]

    plan_ids = service.import_roles(requests)

    assert plan_ids == (88, 89)
    assert user_dao.replace_calls == 1
    assert len(user_dao.saved_plans) == 2


def test_imports_game_loadout_into_explicit_named_slot() -> None:
    user_dao = _UserDao()
    service = GameLoadoutProjectionService(user_dao, _StaticDao())
    role = service.project_current().roles[0]

    plan_id = service.import_role(
        role,
        score=100.0,
        assignment_scores={"nte-module-21-11": 20.0, "nte-core-22-12": 80.0},
        slot_id=71,
    )

    assert plan_id == 188
    assert user_dao.slot_save_calls == 1
    assert user_dao.replace_calls == 0
    assert user_dao.saved["slot_id"] == 71


def test_rejects_non_nte_core_snapshot_for_game_mode() -> None:
    result = GameLoadoutProjectionService(
        _UserDao(source="gamepad"),
        _StaticDao(),
    ).project_current()

    assert not result.supported
    assert not result.roles
    assert "nte-core" in result.message
