# 验证独立 reference 图鉴后续构建和晋升不再把甲硬币误写成方斯。
"""Release-independent reference progression currency contract tests."""

from __future__ import annotations

import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.game_data.reference_progression_currency import (
    validate_reference_progression_currency,
)
from tools.game_data.promote_static_release import (
    StaticReleasePromotionError,
    _validate_candidate_database,
)
from tools.game_data.static_database_build_support import IMPORTER_VERSION, SCHEMA_VERSION
from tools.game_data.build_reference_catalog import ReferenceCatalogBuilder


NTE_TEST_TIER = "core"


def _fixture() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript("""
        CREATE TABLE progression_item (item_id TEXT, name_zh TEXT);
        CREATE TABLE progression_item_alias (token TEXT, context TEXT, item_id TEXT);
        CREATE TABLE character_breakthrough_cost (item_id TEXT);
        CREATE TABLE character_exp_material_cost (cost_item_id TEXT);
        CREATE TABLE fork_exp_material_cost (cost_item_id TEXT);
        CREATE TABLE clone_activity_difficulty (clone_id TEXT, drop_id TEXT, stamina_cost INTEGER);
        CREATE TABLE clone_drop_projection_item (drop_id TEXT, item_id TEXT, quantity INTEGER);
        INSERT INTO progression_item VALUES ('Gold','甲硬币'),('Fons','方斯');
        INSERT INTO progression_item_alias VALUES ('gold','progression_cost','Gold');
        INSERT INTO character_breakthrough_cost VALUES ('Gold');
        INSERT INTO character_exp_material_cost VALUES ('Gold');
        INSERT INTO fork_exp_material_cost VALUES ('Gold');
        INSERT INTO clone_activity_difficulty VALUES ('Trailclone_gold','drop_GoldClone_lv1',40);
        INSERT INTO clone_drop_projection_item VALUES ('drop_GoldClone_lv1','Gold',40000);
        INSERT INTO clone_drop_projection_item VALUES ('drop_fons1','Fons',1);
    """)
    return connection


class ReferenceProgressionCurrencyContractTests(unittest.TestCase):
    def test_reference_builder_enforces_currency_contract(self) -> None:
        operations = (
            "_import_combat_context", "_import_enemy_combat_profiles",
            "_import_roguelike_modifiers", "_import_monster_instance_profiles",
            "_import_abyss_bindings", "_import_monster_catalog",
            "_import_equipment_effect_sources", "_import_encounter_catalogs",
            "_import_progression_catalog",
        )
        with closing(_fixture()) as connection, ExitStack() as stack:
            builder = object.__new__(ReferenceCatalogBuilder)
            builder.connection = connection
            for operation in operations:
                stack.enter_context(patch.object(builder, operation, return_value=None))
            connection.execute("UPDATE progression_item_alias SET item_id='Fons'")
            with self.assertRaisesRegex(ValueError, "progression_item_alias"):
                builder._import_reference_details()

    def test_valid_reference_currency_keeps_gold_and_fons_distinct(self) -> None:
        with closing(_fixture()) as connection:
            result = validate_reference_progression_currency(connection)
        self.assertEqual(1, result["paid_gold_stage_count"])
        self.assertEqual("Gold", result["cost_alias"])

    def test_reference_currency_regressions_are_rejected(self) -> None:
        cases = (
            ("UPDATE progression_item_alias SET item_id='Fons'", "progression_item_alias"),
            ("UPDATE character_breakthrough_cost SET item_id='Fons'", "character_breakthrough_cost"),
            ("UPDATE character_exp_material_cost SET cost_item_id='Fons'", "character_exp_material_cost"),
            ("UPDATE fork_exp_material_cost SET cost_item_id='Fons'", "fork_exp_material_cost"),
            ("UPDATE clone_drop_projection_item SET item_id='Fons' WHERE drop_id='drop_GoldClone_lv1'", "Trailclone_gold"),
            ("UPDATE clone_drop_projection_item SET item_id='Gold' WHERE drop_id='drop_fons1'", "drop_fons1"),
            ("UPDATE progression_item SET name_zh='方斯' WHERE item_id='Gold'", "progression_item"),
        )
        for sql, message in cases:
            with self.subTest(sql=sql), closing(_fixture()) as connection:
                connection.execute(sql)
                with self.assertRaisesRegex(ValueError, message):
                    validate_reference_progression_currency(connection)

    def test_promotion_preflight_rejects_reference_currency_regression(self) -> None:
        with TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            database = candidate / "game_static.sqlite3"
            with closing(sqlite3.connect(database)) as connection, closing(_fixture()) as valid:
                valid.backup(connection)
                connection.execute("UPDATE progression_item_alias SET item_id='Fons'")
                connection.commit()
            summary = {
                "dataset_id": "fixture", "schema_version": SCHEMA_VERSION,
                "importer_version": IMPORTER_VERSION, "catalog_scope": "reference",
            }
            config = {"dataset_id": "fixture", "official_content_root": str(candidate)}
            with (
                patch("tools.game_data.promote_static_release.load_local_config", return_value=config),
                patch("tools.game_data.promote_static_release._database_summary", return_value=summary),
            ):
                with self.assertRaisesRegex(StaticReleasePromotionError, "progression_item_alias"):
                    _validate_candidate_database(candidate, candidate / "local.paths.json", allow_size_warning=False)
