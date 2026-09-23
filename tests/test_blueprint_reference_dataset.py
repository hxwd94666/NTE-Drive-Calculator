# 验证角色图纸子页与计算使用相同的已选装备静态数据集。
"""Reference-dataset selection contract for blueprint generation."""

from pathlib import Path
from types import SimpleNamespace
import sqlite3
import unittest

from src.features.blueprints.dependencies import BlueprintDependencies


ROOT = Path(__file__).resolve().parents[1]


class BlueprintReferenceDatasetTests(unittest.TestCase):
    def test_blueprint_generation_uses_allocation_dataset(self) -> None:
        reference = ROOT / "data" / "role_catalog" / "game_static.sqlite3"
        legacy = ROOT / "data" / "game_static.sqlite3"
        app_context = SimpleNamespace(
            account=SimpleNamespace(
                active_account_id="fixture",
                user_database_path=ROOT / "accounts" / "fixture" / "user_data.sqlite3",
            ),
            generation=3,
            paths=SimpleNamespace(
                static_database_path=legacy,
                equipment_allocation_database_path=reference,
                shared_database_path=ROOT / "data" / "app_shared.sqlite3",
            ),
        )

        dependencies = BlueprintDependencies.from_app_context(app_context)

        self.assertEqual(reference, dependencies.static_database_path)
        self.assertNotEqual(legacy, dependencies.static_database_path)

    def test_reference_contains_new_role_blueprint_inputs(self) -> None:
        database = ROOT / "data" / "role_catalog" / "game_static.sqlite3"
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT c.name_zh, COUNT(p.character_id), COUNT(cell.character_id) "
                "FROM character c LEFT JOIN equipment_plan p USING(character_id) "
                "LEFT JOIN equipment_plan_cell cell USING(character_id) "
                "WHERE c.name_zh IN (?, ?) GROUP BY c.character_id",
                ("黑羽", "明音凛"),
            ).fetchall()

        self.assertEqual({"黑羽", "明音凛"}, {name for name, _, _ in rows})
        self.assertTrue(all(plans > 0 and cells > 0 for _, plans, cells in rows))
