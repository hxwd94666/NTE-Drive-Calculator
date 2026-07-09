# 测试给扩展算法使用的稳定基础接口。
import json
import tempfile
import unittest
from pathlib import Path


class NavigationSupportTests(unittest.TestCase):
    def test_navigation_items_have_stable_keys_and_indexes(self):
        from src.ui.navigation import NAV_ITEMS, nav_index_map, nav_title_map

        keys = [item.key for item in NAV_ITEMS]

        self.assertEqual(["execute", "equipment", "my_role", "identify", "blueprint", "config", "settings"], keys)
        self.assertEqual(
            {"execute": 0, "equipment": 1, "my_role": 2, "identify": 3, "blueprint": 4, "config": 5, "settings": 6},
            nav_index_map(),
        )
        self.assertEqual("⚙  配置", nav_title_map()["config"])


class JsonStoreSupportTests(unittest.TestCase):
    def test_json_store_reads_defaults_and_writes_utf8(self):
        from src.storage.json_store import read_json, write_json

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "data.json"

            self.assertEqual({"默认": True}, read_json(path, default={"默认": True}))

            write_json(path, {"角色": ["澜"]})

            self.assertEqual({"角色": ["澜"]}, json.loads(path.read_text(encoding="utf-8")))
            self.assertIn("澜", path.read_text(encoding="utf-8"))

    def test_json_store_atomic_write_replaces_existing_file(self):
        from src.storage.json_store import read_json, write_json_atomic

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            path.write_text(json.dumps({"old": 1}), encoding="utf-8")

            write_json_atomic(path, {"new": 2})

            self.assertEqual({"new": 2}, read_json(path))
            self.assertFalse(path.with_suffix(".json.tmp").exists())


class ConfigMigrationSupportTests(unittest.TestCase):
    def test_merge_missing_config_data_preserves_existing_values_and_adds_missing_fields(self):
        from src.storage.config_migration import merge_missing_config_data

        current = {
            "roles": {
                "角色A": {
                    "weights": {"攻击力": 1.0},
                    "board_matrix": [[0]],
                }
            },
            "stats": ["攻击力"],
        }
        bundled = {
            "roles": {
                "角色A": {
                    "weights": {"攻击力": 2.0, "暴击率%": 0.5},
                    "extra_shape_buffs": {},
                },
                "角色B": {"weights": {}},
            },
            "stats": ["攻击力", "暴击率%"],
        }

        merged, changed = merge_missing_config_data(current, bundled)

        self.assertTrue(changed)
        self.assertEqual(1.0, merged["roles"]["角色A"]["weights"]["攻击力"])
        self.assertEqual(0.5, merged["roles"]["角色A"]["weights"]["暴击率%"])
        self.assertEqual({}, merged["roles"]["角色A"]["extra_shape_buffs"])
        self.assertIn("角色B", merged["roles"])
        self.assertEqual(["攻击力", "暴击率%"], merged["stats"])

    def test_account_seed_migrates_existing_core_config_without_overwriting_user_values(self):
        from src.features.accounts.manager import AccountManager

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "bundled"
            user_config = root / "config"
            bundled.mkdir()
            user_config.mkdir()
            (bundled / "stats.json").write_text(
                json.dumps(
                    {
                        "gold_base_values": {"攻击力": 8.0, "暴击率%": 1.0},
                        "stat_alias_mapping": {"爆击率": "暴击率%"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (user_config / "stats.json").write_text(
                json.dumps({"gold_base_values": {"攻击力": 9.0}}, ensure_ascii=False),
                encoding="utf-8",
            )

            manager = AccountManager(
                data_root=root,
                bundled_config_dir=bundled,
                iter_image_files=lambda _path: [],
                core_config_files=("stats.json",),
                account_user_files=(),
            )
            manager.seed_user_config()

            migrated = json.loads((user_config / "stats.json").read_text(encoding="utf-8"))
            self.assertEqual(9.0, migrated["gold_base_values"]["攻击力"])
            self.assertEqual(1.0, migrated["gold_base_values"]["暴击率%"])
            self.assertEqual({"爆击率": "暴击率%"}, migrated["stat_alias_mapping"])
            self.assertTrue((user_config / "stats.json.bak").exists())


class StatCatalogSupportTests(unittest.TestCase):
    def test_stat_catalog_normalizes_alias_and_common_ocr_typo(self):
        from src.domain.stat_catalog import StatCatalog

        catalog = StatCatalog.from_config_dir("config")

        self.assertEqual("暴击率%", catalog.normalize_stat_name("暴击率", is_percent=True))
        self.assertEqual("暴击率%", catalog.normalize_stat_name("爆击率", is_percent=True))
        self.assertIsNone(catalog.normalize_stat_name("内核占用", is_percent=False))

    def test_stat_catalog_exposes_main_and_sub_stat_pools(self):
        from src.domain.stat_catalog import StatCatalog

        catalog = StatCatalog.from_config_dir("config")

        self.assertIn("攻击力", catalog.valid_sub_stats)
        self.assertIn("攻击力%", catalog.valid_sub_stats)
        self.assertIn("暴击率", catalog.tape_main_stats)


class EquipmentNormalizerSupportTests(unittest.TestCase):
    def test_normalizer_fills_missing_drive_main_stats(self):
        from src.domain.equipment_normalizer import normalize_equipment_item

        item = {
            "uid": "drive_1",
            "item_type": "drive",
            "quality": "Purple",
            "area": 2,
            "shape_id": "DRIVE_2",
            "sub_stats": {"攻击力": 10.0},
        }

        normalized = normalize_equipment_item(item)

        self.assertEqual({"攻击力": 33.6, "生命值": 448.0}, normalized["main_stats"])
        self.assertEqual("未知套装", normalized["set_name"])

    def test_normalizer_keeps_tape_main_stat_as_text(self):
        from src.domain.equipment_normalizer import normalize_equipment_item

        item = {
            "uid": "tape_1",
            "item_type": "tape",
            "quality": "Gold",
            "area": 15,
            "set_name": "森林萤火之心",
            "main_stats": {"攻击力%": 37.5},
            "sub_stats": {},
        }

        normalized = normalize_equipment_item(item)

        self.assertEqual("攻击力%", normalized["main_stats"])
        self.assertEqual("TAPE_15", normalized["shape_id"])


class AllocationContractSupportTests(unittest.TestCase):
    def test_allocation_contracts_expose_strategy_modes_and_plan_keys(self):
        from src.optimizer.contracts import (
            STRATEGY_MODES,
            ALLOCATION_PLAN_KEYS,
            CandidatePool,
            AllocationPlan,
        )

        self.assertEqual(("role_priority", "drive_priority", "global_optimal"), STRATEGY_MODES)
        self.assertIn("assigned_set_drives", ALLOCATION_PLAN_KEYS)
        self.assertIn("assigned_extra_drives", ALLOCATION_PLAN_KEYS)

        candidate_pool: CandidatePool = {"drives": [], "tapes": {}}
        plan: AllocationPlan = {"valid": False}
        self.assertEqual([], candidate_pool["drives"])
        self.assertFalse(plan["valid"])

    def test_legacy_strategy_module_exports_split_strategy_classes(self):
        from src.optimizer import strategies
        from src.optimizer.drive_candidate_ranker import BaseDispatchStrategy
        from src.optimizer.role_priority_strategy import RolePriorityStrategy
        from src.optimizer.drive_priority_strategy import MatrixBaseStrategy

        self.assertIs(strategies.BaseDispatchStrategy, BaseDispatchStrategy)
        self.assertIs(strategies.RolePriorityStrategy, RolePriorityStrategy)
        self.assertIs(strategies.MatrixBaseStrategy, MatrixBaseStrategy)


class SetEffectModeSupportTests(unittest.TestCase):
    def test_set_effect_modes_choose_required_set_pieces(self):
        from src.solver.set_effects import set_piece_options_for_mode

        set_shapes = ["A", "B", "C", "D"]

        self.assertEqual([["A", "B", "C", "D"]], set_piece_options_for_mode(set_shapes, "four_piece"))
        self.assertEqual(
            [["A", "B"], ["A", "C"], ["A", "D"], ["B", "C"], ["B", "D"], ["C", "D"]],
            set_piece_options_for_mode(set_shapes, "two_piece"),
        )
        self.assertEqual([[]], set_piece_options_for_mode(set_shapes, "none"))
        self.assertEqual([["A", "B", "C", "D"]], set_piece_options_for_mode(set_shapes, "old_value"))

    def test_role_priority_uses_blueprint_set_pieces_instead_of_full_set_shapes(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        strategy = RolePriorityStrategy({"Role": {"default_set": "S"}}, {"S": {"shapes": ["A", "B", "C", "D"]}}, {})
        blueprint = {"set_pieces": ["A", "B"], "extra_pieces": ["X"], "board": []}
        available = [
            Drive(uid="a", quality="Gold", area=1, shape_id="A", main_stats={"x": 1, "y": 1}, role_scores={"Role": 1.0}),
            Drive(uid="b", quality="Gold", area=1, shape_id="B", main_stats={"x": 1, "y": 1}, role_scores={"Role": 1.0}),
            Drive(uid="x", quality="Gold", area=1, shape_id="X", main_stats={"x": 1, "y": 1}, role_scores={"Role": 1.0}),
        ]

        plan = strategy._find_best_fit("Role", blueprint, available, "S")

        self.assertTrue(plan["valid"])
        self.assertEqual(["A", "B"], [drive.shape_id for drive in plan["assigned_set_drives"]])
        self.assertEqual(["X"], [drive.shape_id for drive in plan["assigned_extra_drives"]])

    def test_matrix_strategy_uses_blueprint_set_pieces_for_slots(self):
        from src.models.equipment import Drive
        from src.optimizer.drive_priority_strategy import MatrixBaseStrategy

        strategy = MatrixBaseStrategy({"Role": {"default_set": "S"}}, {"S": {"shapes": ["A", "B", "C", "D"]}}, {})
        blueprint = {"set_pieces": ["A", "B"], "extra_pieces": ["X"], "board": []}
        drives = [
            Drive(uid="a", quality="Gold", area=1, shape_id="A", main_stats={"x": 1, "y": 1}, role_scores={"Role": 1.0}),
            Drive(uid="b", quality="Gold", area=1, shape_id="B", main_stats={"x": 1, "y": 1}, role_scores={"Role": 1.0}),
            Drive(uid="x", quality="Gold", area=1, shape_id="X", main_stats={"x": 1, "y": 1}, role_scores={"Role": 1.0}),
        ]

        slots, _profit_matrix, _ranking_matrix = strategy._build_profit_matrix(
            [blueprint], ["Role"], drives, {}, {}
        )

        self.assertEqual(["A", "B", "X"], [slot["shape"] for slot in slots])
        self.assertEqual(["set", "set", "extra"], [slot["type"] for slot in slots])


class MatrixStrategyBlueprintSelectionTests(unittest.TestCase):
    def test_blueprint_signature_dedupes_layouts_but_keeps_exact_shape_counts(self):
        from src.solver.blueprint_utils import (
            blueprint_piece_signature,
            dedupe_blueprints_by_piece_signature,
        )

        same_combo_a = {"set_pieces": ["S1", "S2"], "extra_pieces": ["E1", "E2"], "board": [["layout-a"]]}
        same_combo_b = {"set_pieces": ["S2", "S1"], "extra_pieces": ["E2", "E1"], "board": [["layout-b"]]}
        different_combo = {"set_pieces": ["S1", "S2"], "extra_pieces": ["E1", "E1"], "board": [["layout-c"]]}

        self.assertEqual(blueprint_piece_signature(same_combo_a), blueprint_piece_signature(same_combo_b))
        self.assertNotEqual(blueprint_piece_signature(same_combo_a), blueprint_piece_signature(different_combo))

        deduped = dedupe_blueprints_by_piece_signature([same_combo_a, same_combo_b, different_combo])

        self.assertEqual([["layout-a"]], deduped[0]["board"])
        self.assertEqual([["layout-c"]], deduped[1]["board"])

    def test_blueprints_are_deduped_by_complete_extra_shape_combination(self):
        from src.optimizer.drive_priority_strategy import MatrixBaseStrategy

        strategy = MatrixBaseStrategy({}, {}, {})
        blueprints = [
            {"extra_pieces": ["A", "B"], "board": [["layout-1"]]},
            {"extra_pieces": ["B", "A"], "board": [["layout-2"]]},
            {"extra_pieces": ["A", "A"], "board": [["layout-3"]]},
        ]

        deduped = strategy._dedupe_blueprints_by_extra_pieces(blueprints)

        self.assertEqual(2, len(deduped))
        self.assertEqual([["layout-1"]], deduped[0]["board"])
        self.assertEqual(["A", "A"], deduped[1]["extra_pieces"])

    def test_large_blueprint_combos_keep_highest_theoretical_score_and_limit_to_500(self):
        from src.models.equipment import Drive
        from src.optimizer.drive_priority_strategy import MatrixBaseStrategy

        roles = {
            "A": {"default_set": "S", "weights": {}},
            "B": {"default_set": "S", "weights": {}},
        }
        sets = {"S": {"shapes": ["BASE"]}}
        strategy = MatrixBaseStrategy(roles, sets, {})
        role_a_bps = [{"extra_pieces": [f"A{i}"], "board": [[f"a{i}"]]} for i in range(25)]
        role_b_bps = [{"extra_pieces": [f"B{i}"], "board": [[f"b{i}"]]} for i in range(25)]
        drives = [
            Drive(
                uid="base",
                quality="Gold",
                area=1,
                shape_id="BASE",
                main_stats={"x": 1, "y": 1},
                role_scores={"A": 1.0, "B": 1.0},
            ),
            Drive(
                uid="low",
                quality="Gold",
                area=1,
                shape_id="A0",
                main_stats={"x": 1, "y": 1},
                role_scores={"A": 2.0, "B": 0.0},
            ),
            Drive(
                uid="high",
                quality="Gold",
                area=1,
                shape_id="A24",
                main_stats={"x": 1, "y": 1},
                role_scores={"A": 999.0, "B": 0.0},
            ),
        ]

        combos = list(strategy._iter_bp_combos([role_a_bps, role_b_bps], ["A", "B"], drives, {}))

        self.assertEqual(500, strategy.MAX_COMBO_LIMIT)
        self.assertEqual(500, len(combos))
        self.assertEqual("A24", combos[0][0]["extra_pieces"][0])
        self.assertTrue(any(combo[0]["extra_pieces"] == ["A24"] for combo in combos))


class BlueprintGenerationSupportTests(unittest.TestCase):
    def test_blueprint_generation_can_include_layout_variants_without_affecting_allocation_default(self):
        from src.solver.orchestrator import NTEPipelineOrchestrator

        orchestrator = NTEPipelineOrchestrator(config_dir="config")
        role_name = next(name for name in orchestrator.roles_db if name == "娜娜莉")

        allocation_blueprints = orchestrator.solve_blueprints([role_name])
        display_blueprints = orchestrator.solve_blueprints([role_name], include_layout_variants=True)

        self.assertEqual(4, len(allocation_blueprints[role_name]))
        self.assertEqual(15, len(display_blueprints[role_name]))


class RoleEquipmentImportSupportTests(unittest.TestCase):
    def test_load_my_roles_repairs_missing_set_bonus_from_equipped_tape(self):
        from types import SimpleNamespace
        from src.features.role import dao, paths

        original_paths_runtime = paths.runtime
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config_dir = root / "config"
                user_dir = root / "user"
                config_dir.mkdir()
                user_dir.mkdir()
                (config_dir / "tapes.json").write_text(
                    json.dumps(
                        {
                            "森林萤火之心": {
                                "display_name": "森林萤火之心",
                                "skill": {"灵属性异能伤害增强%": 10.0},
                                "skill_2": {"暴击伤害%": 48.0},
                                "skill_cover": 0.8,
                            }
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (user_dir / "my_roles.json").write_text(
                    json.dumps(
                        {
                            "娜娜莉": {
                                "tape": {"set_name": "森林萤火之心"},
                                "set_bonus": {"display_name": "", "skill": {}, "skill_2": {}, "skill_cover": 0.8},
                            }
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                paths.runtime = SimpleNamespace(CONFIG_DIR=config_dir, USER_CONFIG_DIR=user_dir)

                loaded = dao.load_my_roles()

                set_bonus = loaded["娜娜莉"]["set_bonus"]
                self.assertEqual("森林萤火之心", set_bonus["display_name"])
                self.assertEqual({"灵属性异能伤害增强%": 10.0}, set_bonus["skill"])
                self.assertEqual({"暴击伤害%": 48.0}, set_bonus["skill_2"])
        finally:
            paths.runtime = original_paths_runtime

    def test_load_my_roles_keeps_custom_set_bonus_values_when_repairing_name(self):
        from types import SimpleNamespace
        from src.features.role import dao, paths

        original_paths_runtime = paths.runtime
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config_dir = root / "config"
                user_dir = root / "user"
                config_dir.mkdir()
                user_dir.mkdir()
                (config_dir / "tapes.json").write_text(
                    json.dumps(
                        {
                            "森林萤火之心": {
                                "display_name": "森林萤火之心",
                                "skill": {"灵属性异能伤害增强%": 10.0},
                                "skill_2": {"暴击伤害%": 48.0},
                                "skill_cover": 0.8,
                            }
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (user_dir / "my_roles.json").write_text(
                    json.dumps(
                        {
                            "娜娜莉": {
                                "tape": {"set_name": "森林萤火之心"},
                                "set_bonus": {
                                    "display_name": "",
                                    "skill": {"自定义加成%": 12.0},
                                    "skill_2": {"自定义四件%": 34.0},
                                    "skill_cover": 0.5,
                                },
                            }
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                paths.runtime = SimpleNamespace(CONFIG_DIR=config_dir, USER_CONFIG_DIR=user_dir)

                loaded = dao.load_my_roles()

                set_bonus = loaded["娜娜莉"]["set_bonus"]
                self.assertEqual("森林萤火之心", set_bonus["display_name"])
                self.assertEqual({"自定义加成%": 12.0}, set_bonus["skill"])
                self.assertEqual({"自定义四件%": 34.0}, set_bonus["skill_2"])
                self.assertEqual(0.5, set_bonus["skill_cover"])
        finally:
            paths.runtime = original_paths_runtime

    def test_import_role_equipment_splits_tape_equipment_and_set_bonus(self):
        from types import SimpleNamespace
        from src.features.role import equipment_import

        original_runtime = equipment_import.runtime
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config_dir = root / "config"
                user_dir = root / "user"
                config_dir.mkdir()
                user_dir.mkdir()
                (config_dir / "stats.json").write_text(
                    json.dumps(
                        {
                            "tape_main_stat_values": {"攻击力%": 37.5},
                            "stat_alias_mapping": {"攻击力百分比": "攻击力%"},
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                (config_dir / "tapes.json").write_text(
                    json.dumps(
                        {
                            "森林萤火之心": {
                                "display_name": "森林萤火之心",
                                "skill": {"灵属性异能伤害增强%": 10.0},
                                "skill_2": {"暴击伤害%": 48.0},
                                "skill_cover": 0.8,
                            }
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                equipment_import.runtime = SimpleNamespace(CONFIG_DIR=config_dir, USER_CONFIG_DIR=user_dir)

                equipment_import.import_role_equipment(
                    "娜娜莉",
                    [],
                    [],
                    {
                        "uid": "tape_1",
                        "set_name": "森林萤火之心",
                        "main_stats": "攻击力百分比",
                        "sub_stats": {"暴击率%": 10.0},
                        "quality": "Gold",
                    },
                )

                saved = json.loads((user_dir / "my_roles.json").read_text(encoding="utf-8"))
                role = saved["娜娜莉"]
                self.assertEqual({"攻击力%": 37.5}, role["tape"]["main_stats"])
                self.assertNotIn("skill", role["tape"])
                self.assertEqual({"灵属性异能伤害增强%": 10.0}, role["set_bonus"]["skill"])
        finally:
            equipment_import.runtime = original_runtime


class ScanNamingSupportTests(unittest.TestCase):
    def test_scan_naming_preserves_existing_filename_rules(self):
        from src.features.scanning import naming

        self.assertEqual("raw_drive_0001.png", naming.full_scan_filename(1))
        self.assertEqual("raw_drive_probe_0001.png", naming.probe_filename(1))
        self.assertEqual("raw_drive_new_0003.png", naming.auto_new_filename(3))
        self.assertEqual("raw_drive_semi_0004.png", naming.semi_filename(4))
        self.assertEqual(12, naming.raw_drive_index_from_name("raw_drive_0012.png"))
        self.assertTrue(naming.is_full_scan_filename("raw_drive_0012.png"))
        self.assertTrue(naming.is_incremental_filename("raw_drive_probe_0001.png", "incremental_auto"))
        self.assertFalse(naming.is_incremental_filename("raw_drive_semi_0001.png", "incremental_auto"))


class ExtensionDocumentationTests(unittest.TestCase):
    def test_extension_docs_explain_algorithm_and_navigation_extension_points(self):
        architecture = Path("docs/architecture.md")
        extension = Path("docs/extension-guide.md")

        self.assertTrue(architecture.exists())
        self.assertTrue(extension.exists())

        text = architecture.read_text(encoding="utf-8") + "\n" + extension.read_text(encoding="utf-8")
        self.assertIn("src/optimizer/contracts.py", text)
        self.assertIn("src/ui/navigation.py", text)
        self.assertIn("src/domain/stat_catalog.py", text)
        self.assertIn("边际", text)
