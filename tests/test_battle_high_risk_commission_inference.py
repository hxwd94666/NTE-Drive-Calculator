# 验证高危委托静态目录与受击 GE 身份补证。
from __future__ import annotations

import unittest
from pathlib import Path

from src.services.battle_inferred_target_condition_service import (
    BattleInferredTargetConditionService,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


STATIC_DATABASE = Path("data/game_static.sqlite3")


def _outgoing(max_hp: float, target_id: str = "enemy-wire:boss") -> dict:
    return {
        "relative_time_us": 1,
        "direction": "outgoing",
        "target_id": target_id,
        "target_monster_id": "",
        "target_max_hp": max_hp,
    }


def _incoming(effect_index: int, effect_name: str) -> dict:
    return {
        "relative_time_us": 2,
        "direction": "incoming",
        "gameplay_effect_index": effect_index,
        "gameplay_effect_name": effect_name,
    }


class BattleHighRiskCommissionInferenceTests(unittest.TestCase):
    def test_static_catalog_exposes_exact_high_risk_difficulties(self) -> None:
        with StaticGameDataDao(STATIC_DATABASE) as static_dao:
            rows = static_dao.list_high_risk_commission_fingerprint_rows()

        flower = [
            row for row in rows if row["commission_id"] == "AdvVision_flowerBoss"
        ]
        self.assertEqual([1, 2, 3, 4, 5, 6], [row["difficulty_id"] for row in flower])
        self.assertEqual(
            [3_232_360.0, 4_520_250.0],
            [row["health_base"] for row in flower[-2:]],
        )

    def test_single_target_incoming_ge_resolves_silent_garden_difficulty_6(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={
                "hits": (
                    _outgoing(4_520_250.0),
                    _incoming(23, "GE_Boss_07_act06_Dmg_BP"),
                )
            },
            range_start_us=None,
            range_end_us=None,
        )

        assert inferred is not None
        self.assertEqual("high_risk_commission", inferred.environment_kind)
        self.assertEqual(
            "adv_vision|AdvVision_flowerBoss|6", inferred.environment_ref
        )
        self.assertEqual("高危委托 · 「静默庭园」 · 难度 6", inferred.environment_name)
        self.assertEqual("高", inferred.confidence)
        self.assertIn("正式静态类路径一致", inferred.inference_basis)
        assert inferred.target_condition is not None
        self.assertEqual("open_world", inferred.target_condition.environment_kind)

    def test_incoming_ge_is_not_applied_to_multiple_observed_targets(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={
                "hits": (
                    _outgoing(4_520_250.0, "enemy-wire:1"),
                    _outgoing(4_520_250.0, "enemy-wire:2"),
                    _incoming(23, "GE_Boss_07_act06_Dmg_BP"),
                )
            },
            range_start_us=None,
            range_end_us=None,
        )

        self.assertIsNone(inferred)

    def test_hp_continuous_wire_switch_remains_one_high_risk_boss(self) -> None:
        first = _outgoing(4_040_458.0, "enemy-wire:primary")
        first.update({
            "target_hp_before": 1_700_000.0,
            "target_hp_after": 1_680_000.0,
            "total_damage": 20_000.0,
        })
        switched = _outgoing(4_040_458.0, "enemy-wire:temporary")
        switched.update({
            "relative_time_us": 2,
            "target_hp_before": 1_680_000.0,
            "target_hp_after": 1_679_000.0,
            "total_damage": 1_000.0,
        })
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={
                "hits": (
                    first,
                    switched,
                    _incoming(2195, "GE_Boss_017_act01_Dmg_06c_BP"),
                )
            },
            range_start_us=None,
            range_end_us=None,
        )

        assert inferred is not None
        self.assertEqual("adv_vision|AdvVision_Mammon|6", inferred.environment_ref)
        self.assertEqual("高危委托 · 「毛线球还是方斯？」 · 难度 6", inferred.environment_name)


if __name__ == "__main__":
    unittest.main()
