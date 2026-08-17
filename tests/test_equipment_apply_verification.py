# 测试装备装配结果复核。
"""Verify complete and residual equipment-event comparison semantics."""

from __future__ import annotations

from src.services.equipment_apply_verification import (
    plan_mismatch,
    scoped_plan_mismatch,
)


def _item(
    serial: int,
    *,
    character_id: int,
    character_uid: dict[str, int],
    row: int,
    column: int,
) -> dict:
    return {
        "uid_serial": serial,
        "uid_slot": 8,
        "equipped": True,
        "equipped_character_id": character_id,
        "equipped_character_uid": character_uid,
        "equipped_placement": {"row": row, "column": column},
    }


def test_scoped_verification_ignores_other_role_rows_but_checks_target_item() -> None:
    target_uid = {"slot": 700, "serial": 701}
    modules = [{"uid_serial": 1, "uid_slot": 8, "target_row": 1, "target_column": 2}]
    items = [
        _item(1, character_id=1003, character_uid=target_uid, row=1, column=2),
        _item(2, character_id=1004, character_uid={"slot": 702, "serial": 703}, row=3, column=4),
    ]

    assert scoped_plan_mismatch(
        items=items,
        modules=modules,
        core_assignment=None,
        character_id=1003,
        character_uid=target_uid,
    ) is None
    assert plan_mismatch(
        items=items,
        modules=modules,
        core_assignment=None,
        character_id=1003,
        character_uid=target_uid,
    ) is None


def test_scoped_verification_only_checks_that_target_item_is_equipped() -> None:
    target_uid = {"slot": 700, "serial": 701}
    modules = [{"uid_serial": 1, "uid_slot": 8, "target_row": 1, "target_column": 2}]

    assert scoped_plan_mismatch(
        items=[_item(1, character_id=1004, character_uid=target_uid, row=1, column=2)],
        modules=modules,
        core_assignment=None,
        character_id=1003,
        character_uid=target_uid,
    ) is None


def test_scoped_verification_accepts_residual_equipment_without_placement() -> None:
    target_uid = {"slot": 700, "serial": 701}
    modules = [{"uid_serial": 1, "uid_slot": 8, "target_row": 1, "target_column": 2}]
    residual = _item(1, character_id=1003, character_uid=target_uid, row=1, column=2)
    residual["equipped_placement"] = None

    assert scoped_plan_mismatch(
        items=[residual],
        modules=modules,
        core_assignment=None,
        character_id=1003,
        character_uid=target_uid,
    ) is None


def test_complete_verification_can_ignore_module_grid_position() -> None:
    target_uid = {"slot": 700, "serial": 701}
    modules = [{"uid_serial": 1, "uid_slot": 8, "target_row": 1, "target_column": 2}]

    assert plan_mismatch(
        items=[_item(1, character_id=1003, character_uid=target_uid, row=5, column=5)],
        modules=modules,
        core_assignment=None,
        character_id=1003,
        character_uid=target_uid,
        ignore_module_placement=True,
    ) is None


def test_scoped_verification_retries_only_for_a_seen_unequipped_target_item() -> None:
    target_uid = {"slot": 700, "serial": 701}
    modules = [
        {"uid_serial": 1, "uid_slot": 8, "target_row": 1, "target_column": 2},
        {"uid_serial": 2, "uid_slot": 8, "target_row": 2, "target_column": 2},
    ]
    seen_equipped = _item(1, character_id=1003, character_uid=target_uid, row=1, column=2)
    seen_unequipped = dict(seen_equipped)
    seen_unequipped["equipped"] = False

    assert scoped_plan_mismatch(
        items=[seen_equipped], modules=modules, core_assignment=None,
        character_id=1003, character_uid=target_uid,
    ) is None
    assert scoped_plan_mismatch(
        items=[seen_unequipped], modules=modules, core_assignment=None,
        character_id=1003, character_uid=target_uid,
    ) == "装备 UID (1, 8) 未装备"
