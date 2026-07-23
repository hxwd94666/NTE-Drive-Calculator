# 测试静态游戏数据库结构及其构建规则。
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools" / "game_data"
MODULE_PATH = TOOLS_DIR / "build_static_database.py"
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
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "012_game_static_graduation_template.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "013_game_static_setting_defaults.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "014_game_static_character_shape_bonus.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "015_game_static_logical_character_shape_bonus.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "016_game_static_fork_refinement_parameter.sql",
)
PROJECT_DATABASE_PATH = PROJECT_ROOT / "data" / "game_static.sqlite3"


def load_builder_module():
    sys.path.insert(0, str(TOOLS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("build_static_database", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(TOOLS_DIR))


class StaticGameDatabaseTests(unittest.TestCase):
    def test_legacy_calculation_catalog_uses_sqlite_not_role_or_set_json(self):
        """The old calculation UI may retain its solver, but not JSON static data."""
        from src.services.legacy_allocation_static_catalog import (
            build_legacy_allocation_static_catalog,
        )

        real_open = open

        def forbid_legacy_static_json(file, *args, **kwargs):
            normalized_path = str(file).replace("\\", "/").lower()
            if normalized_path.endswith(("/roles.json", "/sets.json")):
                raise AssertionError(f"calculation catalog attempted JSON access: {file}")
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=forbid_legacy_static_json):
            catalog = build_legacy_allocation_static_catalog(
                config_dir=PROJECT_ROOT / "config",
            )

        self.assertGreater(len(catalog.roles_db), 0)
        self.assertGreater(len(catalog.sets_db), 0)
        self.assertGreater(len(catalog.shapes_db), 0)

    def test_legacy_calculation_catalog_uses_account_shape_bonus_override(self):
        from src.services.character_weight_service import (
            save_account_character_shape_bonus,
        )
        from src.services.legacy_allocation_static_catalog import (
            build_legacy_allocation_static_catalog,
        )
        from src.storage.sqlite.user_data_dao import UserDataDao

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            with UserDataDao(database, account_id="shape-override"):
                pass
            save_account_character_shape_bonus(
                database,
                1051,
                shape_label="Type-4",
                property_values={"CritBase": 8.0},
            )
            catalog = build_legacy_allocation_static_catalog(
                config_dir=PROJECT_ROOT / "config",
                user_database_path=database,
            )

        self.assertEqual("Type-4", catalog.roles_db["「零」"]["extra_shape_label"])
        self.assertEqual(
            {"暴击率%": 8.0},
            catalog.roles_db["「零」"]["extra_shape_buffs"],
        )

    def test_weight_page_saves_shape_bonus_override_to_account_sqlite(self):
        from src.features.configuration import page as configuration_page
        from src.storage.sqlite.user_data_dao import UserDataDao

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            with UserDataDao(database, account_id="weight-page"):
                pass
            window = SimpleNamespace(
                _current_config_name="account_weights",
                _config_form_data={
                    "「零」": {
                        "character_id": 1051,
                        "weights": {"CritBase": 1.25},
                        "main_weights": {"CritBase": 0.75},
                        "extra_shape_label": "Type-2",
                        "extra_shape_buffs": {"AtkUp": 15.0},
                    }
                },
                _config_dirty=True,
                _config_dirty_character_ids={1051},
                _config_dirty_shape_bonus_ids={1051},
                reloaded=False,
            )
            window._load_data = lambda: setattr(window, "reloaded", True)
            with patch.object(
                configuration_page.runtime, "USER_DATABASE_PATH", database,
                create=True,
            ), patch.object(
                configuration_page.QMessageBox, "information",
            ), patch.object(configuration_page.QMessageBox, "warning"):
                configuration_page.save_config_form(window, PROJECT_ROOT / "config", None)
            with UserDataDao(database) as user_dao:
                weights = user_dao.get_character_weight_preferences(1051)
                shape_bonus = user_dao.get_character_shape_bonus_preferences(1051)

        self.assertTrue(window.reloaded)
        self.assertEqual({"CritBase": 1.25}, weights["property_weights"])
        self.assertEqual("Type-2", shape_bonus["shape_label"])
        self.assertEqual({"AtkUp": 15.0}, shape_bonus["property_values"])

    def test_checked_in_distribution_database_has_no_source_payloads(self):
        self.assertTrue(PROJECT_DATABASE_PATH.is_file())
        connection = sqlite3.connect(PROJECT_DATABASE_PATH)
        try:
            schema_version = connection.execute(
                "SELECT MAX(version) FROM schema_migration"
            ).fetchone()[0]
            payload_count = connection.execute(
                "SELECT COUNT(*) FROM source_row WHERE payload_json IS NOT NULL"
            ).fetchone()[0]
            character_count = connection.execute(
                "SELECT COUNT(*) FROM character"
            ).fetchone()[0]
            source_row_count = connection.execute(
                "SELECT COUNT(*) FROM source_row"
            ).fetchone()[0]
            source_hash_count = connection.execute(
                "SELECT COUNT(*) FROM source_row WHERE LENGTH(content_sha256) = 64"
            ).fetchone()[0]
            absolute_path_count = connection.execute(
                "SELECT COUNT(*) FROM source_file WHERE INSTR(relative_path, ':') > 0"
            ).fetchone()[0]
            graduation_count = connection.execute(
                "SELECT COUNT(*) FROM character_graduation_template"
            ).fetchone()[0]
            classified_role_template_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM character_annotation
                WHERE classification IN (
                    'available_character', 'scheduled_character', 'playable'
                )
                """
            ).fetchone()[0]
            default_avatar_template_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM character_annotation
                WHERE character_id = 1051
                  AND classification = 'available_avatar_variant'
                """
            ).fetchone()[0]
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            connection.close()

        self.assertEqual(0, payload_count)
        self.assertEqual(16, schema_version)
        self.assertGreater(character_count, 0)
        self.assertEqual(source_row_count, source_hash_count)
        # The role-template DAO adds official ID 1051 as the default avatar
        # when no account-specific avatar variant has been observed.
        self.assertEqual(
            classified_role_template_count + default_avatar_template_count,
            graduation_count,
        )
        self.assertEqual(0, absolute_path_count)
        self.assertEqual([], violations)

    def test_combat_transformations_do_not_get_independent_growth_or_skills(self):
        connection = sqlite3.connect(PROJECT_DATABASE_PATH)
        try:
            transformations = [
                row[0]
                for row in connection.execute(
                    "SELECT character_id FROM character_annotation "
                    "WHERE classification = 'combat_transformation'"
                )
            ]
            for table in (
                "character_awaken_effect",
                "character_panel_growth",
                "character_skill",
            ):
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE character_id IN "
                    f"({','.join('?' for _ in transformations)})",
                    transformations,
                ).fetchone()[0]
                self.assertEqual(0, count, table)
        finally:
            connection.close()

    def test_schema_can_be_created_with_foreign_keys_enabled(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        for schema_path in SCHEMA_PATHS:
            connection.executescript(schema_path.read_text(encoding="utf-8"))

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("equipment_suit_required_shape", tables)
        self.assertIn("equipment_plan", tables)
        self.assertIn("fork_item", tables)
        self.assertIn("character_awaken_effect", tables)
        self.assertIn("character_panel_growth", tables)
        self.assertIn("character_skill", tables)
        self.assertIn("skill_damage", tables)
        self.assertIn("enemy_combat_profile", tables)
        self.assertIn("monster_instance_profile", tables)
        self.assertIn("abyss_level_monster_spawn", tables)
        self.assertIn("character_weight_recommendation", tables)
        self.assertIn("character_weight_recommendation_property", tables)

    def test_schema_uses_source_shape_ids_without_legacy_aliases(self):
        schema = "\n".join(path.read_text(encoding="utf-8") for path in SCHEMA_PATHS)

        self.assertNotIn("legacy_shape_id", schema)
        self.assertIn("character_annotation", schema)
        self.assertIn("payload_json TEXT,", schema)
        self.assertNotIn("payload_json TEXT NOT NULL", schema)
        self.assertIn("DROP COLUMN game_version", schema)

    def test_plan_grid_discards_border_and_keeps_playable_anchor_cells(self):
        module = load_builder_module()
        grid = [
            "-1,-1,-1,-1,-1,-1,-1",
            "-1,0,0,0,0,0,-1",
            "-1,0,equipment_module_1,0,0,0,-1",
            "-1,0,0,0,0,0,-1",
            "-1,0,0,0,0,0,-1",
            "-1,0,0,0,0,0,-1",
            "-1,-1,-1,-1,-1,-1,-1",
        ]

        cells, anchors = module.parse_plan_grid(grid)

        self.assertEqual(25, len(cells))
        self.assertEqual([(2, 2, "equipment_module_1")], anchors)

    def test_numbered_source_rows_split_on_final_numeric_suffix(self):
        module = load_builder_module()

        self.assertEqual(
            ("ForkUpgradePack_special", 100),
            module.split_numbered_row("ForkUpgradePack_special_100"),
        )

    def test_builder_has_no_legacy_config_input_or_shape_mapping(self):
        source = MODULE_PATH.read_text(encoding="utf-8")

        self.assertNotIn("legacy-config-dir", source)
        self.assertNotIn("LEGACY_SHAPE_IDS", source)


if __name__ == "__main__":
    unittest.main()
