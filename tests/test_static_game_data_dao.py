# 测试静态游戏数据 DAO 的只读查询和运行时路径解析。
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV, StaticGameDataDao


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATHS = (
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "002_game_static.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "003_game_static_remove_game_version.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "004_game_static_character_awaken.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "005_game_static_character_growth.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "006_game_static_character_skills.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "007_game_static_skill_damage.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "008_game_static_combat_context.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "009_game_static_monster_binding.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "010_game_static_abyss_binding.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "011_game_static_recommended_weights.sql",
)


class StaticGameDataDaoTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "game_static.sqlite3"
        connection = sqlite3.connect(self.database_path)
        for schema_path in SCHEMA_PATHS:
            connection.executescript(schema_path.read_text(encoding="utf-8"))
        connection.execute("INSERT INTO schema_migration VALUES (2, '2026-07-18')")
        connection.execute("INSERT INTO schema_migration VALUES (3, '2026-07-18')")
        connection.execute("INSERT INTO schema_migration VALUES (4, '2026-07-21')")
        connection.execute("INSERT INTO schema_migration VALUES (5, '2026-07-21')")
        connection.execute("INSERT INTO schema_migration VALUES (6, '2026-07-21')")
        connection.execute("INSERT INTO schema_migration VALUES (7, '2026-07-21')")
        connection.execute("INSERT INTO schema_migration VALUES (8, '2026-07-22')")
        connection.execute("INSERT INTO schema_migration VALUES (9, '2026-07-22')")
        connection.execute("INSERT INTO schema_migration VALUES (10, '2026-07-22')")
        connection.execute("INSERT INTO schema_migration VALUES (11, '2026-07-22')")
        connection.execute(
            "INSERT INTO dataset VALUES ('fixture', 3, '2026-07-18')"
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
            """
            INSERT INTO character_awaken_effect VALUES (
                1001, 'resonance_3', 6, 'Awaken_Resonance', '三觉', NULL, NULL,
                '技能等级提升', NULL, NULL, NULL, '[{\"SkillName\":\"Skill1\",\"SkillLevel\":1}]',
                '[]', 1
            )
            """
        )
        connection.execute(
            "INSERT INTO character_awaken_skill_level_bonus VALUES "
            "(1001, 'resonance_3', 0, 'Skill1', 1)"
        )
        connection.execute(
            """
            INSERT INTO character_panel_growth VALUES (
                1001, 20, 1, 'breakthrough_after', 2000.0, 100.0, 80.0,
                1, 1, 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO character_skill VALUES (
                1001, 'Skill1', 'Proactive', 1, 1, 'Ability.Skill', NULL, 0, 1, 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO character_skill_level VALUES (
                1001, 'Skill1', 1, 2, 0, '[{\"ID\":\"gold\",\"Number\":2000}]'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO skill_damage VALUES (
                'Damage1', 'Skill1', 'NORMAL', 0.1, 0.2, 0.0, 'P', 0.5,
                '[1.0,1.1]', '[]', '[]', 1.0, 'Low', 0, 0.0, 1, 2.0, 0, 0.0, 1
            )
            """
        )
        connection.execute(
            "INSERT INTO skill_damage_modifier VALUES ('Damage1', 0.9, 1)"
        )
        connection.execute(
            "INSERT INTO combat_level_curve VALUES "
            "('topple:character_level', 'topple', NULL, NULL, 'RCIM_Constant', 'exact_level', 1)"
        )
        connection.execute(
            "INSERT INTO combat_level_curve_point VALUES "
            "('topple:character_level', 0, 80, NULL, 3603)"
        )
        connection.execute(
            "INSERT INTO combat_level_curve VALUES "
            "('reaction:GE_Test', 'reaction', 'REACTION_RESULT_TYPE_1', 'GE_Test', NULL, 'source_tier', 1)"
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
            "INSERT INTO character_weight_recommendation VALUES "
            "(1001, 'workshop_api', '1001', '角色', '2026-07-22')"
        )
        connection.execute(
            "INSERT INTO character_weight_recommendation_property VALUES "
            "(1001, 'Attr1', 0.75, 1.0, 0)"
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
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_summary_and_read_only_connection(self):
        with StaticGameDataDao(self.database_path) as dao:
            summary = dao.summary()
            self.assertEqual(summary["schema_version"], 11)
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

    def test_equipment_attributes_are_queryable_by_official_id(self):
        with StaticGameDataDao(self.database_path) as dao:
            attributes = dao.list_equipment_attributes()
            attribute = dao.get_equipment_attribute("Attr1")
            missing = dao.get_equipment_attribute("Unknown")
        self.assertEqual(["Attr1"], [row["attribute_id"] for row in attributes])
        self.assertEqual("Attr1", attribute["attribute_id"])
        self.assertFalse(attribute["show_percent"])
        self.assertIsNone(missing)

    def test_character_recommended_weights_use_official_property_ids(self):
        with StaticGameDataDao(self.database_path) as dao:
            recommendation = dao.get_character_recommended_weights(1001)
        self.assertEqual("workshop_api", recommendation["source_kind"])
        self.assertEqual({"Attr1": 0.75}, recommendation["property_weights"])
        self.assertEqual({"Attr1": 1.0}, recommendation["main_property_weights"])

    def test_characters_plans_and_forks_are_queryable(self):
        with StaticGameDataDao(self.database_path) as dao:
            character = dao.get_character(1001)
            plan = dao.get_equipment_plan(1001)
            default_suit = dao.get_character_default_suit(1001)
            forks = dao.list_forks()
            role_templates = dao.list_role_template_characters()
            fork_templates = dao.list_fork_templates()
        self.assertEqual(character["classification"], "playable")
        self.assertEqual(plan["core_item_id"], "Core1")
        self.assertEqual(plan["module_item_ids"], ["Module1"])
        self.assertEqual({"suit_id": "Suit1", "suit_name_zh": "官方数据空幕"}, default_suit)
        self.assertEqual(forks[0]["exclusive_character_ids"], [1001])
        self.assertEqual(role_templates[0]["character_id"], 1001)
        self.assertEqual(fork_templates[0]["fork_id"], "fork_Test")
        self.assertEqual(fork_templates[0]["upgrade_levels"], [])

    def test_character_awaken_effects_include_skill_level_bonuses(self):
        with StaticGameDataDao(self.database_path) as dao:
            effects = dao.list_character_awaken_effects(1001)
        self.assertEqual(effects[0]["effect_id"], "resonance_3")
        self.assertEqual(effects[0]["skill_level_bonuses"], [
            {"ordinal": 0, "skill_id": "Skill1", "level_delta": 1}
        ])

    def test_character_panel_growth_is_queryable_by_breakthrough_stage(self):
        with StaticGameDataDao(self.database_path) as dao:
            growth = dao.get_character_panel_growth(1001, 20, 1)
        self.assertEqual(growth["state"], "breakthrough_after")
        self.assertEqual(growth["atk_base"], 100.0)

    def test_character_skills_include_level_requirements_and_costs(self):
        with StaticGameDataDao(self.database_path) as dao:
            skills = dao.list_character_skills(1001)
        self.assertEqual(skills[0]["skill_id"], "Skill1")
        self.assertTrue(skills[0]["show_detail_info"])
        self.assertEqual(skills[0]["levels"], [{
            "level": 1,
            "required_breakthrough_stage": 2,
            "required_awaken_level": 0,
            "cost_items": [{"ID": "gold", "Number": 2000}],
        }])
        self.assertEqual(skills[0]["damage_entries"], [{
            "damage_id": "Damage1",
            "damage_type": "NORMAL",
            "charge_add": 0.1,
            "unbal_value": 0.2,
            "heterochrome_add": 0.0,
            "damage_source_category": "P",
            "fixed_crit_rate": 0.5,
            "atk_rate_base": [1.0, 1.1],
            "def_rate_base": [],
            "hp_rate_base": [],
            "story_balance_ge_rate": 1.0,
            "attack_break_level": "Low",
            "override_breakable_damage": False,
            "breakable_damage": 0.0,
            "override_breakable_impulse": True,
            "breakable_impulse": 2.0,
            "override_vehicle_breakable_impulse": False,
            "vehicle_breakable_impulse": 0.0,
            "source_row_id": 1,
            "modifier_atk_rate_base_coefficient": 0.9,
            "modifier_source_row_id": 1,
        }])

    def test_combat_context_and_abyss_bindings_are_queryable(self):
        with StaticGameDataDao(self.database_path) as dao:
            damage = dao.get_skill_damage("Damage1")
            reaction = dao.get_reaction_damage_curve("GE_Test")
            enemy = dao.get_enemy_combat_profile("night_999", "Boss1")
            level = dao.get_abyss_level_monsters("Abyss_Common", 1)

        self.assertEqual(damage["atk_rate_base"], [1.0, 1.1])
        self.assertEqual([point["value"] for point in reaction["points"]], [80, 120])
        self.assertEqual(enemy["resistances"]["chaos"]["resistance_base"], 0.2)
        self.assertEqual(level["spawns"][0]["attribute_pack_id"], "AbyssBoss1")
        self.assertEqual(level["spawns"][0]["monster_level"], 43)

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


if __name__ == "__main__":
    unittest.main()
