# 覆盖用户工作流相关的回归测试。
import json
import os
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

class PriorityGroupWorkflowTests(unittest.TestCase):
    def test_role_weight_pools_keep_main_and_sub_stats_separate(self):
        from src.features.configuration.page import (
            _main_weight_choice_pool,
            _sub_weight_choice_pool,
        )

        config_dir = Path("config")
        sub_pool = _sub_weight_choice_pool(config_dir)
        main_pool = _main_weight_choice_pool(config_dir)

        self.assertIn("攻击力", sub_pool)
        self.assertNotIn("治疗加成", sub_pool)
        self.assertIn("治疗加成", main_pool)

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

    def test_role_selector_reorder_selected_moves_role_without_changing_links(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"A": {}, "B": {}, "C": {}, "D": {}}, [])
        selector.selected = ["A", "B", "C", "D"]
        selector.priority_links = ["=", ">>", "="]

        selector._reorder_selected(3, 1)

        self.assertEqual(["A", "D", "B", "C"], selector.selected)
        self.assertEqual(["=", ">>", "="], selector.priority_links)

    def test_role_selector_available_names_excludes_selected_and_filters(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"早雾": {}, "达芙蒂尔": {}, "薄荷": {}}, [])
        selector.selected = ["早雾"]

        self.assertEqual(["薄荷", "达芙蒂尔"], selector._available_role_names(""))
        self.assertEqual(["达芙蒂尔"], selector._available_role_names("达"))

    def test_role_selector_custom_sets_only_store_real_overrides(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
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

    def test_role_selector_legacy_full_custom_sets_do_not_lock_old_defaults(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
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

        app = QApplication.instance() or QApplication([])
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

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"A": {}, "B": {}, "C": {}, "D": {}}, [])
        selector.selected = ["A", "B", "C", "D"]
        selector.priority_links = [">", ">>", "="]

        selector._drop_selected_on(3, 1)

        self.assertEqual(["A", "D", "B", "C"], selector.selected)
        self.assertEqual([">", ">>", "="], selector.priority_links)

    def test_role_selector_uses_single_combined_scroll_area(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()

        self.assertTrue(hasattr(selector, "roles_scroll"))
        self.assertFalse(hasattr(selector, "priority_scroll"))
        self.assertFalse(hasattr(selector, "grid_scroll"))

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
        from src.optimizer.drive_priority_strategy import MatrixBaseStrategy

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


class ConfigurationWorkflowTests(unittest.TestCase):
    def test_reset_config_form_restores_bundled_config_after_confirm(self):
        from src.features.configuration import page

        with tempfile.TemporaryDirectory() as current_tmp, tempfile.TemporaryDirectory() as bundled_tmp:
            current_dir = Path(current_tmp)
            bundled_dir = Path(bundled_tmp)
            (current_dir / "roles.json").write_text(
                json.dumps({"旧角色": {"weights": {}}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (bundled_dir / "roles.json").write_text(
                json.dumps({"默认角色": {"weights": {}}}, ensure_ascii=False),
                encoding="utf-8",
            )

            window = SimpleNamespace(
                _current_config_name="roles.json",
                _config_dirty=True,
                _load_data=lambda: None,
            )
            original_warning = page.QMessageBox.warning
            original_information = page.QMessageBox.information
            original_switch = page.switch_config_form
            switched = []
            page.QMessageBox.warning = lambda *_args, **_kwargs: page.QMessageBox.Yes
            page.QMessageBox.information = lambda *_args, **_kwargs: None
            page.switch_config_form = lambda _window, name, _config_dir: switched.append(name)
            try:
                page.reset_config_form(window, current_dir, bundled_dir)
            finally:
                page.QMessageBox.warning = original_warning
                page.QMessageBox.information = original_information
                page.switch_config_form = original_switch

            self.assertEqual({"默认角色": {"weights": {}}}, json.loads((current_dir / "roles.json").read_text(encoding="utf-8")))
            self.assertFalse(window._config_dirty)
            self.assertEqual(["roles.json"], switched)

    def test_roles_form_does_not_render_or_edit_legacy_board_matrix(self):
        from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QTabWidget, QVBoxLayout, QWidget

        from src.features.configuration import page as config_page

        app = QApplication.instance() or QApplication([])

        class Window:
            all_set_names = ["套装A"]

            def __init__(self):
                self.container = QWidget()
                self.config_form_layout = QVBoxLayout(self.container)

            def _stat_choice_pool(self):
                return ["攻击力"]

            def _save_role_field(self, *_args):
                pass

            def _save_single_extra_shape_buff(self, *_args):
                pass

            def _save_role_weight_value(self, *_args):
                pass

            def _save_role_board_cell(self, *_args):
                pass

            def _del_role(self, *_args):
                pass

            def _add_weight(self, *_args):
                pass

            def _del_weight(self, *_args):
                pass

        data = {
            "A": {
                "default_set": "套装A",
                "extra_shape_buffs": {},
                "weights": {},
            }
        }
        window = Window()
        config_page.render_roles_form(window, data)
        tabs = window.container.findChild(QTabWidget)
        tabs.setCurrentIndex(0)
        app.processEvents()

        combos = [
            combo
            for combo in window.container.findChildren(QComboBox)
            if combo.count() == 2 and [combo.itemText(0), combo.itemText(1)] == ["-1", "0"]
        ]
        self.assertEqual([], combos)
        self.assertFalse(any(button.objectName() == "btnBoardLock" for button in window.container.findChildren(QPushButton)))
        self.assertNotIn(
            "默认套装",
            [label.text() for label in window.container.findChildren(config_page.QLabel)],
        )


class UpdateWorkflowTests(unittest.TestCase):
    def test_update_check_default_timeout_is_short(self):
        from src.features.settings import updates

        seen_timeouts = []
        original_urlopen = updates.urllib.request.urlopen

        def fake_urlopen(_request, **kwargs):
            seen_timeouts.append(kwargs.get("timeout"))
            raise urllib.error.URLError("network unavailable")

        updates.urllib.request.urlopen = fake_urlopen
        try:
            updates.fetch_update_info(
                "https://example.invalid/latest",
                "https://example.invalid/releases",
                "1.1.0",
            )
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertTrue(seen_timeouts)
        self.assertTrue(all(timeout <= 3 for timeout in seen_timeouts))

    def test_startup_update_error_updates_status_without_prompt(self):
        import src.ui.app as app_module

        class Label:
            def __init__(self):
                self.text = ""

            def setText(self, text):
                self.text = text

        class Window:
            def __init__(self):
                self._update_check_manual = False
                self._update_status = Label()
                self.prompts = []

            def _show_update_failure_netdisk_prompt(self, detail=""):
                self.prompts.append(detail)

        window = Window()
        app_module.MainWindow._on_update_error(window, "timeout")

        self.assertIn("GitHub请求失败", window._update_status.text)
        self.assertEqual([], window.prompts)

    def test_update_network_failure_returns_user_facing_result(self):
        from src.features.settings import updates

        original_urlopen = updates.urllib.request.urlopen
        updates.urllib.request.urlopen = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError(OSError(10061, "connection refused"))
        )
        try:
            info = updates.fetch_update_info(
                "https://example.invalid/latest",
                "https://example.invalid/releases",
                "1.1.0",
                timeout=1,
            )
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertFalse(info["has_release"])
        self.assertFalse(info["newer"])
        self.assertEqual("https://example.invalid/releases", info["url"])
        self.assertEqual("GitHub请求失败，可前往网盘链接查看版本更新情况", info["message"])
        self.assertNotIn("Traceback", info["message"])

    def test_update_rate_limit_returns_user_facing_result(self):
        from src.features.settings import updates

        original_urlopen = updates.urllib.request.urlopen
        error = urllib.error.HTTPError(
            "https://example.invalid/latest",
            403,
            "rate limit exceeded",
            hdrs=None,
            fp=None,
        )
        updates.urllib.request.urlopen = lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
        try:
            info = updates.fetch_update_info(
                "https://example.invalid/latest",
                "https://example.invalid/releases",
                "1.1.0",
                timeout=1,
            )
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertFalse(info["has_release"])
        self.assertTrue(info["error"])
        self.assertEqual("GitHub请求失败，可前往网盘链接查看版本更新情况", info["message"])

    def test_update_rate_limit_falls_back_to_latest_release_page(self):
        from src.features.settings import updates

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"{}"

            def geturl(self):
                return "https://github.com/example/project/releases/tag/1.1.1"

        original_urlopen = updates.urllib.request.urlopen
        error = urllib.error.HTTPError(
            "https://api.github.com/repos/example/project/releases/latest",
            403,
            "rate limit exceeded",
            hdrs=None,
            fp=None,
        )

        def fake_urlopen(request, **_kwargs):
            url = request.full_url if hasattr(request, "full_url") else str(request)
            if "api.github.com" in url:
                raise error
            return Response()

        updates.urllib.request.urlopen = fake_urlopen
        try:
            info = updates.fetch_update_info(
                "https://api.github.com/repos/example/project/releases/latest",
                "https://github.com/example/project/releases",
                "1.1.0",
                timeout=1,
            )
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertTrue(info["has_release"])
        self.assertTrue(info["newer"])
        self.assertEqual("1.1.1", info["latest"])
        self.assertEqual("https://github.com/example/project/releases/tag/1.1.1", info["release_url"])

    def test_update_check_reads_release_notes_from_atom_feed_without_api_call(self):
        from src.features.settings import updates

        atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <link rel="alternate" type="text/html" href="https://github.com/example/project/releases/tag/1.1.1"/>
    <title>NTE_Drive_Calc_Setup_1.1.1.exe</title>
    <content type="html">&lt;p&gt;新功能：&lt;br&gt;1. 修复更新说明&lt;/p&gt;</content>
  </entry>
</feed>"""

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return atom.encode("utf-8")

        original_urlopen = updates.urllib.request.urlopen

        def fake_urlopen(request, **_kwargs):
            url = request.full_url if hasattr(request, "full_url") else str(request)
            if "api.github.com" in url:
                raise AssertionError("update check should not call GitHub REST API by default")
            self.assertTrue(url.endswith("/releases.atom"))
            return Response()

        updates.urllib.request.urlopen = fake_urlopen
        try:
            info = updates.fetch_update_info(
                "https://api.github.com/repos/example/project/releases/latest",
                "https://github.com/example/project/releases",
                "1.1.0",
                timeout=1,
            )
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertTrue(info["has_release"])
        self.assertTrue(info["newer"])
        self.assertEqual("1.1.1", info["latest"])
        self.assertEqual("https://github.com/example/project/releases/tag/1.1.1", info["release_url"])
        self.assertIn("新功能", info["message"])
        self.assertIn("修复更新说明", info["message"])

    def test_update_check_falls_back_to_latest_release_page_without_api_call(self):
        from src.features.settings import updates

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://github.com/example/project/releases/tag/1.1.1"

        original_urlopen = updates.urllib.request.urlopen

        def fake_urlopen(request, **_kwargs):
            url = request.full_url if hasattr(request, "full_url") else str(request)
            if "api.github.com" in url:
                raise AssertionError("update check should not call GitHub REST API by default")
            if url.endswith(".atom"):
                raise urllib.error.URLError("feed unavailable")
            return Response()

        updates.urllib.request.urlopen = fake_urlopen
        try:
            info = updates.fetch_update_info(
                "https://api.github.com/repos/example/project/releases/latest",
                "https://github.com/example/project/releases",
                "1.1.0",
                timeout=1,
            )
        finally:
            updates.urllib.request.urlopen = original_urlopen

        self.assertTrue(info["has_release"])
        self.assertTrue(info["newer"])
        self.assertEqual("1.1.1", info["latest"])

    def test_update_dialog_link_prefers_download_url(self):
        from src.features.settings.updates import update_dialog_link_url

        info = {"url": "https://example.invalid/download.exe", "release_url": "https://example.invalid/release"}
        self.assertEqual("https://example.invalid/download.exe", update_dialog_link_url(info))

    def test_quark_netdisk_url_uses_latest_link(self):
        from src.app.constants import NETDISK_DOWNLOAD_LINKS, QUARK_NETDISK_URL

        self.assertEqual("https://pan.quark.cn/s/82f16b845aec", QUARK_NETDISK_URL)
        self.assertIn(("夸克网盘", "https://pan.quark.cn/s/82f16b845aec"), NETDISK_DOWNLOAD_LINKS)

    def test_marginal_benefit_uses_ability_damage_term(self):
        from src.features.role import core

        original_load_stats = core.load_stats
        try:
            core.load_stats = lambda: {"benefit_one": {"异能伤害%": 1.25}}
            _base, items = core.calc_marginal_benefits(
                {
                    "攻击力白值": 100.0,
                    "攻击力%": 0.0,
                    "攻击力": 0.0,
                    "异能伤害%": 10.0,
                    "伤害增加%": 0.0,
                    "暴击率%": 0.0,
                    "暴击伤害%": 0.0,
                }
            )
        finally:
            core.load_stats = original_load_stats

        names = [item[0] for item in items]
        self.assertIn("异能伤害%", names)
        self.assertNotIn("元素" + "伤害%", names)


class ConfigurationRoleOrderTests(unittest.TestCase):
    def test_new_role_is_inserted_at_the_start_of_role_tabs(self):
        from src.features.configuration import page as config_page

        class Window:
            all_set_names = ["套装A"]

        saved = []
        switched = []
        old_get_text = config_page.QInputDialog.getText
        old_save = config_page.save_config_data
        old_switch = config_page.switch_config_form
        config_page.QInputDialog.getText = lambda *_args, **_kwargs: ("新角色", True)
        config_page.save_config_data = lambda _window, data, _config_dir: saved.append(list(data))
        config_page.switch_config_form = lambda *_args, **kwargs: switched.append(kwargs.get("active_role"))
        try:
            data = {"旧角色A": {}, "旧角色B": {}}
            config_page.add_role(Window(), data, Path("."))
        finally:
            config_page.QInputDialog.getText = old_get_text
            config_page.save_config_data = old_save
            config_page.switch_config_form = old_switch

        self.assertEqual(["新角色", "旧角色A", "旧角色B"], list(data))
        self.assertEqual([["新角色", "旧角色A", "旧角色B"]], saved)
        self.assertEqual(["新角色"], switched)
