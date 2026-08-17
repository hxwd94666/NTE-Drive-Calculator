# 测试配装方案总评等级。
"""Public behavior for complete-loadout grades versus single-item grades."""

from __future__ import annotations

from src.domain.allocation_rating import allocation_grade, loadout_total_grade
from src.features.inventory.equipment_loadout_scoring import (
    score_equipment_display_state,
)
from src.optimizer.contracts import (
    EQUIP_GRADE,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_UID,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_TOTAL_GRADE,
)


class _FixedPresentation:
    @staticmethod
    def score_tape(_main, sub_stats, _weights, _quality, _main_weights):
        return float(sub_stats["test_score"])

    @staticmethod
    def score_drive(sub_stats, _shape_id, _weights, _quality):
        return float(sub_stats["test_score"])

    @staticmethod
    def shape_area(_shape_id, _default):
        return 20


def _slot_state(slot_id: int, tape_score: float, drive_score: float) -> dict:
    return {
        "_loadout_slot_id": slot_id,
        ROLE_EQUIPPED_TAPE: {
            EQUIP_UID: f"tape-{slot_id}",
            EQUIP_MAIN_STATS: "测试主词条",
            EQUIP_SUB_STATS: {"test_score": tape_score},
            EQUIP_QUALITY: "Gold",
        },
        ROLE_EQUIPPED_DRIVES: [{
            EQUIP_UID: f"drive-{slot_id}",
            EQUIP_SHAPE_ID: "TEST_SHAPE",
            EQUIP_SUB_STATS: {"test_score": drive_score},
            EQUIP_QUALITY: "Gold",
        }],
    }


def test_loadout_total_grade_uses_fixed_score_intervals() -> None:
    boundaries = (
        (159.999, "D"),
        (160, "C"),
        (179.999, "C"),
        (180, "B"),
        (199.999, "B"),
        (200, "A"),
        (219.999, "A"),
        (220, "S"),
        (239.999, "S"),
        (240, "SS"),
        (259.999, "SS"),
        (260, "SSS"),
        (279.999, "SSS"),
        (280, "ACE"),
    )

    assert [(score, loadout_total_grade(score)) for score, _grade in boundaries] == [
        (score, grade) for score, grade in boundaries
    ]


def test_custom_role_slots_use_total_intervals_without_changing_item_grades() -> None:
    presentation = _FixedPresentation()
    roles = {"自建角色": {"weights": {}, "main_weights": {}}}
    primary = _slot_state(101, 100, 120)
    alternate = _slot_state(102, 90, 110)

    score_equipment_display_state(presentation, "自建角色", primary, roles)
    score_equipment_display_state(presentation, "自建角色", alternate, roles)

    assert primary[ROLE_TOTAL_GRADE] == "S"
    assert alternate[ROLE_TOTAL_GRADE] == "A"
    assert primary[ROLE_EQUIPPED_TAPE][EQUIP_GRADE] == allocation_grade(100, 15)
    assert primary[ROLE_EQUIPPED_DRIVES][0][EQUIP_GRADE] == allocation_grade(120, 20)
