# 验证战报目标环境静态目录的结构化投影。

import unittest
from pathlib import Path

from src.services.battle_target_catalog_service import BattleTargetCatalogService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


class BattleTargetCatalogServiceTests(unittest.TestCase):
    def test_feast_extreme_options_and_witch_buff_are_calculation_ready(self):
        path = Path("data/game_static.sqlite3").resolve()
        with StaticGameDataDao(path) as dao:
            catalog = BattleTargetCatalogService.load(dao)

        stage = next(row for row in catalog["feast"] if row["stage_id"] == "DiyBossStage8")
        extreme = next(row for row in stage["difficulties"] if row["difficulty_id"] == 4)
        damage_buff = next(
            row for row in catalog["witch_buffs"]
            if row["property_id"] == "DamageUpGeneralBase"
        )

        self.assertEqual(1050.0, extreme["defense_base"])
        self.assertEqual(70.0, extreme["topple_limit"])
        self.assertEqual(0.2, extreme["resistances"]["psyche"]["resistance_base"])
        self.assertEqual(0.15, damage_buff["property_value"])
        self.assertEqual(2, len(catalog["outer_realm"]))
        self.assertEqual("炽火灼痕", catalog["outer_realm"][0]["season_buff"]["buff_name_zh"])
        self.assertEqual(2, len(catalog["outer_realm"][0]["season_buff"]["components"]))
        self.assertGreater(len(catalog["open_world"]), 0)

    def test_official_open_world_and_clone_categories_are_nested(self):
        path = Path("data/game_static.sqlite3").resolve()
        with StaticGameDataDao(path) as dao:
            catalog = BattleTargetCatalogService.load(dao)

        open_world = {
            row["category_id"]: row
            for row in catalog["open_world_categories"]
        }
        self.assertEqual(21, len(open_world["open_world:normal"]["targets"]))
        self.assertEqual(5, len(open_world["open_world:elite"]["targets"]))
        self.assertEqual(7, len(open_world["open_world:world_boss"]["targets"]))
        self.assertIn(
            "低语种",
            {
                row["name_zh"]
                for row in open_world["open_world:normal"]["targets"]
            },
        )

        clones = {
            row["name_zh"]: row
            for row in catalog["clone_categories"]
        }
        self.assertEqual(3, len(clones["经验及甲硬币"]["activities"]))
        self.assertEqual(5, len(clones["异能升级材料"]["activities"]))
        self.assertEqual(5, len(clones["弧盘突破材料"]["activities"]))
        self.assertEqual(6, len(clones["空幕"]["activities"]))
        self.assertEqual(3, len(clones["异象巡礼"]["activities"]))
        talent = next(
            row for row in clones["异能升级材料"]["activities"]
            if row["name_zh"] == "小心鸽子"
        )
        self.assertEqual(7, len(talent["difficulties"]))
        members = [
            member
            for activity in clones["异能升级材料"]["activities"]
            for difficulty in activity["difficulties"]
            for member in difficulty["spawn_members"]
        ]
        self.assertTrue(members)
        self.assertTrue(all(member.get("profile") for member in members))
        self.assertTrue(all(
            "normal" in member["profile"]["resistances"]
            for member in members
        ))
