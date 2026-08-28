# 测试静态游戏数据库结构及其构建规则。
import importlib.util
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


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
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "017_game_static_combat_catalog.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "018_game_static_character_likeability.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "019_game_static_combat_blueprint.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "020_game_static_buff_definition.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "021_game_static_buff_modifier_scope.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "022_game_static_encounter_catalog.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "023_game_static_encounter_activity.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "024_game_static_encounter_rotation.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "025_game_static_encounter_lookup_indexes.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "026_game_static_outer_realm_buff.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "027_game_static_abyss_monster_name.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "028_game_static_high_risk_commission.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "029_game_static_boss_support.sql",
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
    def test_skill_damage_owner_comes_from_formal_ability_binding(self):
        with StaticGameDataDao(PROJECT_DATABASE_PATH) as static_dao:
            self.assertEqual(
                [1071],
                static_dao.list_skill_damage_owner_character_ids(
                    "GE_Player_Chaos_Melee1_Damage"
                ),
            )

    def test_checked_in_zankou_dot_uses_imported_gameplay_tag(self):
        with StaticGameDataDao(PROJECT_DATABASE_PATH) as static_dao:
            self.assertTrue(static_dao.gameplay_effect_has_tag(
                "GE_Player_Zankou_DotDamage",
                "State.Damage.Dot",
            ))
            self.assertIn(
                "State.Damage.Dot",
                static_dao.list_gameplay_effect_tags((
                    "GE_Player_Mismo_UltraSkill_Damage",
                ))["GE_Player_Mismo_UltraSkill_Damage"],
            )

    def test_rebuild_backs_up_the_existing_release_database_first(self):
        module = load_builder_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "data" / "game_static.sqlite3"
            backup = root / "build" / "previous" / "data" / output.name
            output.parent.mkdir(parents=True)
            with closing(sqlite3.connect(output)) as connection:
                connection.execute(
                    "CREATE TABLE character_weight_recommendation(source_kind TEXT)"
                )
                connection.execute(
                    "INSERT INTO character_weight_recommendation VALUES ('workshop_api')"
                )
                connection.commit()

            module.backup_existing_release_database(output, backup)

            with closing(sqlite3.connect(backup)) as connection:
                source_kind = connection.execute(
                    "SELECT source_kind FROM character_weight_recommendation"
                ).fetchone()[0]
            self.assertEqual("workshop_api", source_kind)

    def test_unsynchronized_rebuild_does_not_overwrite_valid_release_backup(self):
        module = load_builder_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "data" / "game_static.sqlite3"
            backup = root / "build" / "previous" / "data" / output.name
            output.parent.mkdir(parents=True)
            backup.parent.mkdir(parents=True)
            backup.write_bytes(b"valid-previous-release")
            with closing(sqlite3.connect(output)) as connection:
                connection.execute(
                    "CREATE TABLE character_weight_recommendation(source_kind TEXT)"
                )
                connection.execute(
                    "INSERT INTO character_weight_recommendation VALUES ('default')"
                )
                connection.commit()

            module.backup_existing_release_database(output, backup)

            self.assertEqual(b"valid-previous-release", backup.read_bytes())

    def test_new_role_likeability_bonuses_follow_actor_identity(self):
        connection = sqlite3.connect(PROJECT_DATABASE_PATH)
        try:
            rows = connection.execute(
                """
                SELECT bonus.character_id, property.property_id, property.value
                FROM character_likeability_bonus AS bonus
                JOIN character_likeability_bonus_property AS property
                  USING (character_id)
                WHERE bonus.character_id IN (1046, 1051, 1052, 1054, 1055)
                ORDER BY bonus.character_id, property.ordinal
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            [
                (1052, "CritBase", 0.04),
                (1054, "AtkUp", 0.05),
                (1055, "AtkUp", 0.05),
            ],
            rows,
        )

    def test_lacrimosa_nightmare_applications_use_official_static_curves(self):
        connection = sqlite3.connect(PROJECT_DATABASE_PATH)
        try:
            rows = connection.execute(
                """
                SELECT curve_id, GROUP_CONCAT(value, ',')
                FROM combat_curve
                JOIN combat_curve_point USING (curve_table_asset_path, curve_id)
                WHERE curve_table_asset_path = '/Game/DataTable/Skill/'
                    || 'GlobalCharacterData/DT_GlobalValueLacrimosaData'
                  AND curve_id IN (
                    'Lacrimosa_Skilldotnum_1',
                    'Lacrimosa_UltraSkilldotnum_1'
                  )
                GROUP BY curve_id
                ORDER BY curve_id
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            [
                ("Lacrimosa_Skilldotnum_1", "5.0,5.0"),
                ("Lacrimosa_UltraSkilldotnum_1", "5.0,5.0"),
            ],
            rows,
        )

    def test_lingke_uses_nature_ordered_fallback_and_official_first_fork(self):
        connection = sqlite3.connect(PROJECT_DATABASE_PATH)
        try:
            role = connection.execute(
                """
                SELECT character.element_type, character.group_type,
                       recommendation.source_kind, recommendation.source_name,
                       template.fork_id
                FROM character
                JOIN character_weight_recommendation AS recommendation
                  USING (character_id)
                JOIN character_graduation_template AS template USING (character_id)
                WHERE character.character_id = 1072
                """
            ).fetchone()
            weights = connection.execute(
                """
                SELECT property_id, weight
                FROM character_weight_recommendation_property
                WHERE character_id = 1072 ORDER BY ordinal
                """
            ).fetchall()
            forks = connection.execute(
                """
                SELECT fork_id
                FROM character_cultivation_fork_recommendation
                WHERE character_id = 1072 ORDER BY ordinal
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            (
                "ECharacterElementType::CHARACTER_ELEMENT_TYPE_NATURE",
                "ECharacterGroupType::CHARACTER_GROUP_TYPE_THREE",
                "default",
                "preimplementation_workshop_fallback",
                "fork_GoldRecord",
            ),
            role,
        )
        self.assertEqual(
            [
                ("DamageUpNatureBase", 1.25),
                ("CritBase", 1.0),
                ("CritDamageBase", 0.9),
                ("AtkUp", 0.4),
                ("DamageUpGeneralBase", 0.9),
            ],
            weights,
        )
        self.assertEqual([("fork_GoldRecord",), ("fork_oulaquantao",)], forks)

    def test_worldrain_has_all_five_refinement_descriptions(self):
        connection = sqlite3.connect(PROJECT_DATABASE_PATH)
        try:
            row = connection.execute(
                """
                SELECT item.star_pack_id, item.max_star,
                       COUNT(level.star_level),
                       SUM(level.description_zh IS NOT NULL)
                FROM fork_item AS item
                JOIN fork_star_level AS level
                  ON level.star_pack_id = item.star_pack_id
                WHERE item.fork_id = 'fork_worldrain'
                GROUP BY item.fork_id
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(
            ("upgradestar_pack_fork_Worldrain", 5, 5, 5),
            row,
        )

    def test_official_slot_relations_import_character_shape_bonus(self):
        with tempfile.TemporaryDirectory() as directory:
            rebuilt_database = Path(directory) / "rebuilt.sqlite3"
            shutil.copy2(PROJECT_DATABASE_PATH, rebuilt_database)
            connection = sqlite3.connect(rebuilt_database)
            try:
                connection.execute("DELETE FROM logical_character_shape_bonus_property")
                connection.execute("DELETE FROM logical_character_shape_bonus")
                module = load_builder_module()
                builder = module.StaticDatabaseBuilder.__new__(module.StaticDatabaseBuilder)
                builder.connection = connection
                builder.rows = {
                    "character": {
                        "1075": {"ElementData": {
                            "EquipmentSlotID": "EquipmentSlots_oneiroi",
                        }},
                    },
                    "character_equipment_slots": {
                        "EquipmentSlots_oneiroi": {
                            "OwnerGridCount": 3,
                            "ModifyPropID": "SlotsEffect_oneiroi",
                        },
                    },
                    "equipment_slot_modify": {
                        "SlotsEffect_oneiroi": {
                            "ConditionArray": [],
                            "ModifyData": [{
                                "PropName": "AtkUp",
                                "PropValue": 0.1,
                                "ModifierOp": "EModifyModOp::MODIFY_MODOP_ADDITIVE",
                            }],
                        },
                    },
                }
                builder._import_official_character_shape_bonuses()
                oneiroi = connection.execute(
                    """SELECT b.shape_label, b.shape_grid_count,
                              p.property_id, p.display_value, b.source_kind
                       FROM logical_character_shape_bonus AS b
                       JOIN logical_character_shape_bonus_property AS p
                         USING (logical_character_key)
                       WHERE b.logical_character_key = 'character:1075'"""
                ).fetchone()
                violations = connection.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
            finally:
                connection.close()

        self.assertEqual(
            ("Type-3", 3, "AtkUp", 10.0, "official_role_profile"),
            oneiroi,
        )
        self.assertEqual([], violations)

    def test_split_character_importer_uses_public_show_time_helper(self):
        from tools.game_data.static_database_character_imports import (
            _player_variant_id,
        )

        module = load_builder_module()

        self.assertTrue(callable(module.show_time))
        self.assertIn("show_time", module.CharacterImportMixin._import_characters.__code__.co_names)
        self.assertIsNone(_player_variant_id({"AssetPathName": "None"}))

    def test_legacy_calculation_catalog_uses_sqlite_not_legacy_set_json(self):
        """The old calculation UI may retain its solver, but not JSON static data."""
        from src.services.legacy_allocation_static_catalog import (
            build_legacy_allocation_static_catalog,
        )

        real_open = open

        def forbid_legacy_static_json(file, *args, **kwargs):
            normalized_path = str(file).replace("\\", "/").lower()
            if normalized_path.endswith("/sets.json"):
                raise AssertionError(f"calculation catalog attempted JSON access: {file}")
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=forbid_legacy_static_json):
            catalog = build_legacy_allocation_static_catalog(
                config_dir=PROJECT_ROOT / "config",
            )

        self.assertGreater(len(catalog.roles_db), 0)
        self.assertGreater(len(catalog.sets_db), 0)
        self.assertGreater(len(catalog.shapes_db), 0)

    def test_legacy_calculation_catalog_includes_custom_role_for_step_two(self):
        from src.services.custom_character_service import (
            create_custom_character,
            save_custom_character_target_suit,
        )
        from src.services.legacy_allocation_static_catalog import (
            build_legacy_allocation_static_catalog,
        )
        from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
        from src.storage.sqlite.user_data_dao import UserDataDao

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            with UserDataDao(database, account_id="legacy-custom"):
                pass
            custom = create_custom_character(database, "第二步自建角色")
            with StaticGameDataDao(PROJECT_DATABASE_PATH) as static_dao:
                target_suit = next(
                    suit for suit in static_dao.list_suits()
                    if suit.get("required_shape_ids")
                )
            save_custom_character_target_suit(
                database,
                int(custom["character_id"]),
                str(target_suit["suit_id"]),
            )
            catalog = build_legacy_allocation_static_catalog(
                config_dir=PROJECT_ROOT / "config",
                user_database_path=database,
            )

        role = catalog.roles_db["第二步自建角色"]
        self.assertTrue(role["is_custom"])
        self.assertEqual(target_suit["name_zh"], role["default_set"])
        self.assertEqual(20, sum(cell == 0 for row in catalog.board_matrices["第二步自建角色"] for cell in row))

    def test_legacy_calculation_catalog_ignores_public_shape_override(self):
        from src.services.legacy_allocation_static_catalog import (
            build_legacy_allocation_static_catalog,
        )
        from src.storage.sqlite.shared_data_dao import SharedDataDao
        from src.storage.sqlite.user_data_dao import UserDataDao

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            static_database = Path(directory) / "game_static.sqlite3"
            shared_database = Path(directory) / "app_shared.sqlite3"
            shutil.copy2(PROJECT_DATABASE_PATH, static_database)
            with UserDataDao(database, account_id="shape-override"):
                pass
            with patch.dict(
                "os.environ",
                {
                    "NTE_GAME_STATIC_DB": str(static_database),
                    "NTE_APP_SHARED_DB": str(shared_database),
                },
            ):
                with StaticGameDataDao(static_database) as static_dao:
                    expected = static_dao.get_character_shape_bonus(1051)
                    logical_key = static_dao.get_logical_character_key(1051)
                with SharedDataDao(shared_database) as shared_dao:
                    shared_dao.upsert_shape_bonus_override(
                        logical_key,
                        representative_character_id=1051,
                        shape_label="Type-4",
                        shape_grid_count=4,
                        properties=[{
                            "property_id": "CritBase",
                            "display_value": 8.0,
                        }],
                        based_on_dataset_id="legacy-fixture",
                    )
                catalog = build_legacy_allocation_static_catalog(
                    config_dir=PROJECT_ROOT / "config",
                    user_database_path=database,
                )

        self.assertEqual(expected["shape_label"], catalog.roles_db["「零」"]["extra_shape_label"])
        self.assertNotEqual(
            {"暴击率%": 8.0}, catalog.roles_db["「零」"]["extra_shape_buffs"]
        )

    def test_weight_page_rejects_official_shape_bonus_edit(self):
        from src.features.configuration import page as configuration_page
        from src.services.character_shape_bonus_service import get_effective_character_shape_bonus
        from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
        from src.storage.sqlite.user_data_dao import UserDataDao

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            static_database = Path(directory) / "game_static.sqlite3"
            shared_database = Path(directory) / "app_shared.sqlite3"
            shutil.copy2(PROJECT_DATABASE_PATH, static_database)
            with UserDataDao(database, account_id="weight-page"):
                pass
            window = SimpleNamespace(
                app_context=SimpleNamespace(
                    account=SimpleNamespace(
                        active_account_id="weight-page",
                        user_database_path=database,
                    ),
                    generation=0,
                    paths=SimpleNamespace(
                        config_dir=PROJECT_ROOT / "config",
                        static_database_path=static_database,
                        shared_database_path=shared_database,
                    ),
                ),
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
            with patch.object(configuration_page.QMessageBox, "information"), \
                 patch.object(configuration_page.QMessageBox, "warning") as warning:
                configuration_page.save_config_form(window, PROJECT_ROOT / "config", None)
            with UserDataDao(database) as user_dao:
                weights = user_dao.get_character_weight_preferences(1051)
            with StaticGameDataDao(static_database) as static_dao:
                shape_bonus = get_effective_character_shape_bonus(
                    static_dao,
                    1051,
                    shared_database_path=shared_database,
                )

        self.assertFalse(window.reloaded)
        self.assertIsNone(weights)
        self.assertNotEqual("Type-2", shape_bonus["shape_label"])
        warning.assert_called_once()

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
        self.assertEqual(29, schema_version)
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

    def test_latest_outer_realm_buffs_keep_official_components(self):
        connection = sqlite3.connect(PROJECT_DATABASE_PATH)
        try:
            rows = connection.execute(
                """
                SELECT b.level_config_id, b.buff_name_zh, c.trigger_kind,
                       c.property_id, c.property_value, c.duration_seconds,
                       c.trigger_cooldown_seconds, c.stack_limit_count
                FROM outer_realm_season_buff AS b
                JOIN outer_realm_season_buff_component AS c USING (level_config_id)
                ORDER BY b.level_config_id, c.component_ordinal
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            [
                ("Abyss_8", "炽火灼痕", "corruption_damage_stack", "CritDamageBase", 0.06, 6.0, 1.0, 8),
                ("Abyss_8", "炽火灼痕", "while_target_toppled", "DamageUpGeneralBase", 0.25, None, None, 1),
                ("Abyss_9", "飘摇残响", "whole_battle", "DamageUpNatureBase", 0.45, None, None, 1),
                ("Abyss_9", "飘摇残响", "whole_battle", "DamageUpIncantationBase", 0.45, None, None, 1),
            ],
            rows,
        )

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
        self.assertIn("roguelike_modifier_profile", tables)
        self.assertIn("buff_definition", tables)
        self.assertIn("monster_instance_profile", tables)
        self.assertIn("abyss_level_monster_spawn", tables)
        self.assertIn("character_weight_recommendation", tables)
        self.assertIn("character_weight_recommendation_property", tables)
        self.assertIn("feast_stage", tables)
        self.assertIn("feast_option", tables)
        self.assertIn("divination_buff", tables)
        self.assertIn("clone_activity_category", tables)
        self.assertIn("clone_activity", tables)
        self.assertIn("clone_activity_difficulty", tables)
        self.assertIn("clone_spawn_member", tables)
        self.assertIn("monster_template_binding", tables)
        self.assertIn("outer_realm_rotation", tables)
        self.assertIn("high_risk_commission", tables)
        self.assertIn("high_risk_commission_difficulty", tables)
        self.assertIn("high_risk_monster_pool_member", tables)
        self.assertIn("monster_boss_support", tables)

    def test_checked_in_encounter_catalog_is_complete(self):
        connection = sqlite3.connect(PROJECT_DATABASE_PATH)
        try:
            counts = {
                table: connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                for table in (
                    "feast_stage",
                    "feast_stage_difficulty",
                    "feast_option",
                    "divination_buff",
                    "clone_activity_category",
                    "clone_activity",
                    "clone_activity_difficulty",
                    "clone_spawn_member",
                    "monster_template_binding",
                    "outer_realm_rotation",
                    "high_risk_commission",
                    "high_risk_commission_difficulty",
                    "high_risk_monster_pool_member",
                    "monster_boss_support",
                )
            }
        finally:
            connection.close()

        self.assertEqual(8, counts["feast_stage"])
        self.assertEqual(32, counts["feast_stage_difficulty"])
        self.assertEqual(54, counts["feast_option"])
        self.assertEqual(7, counts["divination_buff"])
        self.assertEqual(7, counts["clone_activity_category"])
        self.assertEqual(56, counts["clone_activity"])
        self.assertGreater(counts["clone_activity_difficulty"], 0)
        self.assertGreater(counts["clone_spawn_member"], 0)
        self.assertGreater(counts["monster_template_binding"], 0)
        self.assertGreater(counts["outer_realm_rotation"], 0)
        self.assertEqual(13, counts["high_risk_commission"])
        self.assertEqual(78, counts["high_risk_commission_difficulty"])
        self.assertEqual(55, counts["high_risk_monster_pool_member"])
        self.assertEqual(55, counts["monster_boss_support"])

        connection = sqlite3.connect(PROJECT_DATABASE_PATH)
        try:
            challenge_boss_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM monster_boss_support AS b
                JOIN source_row AS r USING (source_row_id)
                JOIN source_file AS f USING (source_file_id)
                WHERE b.monster_template_name =
                      'boss_07_ChallengeLv5_BP' COLLATE NOCASE
                  AND r.row_key = 'boss_07_ChallengeLv5_BP'
                  AND f.relative_path =
                      'DataTable/Monster/DT_BossSupportDataTable.json'
                """
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(1, challenge_boss_count)

        connection = sqlite3.connect(PROJECT_DATABASE_PATH)
        try:
            unlimited_count = connection.execute(
                "SELECT COUNT(*) FROM clone_activity_difficulty "
                "WHERE kill_monster_time_limit IS NULL"
            ).fetchone()[0]
            inference_rotation_count = connection.execute(
                "SELECT COUNT(*) FROM outer_realm_rotation "
                "WHERE inference_ordinal IS NOT NULL"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertGreater(unlimited_count, 0)
        self.assertEqual(2, inference_rotation_count)

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
