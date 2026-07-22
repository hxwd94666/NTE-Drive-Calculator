# 测试静态游戏数据 DAO 的只读查询和运行时路径解析。
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV, StaticGameDataDao


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATHS = tuple(
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / filename
    for filename in (
        "002_game_static.sql", "003_game_static_combat.sql",
        "004_game_static_monster_binding.sql", "005_game_static_abyss_binding.sql",
    )
)


class StaticGameDataDaoTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "game_static.sqlite3"
        connection = sqlite3.connect(self.database_path)
        for schema_path in SCHEMA_PATHS:
            connection.executescript(schema_path.read_text(encoding="utf-8"))
        connection.executemany(
            "INSERT INTO schema_migration VALUES (?, '2026-07-22')", ((2,), (3,), (4,), (5,))
        )
        connection.execute(
            "INSERT INTO dataset VALUES ('fixture', 'test', 5, '2026-07-22')"
        )
        connection.execute(
            "INSERT INTO source_file VALUES (1, 'DataTable/Test.json', 'hash', 1)"
        )
        connection.execute(
            "INSERT INTO source_row VALUES (1, 1, 'row', ?, 'row-hash')",
            (json.dumps({"RawField": "RawValue"}),),
        )
        connection.execute(
            "INSERT INTO character VALUES (1001, '角色', NULL, NULL, 'Element', "
            "'Group', '/Game/Actor', NULL, 1)"
        )
        connection.execute(
            "INSERT INTO character_annotation VALUES "
            "(1001, 'character:1001', 1001, 'playable', 'fixture.json')"
        )
        connection.execute(
            "INSERT INTO equipment_attribute VALUES "
            "('Attr1', '属性', NULL, NULL, 'Type', 0, 1, 1, 1.0, NULL, 1)"
        )
        connection.execute(
            "INSERT INTO equipment_shape VALUES "
            "('EquipmentGeometry_ZhiJiao1', 3, 0, 0, 1)"
        )
        connection.executemany(
            "INSERT INTO equipment_shape_cell VALUES (?,?,?,?)",
            [
                ("EquipmentGeometry_ZhiJiao1", 0, 0, 0),
                ("EquipmentGeometry_ZhiJiao1", 1, 1, 0),
                ("EquipmentGeometry_ZhiJiao1", 2, 0, 1),
            ],
        )
        connection.execute(
            "INSERT INTO equipment_suit VALUES "
            "('Suit1', '官方数据空幕', NULL, NULL, NULL, 1)"
        )
        connection.execute(
            "INSERT INTO equipment_suit_required_shape VALUES "
            "('Suit1', 0, 'EquipmentGeometry_ZhiJiao1')"
        )
        connection.execute(
            "INSERT INTO equipment_suit_effect VALUES "
            "('Suit1', 2, 'Modify1', NULL, '效果', NULL, NULL, 1, 1)"
        )
        connection.execute(
            """
            INSERT INTO equipment_item(
                item_id, kind, quality, name_zh, geometry_id, geometry_enum,
                grid_count, suit_id, suit_type_enum, max_level,
                random_base_attribute_count, random_sub_attribute_count,
                random_sub_attribute_max_count, is_guide_item, source_row_id
            ) VALUES (
                'Core1', 'core', 'Gold', '官方数据空幕', NULL,
                'EEquipmentGeometryType::EquipmentGeometry_Core', NULL,
                'Suit1', 'EEquipmentSuitType::EquipmentSuitType_TaoZhuang1',
                20, 1, 4, 4, 0, 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO equipment_item(
                item_id, kind, quality, name_zh, geometry_id, geometry_enum,
                grid_count, max_level, random_base_attribute_count,
                random_sub_attribute_count, random_sub_attribute_max_count,
                is_guide_item, source_row_id
            ) VALUES (
                'Module1', 'module', 'Gold', '驱动',
                'EquipmentGeometry_ZhiJiao1',
                'EEquipmentGeometryType::EquipmentGeometry_ZhiJiao1',
                3, 20, 2, 4, 4, 0, 1
            )
            """
        )
        connection.execute(
            "INSERT INTO equipment_plan VALUES "
            "(1001, 'Core1', 20, 20, 100.0, NULL, NULL, 1)"
        )
        connection.execute(
            "INSERT INTO equipment_plan_core_attribute VALUES (1001, 0, 'Attr1')"
        )
        connection.execute(
            "INSERT INTO equipment_plan_recommended_attribute VALUES "
            "(1001, 0, 'Attr1')"
        )
        connection.execute(
            "INSERT INTO equipment_plan_cell VALUES (1001, 1, 1, 'Module1')"
        )
        connection.execute(
            "INSERT INTO equipment_plan_module VALUES (1001, 0, 'Module1')"
        )
        connection.execute(
            "INSERT INTO fork_type VALUES (1, '固态', NULL, NULL, 1)"
        )
        connection.execute(
            """
            INSERT INTO fork_item(
                fork_id, name_zh, quality, fork_type_id, raw_group_type,
                exclusive_character_ids_json, source_row_id
            ) VALUES (
                'fork_Test', '测试弧盘', 'Gold', 1,
                'ECharacterGroupType::CHARACTER_GROUP_TYPE_ONE', '[1001]', 1
            )
            """
        )
        connection.execute(
            "INSERT INTO combat_level_curve VALUES "
            "('topple:character_level', 'topple', NULL, NULL, 'RCIM_Constant', "
            "'exact_level', 1)"
        )
        connection.execute(
            "INSERT INTO combat_level_curve_point VALUES "
            "('topple:character_level', 0, 80, NULL, 3603)"
        )
        connection.execute(
            "INSERT INTO combat_level_curve VALUES "
            "('reaction:GE_Test', 'reaction', 'REACTION_RESULT_TYPE_1', "
            "'GE_Test', NULL, 'source_tier', 1)"
        )
        connection.executemany(
            "INSERT INTO combat_level_curve_point VALUES (?,?,?,?,?)",
            (("reaction:GE_Test", 0, None, 0, 80), ("reaction:GE_Test", 1, None, 1, 120)),
        )
        connection.execute(
            "INSERT INTO reaction_definition VALUES "
            "('REACTION_RESULT_TYPE_1', 'COSMOS', 'NATURE', 'GE_Test', 1)"
        )
        connection.execute(
            "INSERT INTO combat_effect_constant VALUES "
            "('Reaction_Test_BuffTime', 1, 12, 'seconds', '测试持续时间', 1)"
        )
        connection.execute(
            "INSERT INTO skill_damage VALUES "
            "('GE_Skill_Test', 'GA_Test', 'CHAOS', NULL, 0.5, 1, 2, 0, 1, 'BL_Low', 1)"
        )
        connection.executemany(
            "INSERT INTO skill_damage_rate VALUES (?,?,?,?)",
            (("GE_Skill_Test", "attack", 0, 1.25), ("GE_Skill_Test", "health", 0, 0.1)),
        )
        connection.execute(
            "INSERT INTO enemy_combat_profile VALUES "
            "('night_999', 'Boss1', 170, 0, 0, 0, 50, 1, 1, 0, 100, 200, 1)"
        )
        connection.execute(
            "INSERT INTO enemy_element_resistance VALUES "
            "('night_999', 'Boss1', 'chaos', 0.2, 0)"
        )
        connection.execute(
            "INSERT INTO enemy_combat_profile VALUES "
            "('standard', 'AbyssBoss1', 170, 0, 0, 0, 50, 1, 1, 0, 100, 200, 1)"
        )
        connection.execute(
            "INSERT INTO abyss_level VALUES ('Abyss_Common', 1, NULL, '始发站', 1)"
        )
        connection.execute(
            "INSERT INTO abyss_level_monster_spawn VALUES "
            "('Abyss_Common', 1, 'EAbyssFightStage::FirstHalf', 0, 1, "
            "'Pool1', 'EAbyssMonsterSpawnType::Spawn_KillAll', 0, 1)"
        )
        connection.execute(
            "INSERT INTO abyss_monster_pool_entry VALUES "
            "('Pool1', 0, '/Game/Monster/Boss.Boss_C', 2, 43, 'standard', "
            "'AbyssBoss1', 1, 1)"
        )
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_summary_and_read_only_connection(self):
        with StaticGameDataDao(self.database_path) as dao:
            summary = dao.summary()
            self.assertEqual(summary["schema_version"], 5)
            self.assertEqual(summary["counts"]["character"], 1)
            with self.assertRaises(sqlite3.OperationalError):
                dao._connection.execute("DELETE FROM character")

    def test_resolves_database_from_environment_for_packaged_runtime(self):
        with patch.dict("os.environ", {STATIC_DATABASE_ENV: str(self.database_path)}):
            with StaticGameDataDao() as dao:
                self.assertEqual(dao.database_path, self.database_path.resolve())

    def test_shapes_and_suits_keep_source_ids(self):
        with StaticGameDataDao(self.database_path) as dao:
            shapes = dao.list_shapes()
            suit = dao.get_suit("Suit1")
        self.assertEqual(shapes[0]["shape_id"], "EquipmentGeometry_ZhiJiao1")
        self.assertEqual(len(shapes[0]["cells"]), 3)
        self.assertEqual(
            suit["required_shape_ids"], ["EquipmentGeometry_ZhiJiao1"]
        )
        self.assertTrue(suit["effects"][0]["reapply_after_revive"])

    def test_characters_plans_and_forks_are_queryable(self):
        with StaticGameDataDao(self.database_path) as dao:
            character = dao.get_character(1001)
            plan = dao.get_equipment_plan(1001)
            forks = dao.list_forks()
        self.assertEqual(character["classification"], "playable")
        self.assertEqual(plan["core_item_id"], "Core1")
        self.assertEqual(plan["module_item_ids"], ["Module1"])
        self.assertEqual(forks[0]["exclusive_character_ids"], [1001])

    def test_raw_source_payload_is_available(self):
        with StaticGameDataDao(self.database_path) as dao:
            payload = dao.get_source_payload("DataTable/Test.json", "row")
        self.assertEqual(payload, {"RawField": "RawValue"})

    def test_distribution_database_can_omit_raw_source_payload(self):
        connection = sqlite3.connect(self.database_path)
        connection.execute("UPDATE source_row SET payload_json = NULL")
        connection.commit()
        connection.close()

        with StaticGameDataDao(self.database_path) as dao:
            payload = dao.get_source_payload("DataTable/Test.json", "row")
        self.assertIsNone(payload)

    def test_combat_curves_skills_and_enemy_profiles_are_queryable(self):
        with StaticGameDataDao(self.database_path) as dao:
            topple = dao.get_topple_level_multiplier(80)
            reaction = dao.get_reaction_damage_curve("GE_Test")
            skill = dao.get_skill_damage("GE_Skill_Test")
            enemy = dao.get_enemy_combat_profile("night_999", "Boss1")
            constants = dao.list_combat_effect_constants()

        self.assertEqual(topple, 3603)
        self.assertEqual(reaction["mapping_status"], "source_tier")
        self.assertEqual([point["value"] for point in reaction["points"]], [80, 120])
        self.assertEqual(skill["rates"]["attack"][0]["value"], 1.25)
        self.assertEqual(enemy["topple_limit"], 50)
        self.assertEqual(enemy["resistances"]["chaos"]["resistance_base"], 0.2)
        self.assertEqual(constants[0]["unit"], "seconds")

    def test_abyss_level_query_keeps_spawn_and_attribute_provenance(self):
        with StaticGameDataDao(self.database_path) as dao:
            level = dao.get_abyss_level_monsters("Abyss_Common", 1)

        self.assertEqual(level["name_zh"], "始发站")
        self.assertEqual(level["spawns"][0]["monster_pool_id"], "Pool1")
        self.assertEqual(level["spawns"][0]["monster_level"], 43)
        self.assertEqual(level["spawns"][0]["attribute_pack_id"], "AbyssBoss1")
        self.assertEqual(level["spawns"][0]["attribute_source_row_id"], 1)


if __name__ == "__main__":
    unittest.main()
