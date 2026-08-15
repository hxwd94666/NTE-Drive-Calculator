# 覆盖角色优先级配置、拖拽批次和优化器回归。

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



class PriorityGroupWorkflowTests(unittest.TestCase):
    def test_explicit_empty_protagonist_preferences_round_trip(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "priority.json"
            selector = RoleSelector()
            selector.load_roles(
                {"「零」": {"character_id": 1051}},
                [],
                tape_main_stats=["攻击力%", "环合强度"],
                drive_sub_stats=["攻击力%", "环合强度"],
            )
            selector.selected = ["「零」"]
            selector._set_tape_main_filter("「零」", [])
            selector._set_stat_priority_config("「零」", [], [], False, False, "A")
            selector._write_priority_config(path)

            saved = json.loads(path.read_text(encoding="utf-8"))
            restored = RoleSelector()
            restored.load_roles(
                {"「零」": {"character_id": 1051}},
                [],
                tape_main_stats=["攻击力%", "环合强度"],
                drive_sub_stats=["攻击力%", "环合强度"],
            )
            restored._load_priority_config_from(path)

        self.assertEqual({"「零」": []}, saved["tape_main_filters"])
        self.assertEqual(["「零」"], saved["tape_main_filter_override_roles"])
        self.assertEqual(["「零」"], saved["stat_priority_override_roles"])
        self.assertEqual({}, restored.get_tape_main_filters())
        self.assertEqual({}, restored.get_crit_priority_modes())
        self.assertEqual([], restored._selected_substat_priority("「零」"))

    def test_saved_mag_preferences_are_preserved_for_every_role(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "priority.json"
            path.write_text(
                json.dumps(
                    {
                        "priority_list": ["主角", "卡尼斯"],
                        "tape_main_filters": {
                            "主角": ["环合强度"],
                            "卡尼斯": ["环合强度"],
                        },
                        "stat_priority_configs": {
                            "主角": {"stats": ["环合强度"]},
                            "卡尼斯": {"stats": ["环合强度"]},
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            selector = RoleSelector()
            selector.load_roles(
                {
                    "主角": {"character_id": 1051},
                    "卡尼斯": {"character_id": 1071},
                },
                [],
                tape_main_stats=["环合强度"],
                drive_sub_stats=["环合强度"],
            )

            selector._load_priority_config_from(path)

        self.assertEqual(
            {"主角": ["环合强度"], "卡尼斯": ["环合强度"]},
            selector.get_tape_main_filters(),
        )
        priority_modes = selector.get_crit_priority_modes()
        self.assertEqual({"主角", "卡尼斯"}, set(priority_modes))
        self.assertEqual(["环合强度"], priority_modes["主角"]["stats"])
        self.assertEqual(["环合强度"], priority_modes["卡尼斯"]["stats"])

    def test_priority_links_promote_boundary_splits_two_equal_batches(self):
        from src.features.allocation.priority_groups import (
            links_to_priority_groups,
            promote_priority_boundary,
        )

        roles = ["A", "B", "C", "D"]
        links = [">", ">", "="]

        promote_priority_boundary(links, 1)

        self.assertEqual(["=", ">>", "="], links)
        self.assertEqual([["A", "B"], ["C", "D"]], links_to_priority_groups(roles, links))

    def test_priority_link_cycles_strict_equal_boundary_with_expected_batch_edits(self):
        from src.features.allocation.priority_groups import (
            cycle_priority_link,
            links_to_priority_groups,
        )

        roles = ["A", "B", "C", "D", "E"]
        links = [">", ">", ">", ">>"]

        cycle_priority_link(links, 1)
        self.assertEqual([">", "=", ">", ">>"], links)
        self.assertEqual([["A"], ["B", "C"], ["D"], ["E"]], links_to_priority_groups(roles, links))

        cycle_priority_link(links, 1)
        self.assertEqual(["=", ">>", ">", ">>"], links)
        self.assertEqual([["A", "B"], ["C"], ["D"], ["E"]], links_to_priority_groups(roles, links))

        cycle_priority_link(links, 1)
        self.assertEqual([">", ">", ">", ">>"], links)
        self.assertEqual([["A"], ["B"], ["C"], ["D"], ["E"]], links_to_priority_groups(roles, links))

    def test_priority_groups_loads_old_priority_list_as_strict_order(self):
        from src.features.allocation.priority_groups import load_priority_selection

        data = {"priority_list": ["A", "B", "C"]}

        selected, links = load_priority_selection(data, {"A": {}, "B": {}, "C": {}})

        self.assertEqual(["A", "B", "C"], selected)
        self.assertEqual([">", ">"], links)

    def test_priority_groups_loads_new_group_config(self):
        from src.features.allocation.priority_groups import load_priority_selection

        data = {"priority_groups": [["A", "B"], ["C"]], "priority_list": ["C", "A", "B"]}

        selected, links = load_priority_selection(data, {"A": {}, "B": {}, "C": {}})

        self.assertEqual(["A", "B", "C"], selected)
        self.assertEqual(["=", ">>"], links)

    def test_singleton_groups_restore_as_strict_links(self):
        from src.features.allocation.priority_groups import priority_groups_to_links

        self.assertEqual(
            [">", ">"],
            priority_groups_to_links(
                ["A", "B", "C"],
                [["A"], ["B"], ["C"]],
            ),
        )

    def test_role_selector_reorder_selected_moves_crossed_boundary_backward(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"A": {}, "B": {}, "C": {}, "D": {}}, [])
        selector.selected = ["A", "B", "C", "D"]
        selector.priority_links = ["=", ">>", "="]

        selector._reorder_selected(3, 1)

        self.assertEqual(["A", "D", "B", "C"], selector.selected)
        self.assertEqual(["=", "=", ">>"], selector.priority_links)

    def test_dragging_role_forward_moves_crossed_boundary_one_slot_forward(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({name: {} for name in "ABCDE"}, [])
        selector.selected = ["A", "B", "C", "D", "E"]
        selector.priority_links = ["=", ">>", ">", "="]

        selector._reorder_selected(0, 3)

        self.assertEqual(["B", "C", "D", "A", "E"], selector.selected)
        self.assertEqual([">>", "=", ">", "="], selector.priority_links)

    def test_dragging_role_backward_moves_crossed_boundary_one_slot_backward(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({name: {} for name in "ABCDE"}, [])
        selector.selected = ["A", "B", "C", "D", "E"]
        selector.priority_links = ["=", ">>", ">", "="]

        selector._reorder_selected(3, 0)

        self.assertEqual(["D", "A", "B", "C", "E"], selector.selected)
        self.assertEqual(["=", "=", ">>", "="], selector.priority_links)

    def test_role_selector_available_names_excludes_selected_and_filters(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"早雾": {}, "达芙蒂尔": {}, "薄荷": {}}, [])
        selector.selected = ["早雾"]

        self.assertEqual(["薄荷", "达芙蒂尔"], selector._available_role_names(""))
        self.assertEqual(["达芙蒂尔"], selector._available_role_names("达"))

    def test_role_selector_custom_sets_only_store_real_overrides(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {
                "九原": {"default_set": "影之信条"},
                "娜娜莉": {"default_set": "森林套"},
            },
            ["影之信条", "森林套"],
        )
        selector.selected = ["九原", "娜娜莉"]

        selector._set_custom_set("九原", "影之信条")
        selector._set_custom_set("娜娜莉", "影之信条")

        self.assertEqual({"娜娜莉": "影之信条"}, selector.get_custom_sets())

    def test_role_selector_persists_substat_blacklist_without_priority(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {"A": {}},
            [],
            drive_sub_stats=["wanted", "blocked"],
        )
        selector.selected = ["A"]

        selector._set_stat_priority_config(
            "A",
            ["wanted"],
            ["blocked"],
            False,
            True,
            "A",
        )

        self.assertEqual(
            ["blocked"],
            selector.get_crit_priority_modes()["A"]["blacklist"],
        )

    def test_role_selector_legacy_full_custom_sets_do_not_lock_old_defaults(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {
                "九原": {"default_set": "影之信条"},
                "娜娜莉": {"default_set": "森林套"},
            },
            ["影之信条", "森林套"],
        )
        legacy_data = {
            "priority_list": ["九原", "娜娜莉"],
            "custom_sets": {"九原": "森林套", "娜娜莉": "森林套"},
        }

        selector.selected, selector.priority_links = ["九原", "娜娜莉"], [">"]
        self.assertEqual({}, selector._load_custom_set_overrides(legacy_data))

    def test_role_selector_drop_selected_to_target_position_from_front(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"A": {}, "B": {}, "C": {}, "D": {}}, [])
        selector.selected = ["A", "B", "C", "D"]
        selector.priority_links = [">", ">>", "="]

        selector._drop_selected_on(0, 2)

        self.assertEqual(["B", "A", "C", "D"], selector.selected)
        self.assertEqual([">", ">>", "="], selector.priority_links)

    def test_role_selector_drop_selected_to_target_position_from_back(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"A": {}, "B": {}, "C": {}, "D": {}}, [])
        selector.selected = ["A", "B", "C", "D"]
        selector.priority_links = [">", ">>", "="]

        selector._drop_selected_on(3, 1)

        self.assertEqual(["A", "D", "B", "C"], selector.selected)
        self.assertEqual(["=", "=", ">>"], selector.priority_links)

    def test_role_priority_batch_uses_local_optimum_within_equal_group(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        roles_db = {"A": {"default_set": "Set"}, "B": {"default_set": "Set"}}
        sets_db = {"Set": {"shapes": []}}
        blueprints_db = {
            "A": [{"set_pieces": [], "extra_pieces": ["X"]}],
            "B": [{"set_pieces": [], "extra_pieces": ["X"]}],
        }
        drives = [
            Drive(
                uid="drive_1",
                quality="Gold",
                area=1,
                shape_id="X",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                role_scores={"A": 100.0, "B": 99.0},
            ),
            Drive(
                uid="drive_2",
                quality="Gold",
                area=1,
                shape_id="X",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                role_scores={"A": 98.0, "B": 1.0},
            ),
        ]

        result = RolePriorityStrategy(roles_db, sets_db, blueprints_db).execute(
            {"drives": drives, "tapes": {}},
            ["A", "B"],
            {"A": "Set", "B": "Set"},
            priority_groups=[["A", "B"]],
        )

        self.assertEqual("drive_2", result["A"]["assigned_extra_drives"][0].uid)
        self.assertEqual("drive_1", result["B"]["assigned_extra_drives"][0].uid)

    def test_equal_group_isolates_individually_impossible_role(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        roles_db = {
            "A": {"default_set": "Set"},
            "B": {"default_set": "Set"},
            "C": {"default_set": "Set"},
        }
        strategy = RolePriorityStrategy(
            roles_db,
            {"Set": {"shapes": []}},
            {
                "A": [{"set_pieces": [], "extra_pieces": ["X"]}],
                "B": [{"set_pieces": [], "extra_pieces": ["Y"]}],
                "C": [{"set_pieces": [], "extra_pieces": ["Z"]}],
            },
        )
        drives = [
            Drive(uid="x", quality="Gold", area=1, shape_id="X", set_name="Set", main_stats={"m1": 1, "m2": 1}),
            Drive(uid="y", quality="Gold", area=1, shape_id="Y", set_name="Set", main_stats={"m1": 1, "m2": 1}),
        ]

        result = strategy.execute(
            {"drives": drives, "tapes": {}}, ["A", "B", "C"],
            {"A": "Set", "B": "Set", "C": "Set"}, priority_groups=[["A", "B", "C"]],
        )

        self.assertTrue(result["A"]["valid"])
        self.assertTrue(result["B"]["valid"])
        self.assertFalse(result["C"]["valid"])

    def test_role_priority_batch_reuses_matrix_combo_iterator(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        class TrackingRolePriorityStrategy(RolePriorityStrategy):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.used_matrix_combo_iterator = False

            def _iter_bp_combos(self, *args, **kwargs):
                self.used_matrix_combo_iterator = True
                yield from super()._iter_bp_combos(*args, **kwargs)

        roles_db = {"A": {"default_set": "Set"}, "B": {"default_set": "Set"}}
        sets_db = {"Set": {"shapes": []}}
        blueprints_db = {
            "A": [{"set_pieces": [], "extra_pieces": ["X"]}],
            "B": [{"set_pieces": [], "extra_pieces": ["X"]}],
        }
        drives = [
            Drive(
                uid="drive_1",
                quality="Gold",
                area=1,
                shape_id="X",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                role_scores={"A": 10.0, "B": 9.0},
            ),
            Drive(
                uid="drive_2",
                quality="Gold",
                area=1,
                shape_id="X",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                role_scores={"A": 8.0, "B": 7.0},
            ),
        ]
        strategy = TrackingRolePriorityStrategy(roles_db, sets_db, blueprints_db)

        strategy.execute(
            {"drives": drives, "tapes": {}},
            ["A", "B"],
            {"A": "Set", "B": "Set"},
            priority_groups=[["A", "B"]],
        )

        self.assertTrue(strategy.used_matrix_combo_iterator)

    def test_role_priority_single_role_deduplicates_equivalent_blueprints(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        class TrackingRolePriorityStrategy(RolePriorityStrategy):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.fit_calls = 0

            def _find_best_fit(self, *args, **kwargs):
                self.fit_calls += 1
                return super()._find_best_fit(*args, **kwargs)

        roles_db = {"A": {"default_set": "Set"}}
        sets_db = {"Set": {"shapes": []}}
        blueprints_db = {
            "A": [
                {"set_pieces": [], "extra_pieces": ["X"], "board": [["first"]]},
                {"set_pieces": [], "extra_pieces": ["X"], "board": [["duplicate"]]},
            ]
        }
        drives = [
            Drive(
                uid="drive_1",
                quality="Gold",
                area=1,
                shape_id="X",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                role_scores={"A": 10.0},
            )
        ]
        strategy = TrackingRolePriorityStrategy(roles_db, sets_db, blueprints_db)

        strategy.execute({"drives": drives, "tapes": {}}, ["A"], {"A": "Set"})

        self.assertEqual(1, strategy.fit_calls)

    def test_role_priority_single_role_filters_unused_drive_shapes_before_matching(self):
        from src.models.equipment import Drive
        from src.optimizer.role_priority_strategy import RolePriorityStrategy

        class TrackingRolePriorityStrategy(RolePriorityStrategy):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.available_shapes = []

            def _find_best_fit(self, role_name, blueprint, available_pool, target_set, crit_mode=None):
                self.available_shapes.append({drive.shape_id for drive in available_pool})
                return super()._find_best_fit(role_name, blueprint, available_pool, target_set, crit_mode)

        roles_db = {"A": {"default_set": "Set"}}
        sets_db = {"Set": {"shapes": []}}
        blueprints_db = {"A": [{"set_pieces": [], "extra_pieces": ["X"], "board": []}]}
        drives = [
            Drive(
                uid="drive_x",
                quality="Gold",
                area=1,
                shape_id="X",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                role_scores={"A": 10.0},
            ),
            Drive(
                uid="drive_y",
                quality="Gold",
                area=1,
                shape_id="Y",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                role_scores={"A": 99.0},
            ),
        ]
        strategy = TrackingRolePriorityStrategy(roles_db, sets_db, blueprints_db)

        strategy.execute({"drives": drives, "tapes": {}}, ["A"], {"A": "Set"})

        self.assertEqual([{"X"}], strategy.available_shapes)

    def test_matrix_base_does_not_shadow_shared_matrix_helpers(self):
        from src.optimizer.global_optimal_strategy import MatrixBaseStrategy

        duplicated_helpers = {
            "_blueprint_extra_key",
            "_dedupe_blueprints_by_extra_pieces",
            "_shape_score_buckets",
            "_blueprint_theoretical_score",
            "_rank_role_blueprints",
            "_iter_ranked_bp_combos",
            "_iter_bp_combos",
            "_build_profit_matrix",
            "_init_temp_alloc",
        }

        self.assertFalse(duplicated_helpers & set(MatrixBaseStrategy.__dict__))





if __name__ == "__main__":

    unittest.main()
