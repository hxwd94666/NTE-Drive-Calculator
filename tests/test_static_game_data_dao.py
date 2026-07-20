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
        connection.commit()
        connection.close()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_summary_and_read_only_connection(self):
        with StaticGameDataDao(self.database_path) as dao:
            summary = dao.summary()
            self.assertEqual(summary["schema_version"], 3)
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
            role_templates = dao.list_role_template_characters()
            fork_templates = dao.list_fork_templates()
        self.assertEqual(character["classification"], "playable")
        self.assertEqual(plan["core_item_id"], "Core1")
        self.assertEqual(plan["module_item_ids"], ["Module1"])
        self.assertEqual(forks[0]["exclusive_character_ids"], [1001])
        self.assertEqual(role_templates[0]["character_id"], 1001)
        self.assertEqual(fork_templates[0]["fork_id"], "fork_Test")
        self.assertEqual(fork_templates[0]["upgrade_levels"], [])

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
