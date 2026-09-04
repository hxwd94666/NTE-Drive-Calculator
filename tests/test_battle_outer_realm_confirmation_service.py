# 验证轨外手选任一半场会为完整上下半战报冻结整层目标画像。
from __future__ import annotations

from src.services.battle_outer_realm_confirmation_service import (
    complete_outer_realm_confirmation,
)


def _target(stage: str, ordinal: int, hp: float) -> dict:
    target_id = f"Abyss_9:10:{stage}:0:{ordinal}"
    return {
        "target_id": target_id,
        "name_zh": f"目标{ordinal}",
        "monster_class_path": f"/Game/Monster_{ordinal}",
        "monster_level": 90.0,
        "monster_count": 1,
        "profile": {
            "health_base": hp,
            "defense_base": 1000.0 + ordinal,
            "resistances": {"normal": 0.2},
        },
    }


def _catalog() -> dict:
    first = "EAbyssFightStage::FirstHalf"
    second = "EAbyssFightStage::SecondHalf"
    return {"outer_realm": [{
        "level_config_id": "Abyss_9",
        "levels": [{
            "level_id": 10,
            "halves": [
                {"stage": first, "targets": [_target(first, 0, 1000.0)]},
                {"stage": second, "targets": [_target(second, 1, 2000.0)]},
            ],
        }],
    }]}


def _condition() -> dict:
    first = "EAbyssFightStage::FirstHalf"
    upper = _target(first, 0, 1000.0)
    return {
        "environment_kind": "outer_realm",
        "environment_ref": f"Abyss_9|10|{first}",
        "environment_name": "轨外之境第10层上半",
        "selected_target_ids": (upper["target_id"],),
        "selected_target_profiles": [{
            "selection_target_id": upper["target_id"],
            "static_target_id": "frozen-upper",
        }],
        "primary_target_id": upper["target_id"],
    }


def _evidence(*halves: str) -> dict:
    return {"hits": [
        {"direction": "outgoing", "abyss_half": half}
        for half in halves
    ]}


def test_complete_report_confirms_both_halves_from_one_manual_half() -> None:
    completed = complete_outer_realm_confirmation(
        _condition(),
        _catalog(),
        _evidence("upper", "lower"),
    )

    assert completed["environment_ref"] == "Abyss_9|10|mixed"
    assert completed["environment_name"] == "轨外之境第10层上下半"
    assert len(completed["selected_target_ids"]) == 2
    assert {
        row["selection_target_id"]
        for row in completed["selected_target_profiles"]
    } == set(completed["selected_target_ids"])
    assert completed["selected_target_profiles"][0]["static_target_id"] == (
        "frozen-upper"
    )
    assert completed["primary_target_id"].endswith(":0:0")


def test_single_half_report_keeps_single_half_confirmation() -> None:
    original = _condition()

    completed = complete_outer_realm_confirmation(
        original,
        _catalog(),
        _evidence("upper"),
    )

    assert completed == original
