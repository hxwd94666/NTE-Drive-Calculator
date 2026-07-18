# 测试静态游戏数据库结构及其构建规则。
import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools" / "game_data"
MODULE_PATH = TOOLS_DIR / "build_static_database.py"
SCHEMA_PATH = (
    PROJECT_ROOT
    / "src"
    / "storage"
    / "sqlite"
    / "schema"
    / "002_game_static.sql"
)


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
    def test_schema_can_be_created_with_foreign_keys_enabled(self):
        connection = sqlite3.connect(":memory:")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("equipment_suit_required_shape", tables)
        self.assertIn("equipment_plan", tables)
        self.assertIn("fork_item", tables)

    def test_schema_uses_source_shape_ids_without_legacy_aliases(self):
        schema = SCHEMA_PATH.read_text(encoding="utf-8")

        self.assertNotIn("legacy_shape_id", schema)
        self.assertIn("character_annotation", schema)

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
