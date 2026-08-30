# 验证争锋唯一命中后的默认挑战项仍可由用户修改。
from __future__ import annotations

import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.features.battle_report.target_condition_selector import (
    BattleTargetConditionSelector,
)
from src.services.battle_inferred_target_condition_service import (
    BattleInferredTargetConditionService,
)
from src.services.battle_target_catalog_service import BattleTargetCatalogService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


STATIC_DATABASE = Path("data/game_static.sqlite3")


class BattleInferredTargetConditionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_feast_defaults_restore_and_remain_editable(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={
                "hits": ({
                    "relative_time_us": 1,
                    "direction": "outgoing",
                    "target_id": "enemy-wire:feast",
                    "target_monster_id": "boss_05_BP_DiyBoss",
                    "target_max_hp": 6_099_744.0,
                },),
            },
            range_start_us=0,
            range_end_us=10,
        )
        assert inferred is not None
        assert inferred.target_condition is not None
        with StaticGameDataDao(STATIC_DATABASE) as dao:
            catalog = BattleTargetCatalogService.load(dao)

        selector = BattleTargetConditionSelector()
        selector.set_catalog(catalog)
        selector.render(inferred.target_condition)

        preset = selector.current_preset()
        self.assertEqual("DiyBossStage8", preset["environment_ref"])
        self.assertEqual("Attack003_challenge", preset["feast_options"]["2"])
        self.assertEqual("LightOP003_challenge", preset["feast_options"]["3"])
        self.assertEqual("HunOP003_challenge", preset["feast_options"]["4"])
        self.assertEqual("XiangOP003_challenge", preset["feast_options"]["5"])

        attack_combo = selector._feast_option_combos["2"]
        lower_index = next(
            index
            for index in range(attack_combo.count())
            if (attack_combo.itemData(index) or {}).get("option_id")
            == "Attack001_challenge"
        )
        attack_combo.setCurrentIndex(lower_index)

        self.assertEqual(
            "Attack001_challenge",
            selector.current_preset()["feast_options"]["2"],
        )

    def test_world_boss_inferred_level_restores_and_remains_editable(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={
                "hits": ({
                    "relative_time_us": 1,
                    "direction": "outgoing",
                    "target_id": "enemy-wire:black-book",
                    "target_monster_id": "boss_09_BP_WorldBoss",
                    "target_max_hp": 1_450_710.0,
                },),
            },
            range_start_us=0,
            range_end_us=10,
        )
        assert inferred is not None
        assert inferred.target_condition is not None
        with StaticGameDataDao(STATIC_DATABASE) as dao:
            catalog = BattleTargetCatalogService.load(dao)

        selector = BattleTargetConditionSelector()
        selector.set_catalog(catalog)
        selector.render(inferred.target_condition)

        self.assertEqual("黑之书", selector.current_preset()["target_name"])
        self.assertEqual(80.0, selector.current_preset()["enemy_level"])
        level_75 = next(
            index
            for index in range(selector.open_world_variant.count())
            if float(
                (selector.open_world_variant.itemData(index) or {}).get(
                    "monster_level",
                    0.0,
                )
            ) == 75.0
        )
        selector.open_world_variant.setCurrentIndex(level_75)

        self.assertEqual(75.0, selector.current_preset()["enemy_level"])


if __name__ == "__main__":
    unittest.main()
