# 覆盖角色优先级配置、拖拽批次和优化器回归。

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



class PriorityGroupWorkflowTests(unittest.TestCase):
    def test_manual_cap_written_after_automatic_cap_overrides_it(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {"A": {"default_weapon": "暴击弧盘"}},
            [],
            weapons_db={"暴击弧盘": {"sub_stats": {"暴击率%": 32.0}}},
        )
        selector.selected = ["A"]

        self.assertEqual({"A": 68.0}, selector.get_crit_rate_caps())
        selector._set_crit_rate_cap("A", 80.0)
        self.assertEqual({"A": 80.0}, selector.get_crit_rate_caps())

    def test_release_demon_blade_automatic_cap_is_sixty(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector
        from src.services.official_role.fork_templates import fork_templates_as_weapon_models, load_official_role_fork_templates

        QApplication.instance() or QApplication([])
        weapons = fork_templates_as_weapon_models(load_official_role_fork_templates())
        fork_name, model = next(
            (name, item) for name, item in weapons.items()
            if item["fork_id"] == "fork_DemonBlade"
        )
        selector = RoleSelector()
        selector.load_roles({"A": {"default_weapon": fork_name}}, [], weapons_db=weapons)
        selector.selected = ["A"]

        self.assertEqual(40.0, model["sub_stats"]["暴击率%"])  # 24 + 常驻 16
        self.assertEqual({"A": 60.0}, selector.get_crit_rate_caps())
        self.assertEqual({"A": 40.0}, selector.get_crit_rate_baselines())
        selector._set_crit_rate_cap("A", 76.0)
        self.assertEqual({"A": 76.0}, selector.get_crit_rate_caps())

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

        # 旧配置同时覆盖顺序、重复项和已不存在角色，不再另立微型测试模块。
        data = {"priority_list": ["A", "B", "A", "missing", "C", "B"]}

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

    def test_unselected_roles_sort_by_first_name_character_initial(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {name: {} for name in ("早雾", "「零」", "薄荷", "达芙蒂尔", "白藏", "阿德勒")},
            [],
        )

        self.assertEqual(
            ["阿德勒", "白藏", "薄荷", "达芙蒂尔", "「零」", "早雾"],
            selector._available_role_names(),
        )
        selector.selected = ["白藏"]
        self.assertEqual(
            ["阿德勒", "薄荷", "达芙蒂尔", "「零」", "早雾"],
            selector._available_role_names(),
        )

    def test_search_keeps_selected_chain_and_operator_buttons_visible(self):
        from PySide6.QtWidgets import QApplication, QPushButton

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({name: {} for name in ("早雾", "安魂曲", "薄荷")}, [])
        selector.selected = ["早雾", "安魂曲", "薄荷"]
        selector.priority_links = ["=", ">>"]

        selector.search.setText("没有匹配的角色")

        buttons = [
            item.widget().findChildren(QPushButton)
            for index in range(selector.priority_layout.count())
            if (item := selector.priority_layout.itemAt(index)).widget() is not None
        ]
        texts = [button.text() for group in buttons for button in group]
        self.assertIn("=", texts)
        self.assertIn(">>", texts)
        self.assertEqual(3, texts.count("管理"))

    def test_role_card_uses_provided_portrait_and_keeps_name(self):
        from PySide6.QtWidgets import QApplication, QLabel

        from src.features.allocation.role_selector import RoleSelector
        from src.integrations.game_assets.catalog import GameUiAssetCatalog
        from src.integrations.bundled_resources import bundled_game_ui_asset_root

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        assets = GameUiAssetCatalog(bundled_game_ui_asset_root())
        selector.load_roles(
            {"早雾": {"character_id": 1003}},
            [],
            character_icon_paths={"早雾": assets.character_icon(1003)},
        )

        card = selector._cards["早雾"]["card"]
        labels = card.findChildren(QLabel)
        avatar = next(label for label in labels if label.accessibleName() == "早雾头像")
        self.assertFalse(avatar.pixmap().isNull())
        self.assertIn("早雾", [label.text() for label in labels])

    def test_custom_role_uses_question_portrait_in_both_selector_areas(self):
        from PySide6.QtWidgets import QApplication, QLabel

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {"自定义": {"is_custom": True}},
            [],
            character_icon_paths={"自定义": Path(__file__)},
        )

        candidate = selector._cards["自定义"]["card"]
        candidate_avatar = next(
            label for label in candidate.findChildren(QLabel)
            if label.accessibleName() == "自定义头像"
        )
        self.assertEqual("自定义角色问号头像", candidate_avatar.accessibleDescription())
        self.assertFalse(candidate_avatar.pixmap().isNull())

        selector.selected = ["自定义"]
        selector._render_grid()
        selected_avatar = next(
            label for label in selector.priority_w.findChildren(QLabel)
            if label.accessibleName() == "自定义头像"
        )
        self.assertEqual("自定义角色问号头像", selected_avatar.accessibleDescription())
        self.assertFalse(selected_avatar.pixmap().isNull())

    def test_role_selector_reflows_and_exposes_all_selected_roles(self):
        from PySide6.QtWidgets import QApplication, QScrollArea

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.resize(700, 440)
        selector.show()
        selector.load_roles({f"角色{index}": {} for index in range(18)}, [])
        app.processEvents()
        app.processEvents()
        narrow_candidate_columns = selector._shown_card_columns
        narrow_candidate_height = selector.roles_w.sizeHint().height()
        selector.resize(1600, 440)
        app.processEvents()
        app.processEvents()
        self.assertGreater(selector._shown_card_columns, narrow_candidate_columns)
        self.assertLess(selector.roles_w.sizeHint().height(), narrow_candidate_height)
        card_width = selector._cards["角色0"]["card"].width()
        occupied = selector._shown_card_columns * card_width + (selector._shown_card_columns - 1) * 6
        self.assertLess(selector.width() - 4 - occupied, selector._shown_card_columns)

        selector.selected = [f"角色{index}" for index in range(18)]
        selector.priority_links = [">" for _ in range(17)]
        selector._render_grid()
        app.processEvents()
        wide_selected_columns = selector._shown_priority_columns
        wide_selected_height = selector.roles_w.sizeHint().height()
        self.assertGreater(wide_selected_columns, 4)
        self.assertEqual(18, selector.priority_layout.count())
        self.assertTrue(selector.grid_w.isHidden())
        self.assertEqual([], selector.findChildren(QScrollArea))

        selector.resize(1210, 440)
        app.processEvents()
        app.processEvents()
        self.assertEqual(4, selector._shown_priority_columns)
        self.assertEqual(18, selector.priority_layout.count())

        selector.resize(700, 440)
        app.processEvents()
        app.processEvents()
        self.assertEqual(700, selector.width())
        self.assertLess(selector._shown_priority_columns, wide_selected_columns)
        self.assertGreater(selector.roles_w.sizeHint().height(), wide_selected_height)
        self.assertEqual(18, selector.priority_layout.count())
        selector.close()

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

    def test_role_selector_front_drop_uses_strict_link_without_reachable_boundary(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"A": {}, "B": {}, "C": {}, "D": {}}, [])
        selector.selected = ["A", "B", "C", "D"]
        selector.priority_links = [">", ">>", "="]

        selector._drop_selected_on(0, 2)

        self.assertEqual(["B", "A", "C", "D"], selector.selected)
        self.assertEqual([">>", ">", "="], selector.priority_links)
        self.assertEqual([["B"], ["A"], ["C", "D"]], selector.get_priority_groups())

    def test_role_selector_front_drop_on_adjacent_role_joins_target_group(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"A": {}, "B": {}, "C": {}}, [])
        selector.selected = ["A", "B", "C"]
        selector.priority_links = [">>", ">>"]

        selector._drop_selected_on(0, 1)

        self.assertEqual(["A", "B", "C"], selector.selected)
        self.assertEqual(["=", ">>"], selector.priority_links)
        self.assertEqual([["A", "B"], ["C"]], selector.get_priority_groups())

    def test_role_selector_front_drop_preserves_target_equal_group(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"A": {}, "B": {}, "C": {}, "D": {}}, [])
        selector.selected = ["A", "B", "C", "D"]
        selector.priority_links = [">>", "=", ">>"]

        selector._drop_selected_on(0, 2)

        self.assertEqual(["B", "A", "C", "D"], selector.selected)
        self.assertEqual(["=", "=", ">>"], selector.priority_links)
        self.assertEqual([["B", "A", "C"], ["D"]], selector.get_priority_groups())

    def test_role_selector_front_drop_stops_equal_reachability_at_strict_link(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({name: {} for name in "ABCDE"}, [])
        selector.selected = ["A", "B", "C", "D", "E"]
        selector.priority_links = [">>", "=", ">", ">>"]

        selector._drop_selected_on(0, 1)

        self.assertEqual(["A", "B", "C", "D", "E"], selector.selected)
        self.assertEqual([">", "=", ">", ">>"], selector.priority_links)
        self.assertEqual([["A"], ["B", "C"], ["D"], ["E"]], selector.get_priority_groups())

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

class PriorityPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from src.features.allocation.role_selector import RoleSelector
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "account-a" / "priority.json"
        self.selector = RoleSelector(priority_config_path_provider=lambda: self.path)
        self.addCleanup(self.selector.close)
        self.selector.load_roles({"A": {}, "B": {}}, ["旧卡带"])
        self.selector.selected = ["A"]
        self.selector.custom_sets = {"A": "旧卡带"}
        self.selector.custom_weapons = {"A": "旧弧盘"}
        self.selector.crit_rate_caps = {"A": 74.0}
        self.selector.crit_rate_cap_sources = {"A": "manual"}
        self.selector.tape_main_filters = {"A": []}
        self.selector.tape_main_filter_override_roles = {"A"}
        self.selector.stat_priority_configs = {"A": {"stats": []}}
        self.selector.stat_priority_override_roles = {"A"}
        self.selector.set_effect_modes = {"A": "none"}
        self.fields = ("selected", "priority_links", "custom_sets", "custom_weapons",
                       "crit_rate_caps", "crit_rate_cap_sources", "tape_main_filters",
                       "tape_main_filter_override_roles", "stat_priority_configs",
                       "stat_priority_override_roles", "set_effect_modes")

    def snapshot(self):
        from copy import deepcopy
        return {key: deepcopy(getattr(self.selector, key)) for key in self.fields}

    def test_failed_load_preserves_entire_draft(self):
        from unittest.mock import patch
        before = self.snapshot()
        self.path.parent.mkdir(parents=True)
        for payload in (None, "{", "[]", json.dumps({"priority_list": ["B"], "set_effect_modes": []}),
                        json.dumps({"priority_list": ["B"], "custom_set_overrides": {"B": ["bad"]}}),
                        json.dumps({"priority_list": ["B"], "custom_weapons": {"B": {"bad": 1}}})):
            with self.subTest(payload=payload), patch("PySide6.QtWidgets.QMessageBox.warning") as warning:
                if payload is not None:
                    self.path.write_text(payload, encoding="utf-8")
                self.selector.load_priority_config()
                self.assertEqual(before, self.snapshot())
                self.assertEqual(payload is not None, warning.called)
        with patch("builtins.open", side_effect=PermissionError("read denied")), patch(
            "PySide6.QtWidgets.QMessageBox.warning"
        ) as warning:
            self.selector.load_priority_config()
            self.assertEqual(before, self.snapshot())
            self.assertIn("read denied", warning.call_args.args[2])

    def test_failed_save_and_reset_preserve_file_and_draft(self):
        from unittest.mock import patch
        from src.features.allocation.role_selector_preferences import temporary_priority_config_path
        before = self.snapshot()
        self.path.parent.mkdir(parents=True)
        temp_path = temporary_priority_config_path(self.path)
        for path in (self.path, temp_path):
            path.write_bytes(b'{"previous": true}')
        with patch("os.replace", side_effect=OSError("replace denied")), patch(
            "PySide6.QtWidgets.QMessageBox.information"
        ) as information:
            for action in (self.selector.save_priority_config, self.selector.reset_selection):
                with self.assertRaises(OSError):
                    action()
                self.assertEqual(before, self.snapshot())
            information.assert_not_called()
        for path in (self.path, temp_path):
            self.assertEqual(b'{"previous": true}', path.read_bytes())
        self.assertEqual({self.path, temp_path}, set(self.path.parent.iterdir()))

    def test_load_prepares_new_weapon_and_selection_before_legacy_inference(self):
        self.selector.weapons_db = {"新弧盘": {"sub_stats": {"暴击率%": 32.0}}}
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({
            "priority_list": ["B"], "custom_sets": {"B": "旧卡带"},
            "custom_weapons": {"B": "新弧盘"}, "crit_rate_caps": {"B": 68.0},
            "tape_main_filters": {"B": []}, "stat_priority_override_roles": ["B"],
        }), encoding="utf-8")
        self.selector.load_priority_config()
        self.assertEqual(["B"], self.selector.get_selected())
        self.assertEqual({}, self.selector.custom_sets)
        self.assertEqual({"B": "automatic"}, self.selector.crit_rate_cap_sources)
        self.assertEqual({"B": []}, self.selector.tape_main_filters)
        self.assertEqual({"B"}, self.selector.stat_priority_override_roles)


if __name__ == "__main__":
    unittest.main()
