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

    def test_role_selector_priority_frame_width_is_content_based(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()

        short_width = selector._priority_role_frame_width("AB")
        long_width = selector._priority_role_frame_width("ABCDE")

        self.assertEqual(short_width, long_width)
        self.assertLess(short_width, 140)
        self.assertLess(selector._priority_role_name_font_size("ABCDE"), selector._priority_role_name_font_size("AB"))

    def test_role_selector_priority_and_pool_left_edges_align(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()

        self.assertEqual(
            selector.grid_layout.contentsMargins().left(),
            selector.priority_layout.contentsMargins().left(),
        )

    def test_role_selector_wrapped_priority_unit_keeps_content_width(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.selected = ["A", "LongRoleA", "B", "LongRoleB", "C", "D"]
        selector.priority_links = [">", ">", ">", ">", ">"]

        selector._render_priority_row()
        selector.resize(900, 400)
        selector.show()
        app.processEvents()
        selector.priority_layout.activate()
        app.processEvents()

        wrapped_unit = selector.priority_layout.itemAt(5).widget()

        self.assertEqual(wrapped_unit.sizeHint().width(), wrapped_unit.geometry().width())

    def test_priority_role_button_builds_drag_pixmap_without_render_overload(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import PriorityRoleButton, RoleSelector

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()
        button = PriorityRoleButton(selector, "A", 0)
        button.resize(120, 40)
        button.show()
        app.processEvents()

        pixmap = button._make_drag_pixmap(button)

        self.assertFalse(pixmap.isNull())

    def test_role_priority_batch_uses_local_optimum_within_equal_group(self):
        from src.models.equipment import Drive
        from src.optimizer.strategies import RolePriorityStrategy

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

    def test_role_priority_batch_reuses_matrix_combo_iterator(self):
        from src.models.equipment import Drive
        from src.optimizer.strategies import RolePriorityStrategy

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
        from src.optimizer.strategies import RolePriorityStrategy

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
        from src.optimizer.strategies import RolePriorityStrategy

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
        from src.optimizer.strategies import MatrixBaseStrategy

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
    def test_role_board_cell_change_updates_draft_data(self):
        from src.features.configuration import page

        window = SimpleNamespace(_current_config_name="roles.json")
        data = {"role": {"board_matrix": [[0] * 5 for _ in range(5)]}}

        page.save_role_board_cell(window, "role", 1, 2, "-1", data, Path("."))

        self.assertTrue(window._config_dirty)
        self.assertEqual(-1, data["role"]["board_matrix"][1][2])


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

    def test_settings_update_buttons_use_requested_order(self):
        from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QVBoxLayout

        from src.features.settings.page import build_settings_page

        app = QApplication.instance() or QApplication([])

        class Window:
            _log_enabled = False
            _hk_capture = "F9"
            _hk_finish = "F10"
            _hk_stop = "F8"

            def _card(self, _title):
                card = QFrame()
                QVBoxLayout(card)
                return card

            def _toggle_log(self, *_args):
                pass

            def _save_hotkeys(self):
                pass

            def _check_updates(self, manual=True):
                pass

            def _open_update_homepage(self):
                pass

            def _open_url(self, _url):
                pass

            def _refresh_ss(self):
                pass

            def _clear_ss(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            window = Window()
            scroll = build_settings_page(
                window,
                "1.1.0",
                lambda: {
                    "screenshot_dir": root / "scanned_images",
                    "output_file": root / "config" / "real_inventory.json",
                    "config_dir": root / "config",
                    "accounts_dir": root / "accounts",
                    "log_dir": root / "logs",
                },
                lambda _path: [],
                "https://pan.quark.cn/s/42f0d8bed584",
            )

            button_texts = [button.text() for button in scroll.findChildren(QPushButton)]

        self.assertEqual(
            ["检查更新", "网盘下载", "GitHub 主页"],
            [text for text in button_texts if text in {"检查更新", "网盘下载", "GitHub 主页"}],
        )
        app.processEvents()

    def test_settings_page_does_not_show_inventory_info_card(self):
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout

        from src.features.settings.page import build_settings_page

        app = QApplication.instance() or QApplication([])

        class Window:
            _log_enabled = False
            _hk_capture = "F9"
            _hk_finish = "F10"
            _hk_stop = "F8"

            def _card(self, title):
                card = QFrame()
                layout = QVBoxLayout(card)
                layout.addWidget(QLabel(title))
                return card

            def _toggle_log(self, *_args):
                pass

            def _save_hotkeys(self):
                pass

            def _check_updates(self, manual=True):
                pass

            def _open_update_homepage(self):
                pass

            def _open_url(self, _url):
                pass

            def _refresh_ss(self):
                pass

            def _clear_ss(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scroll = build_settings_page(
                Window(),
                "1.1.0",
                lambda: {
                    "screenshot_dir": root / "scanned_images",
                    "output_file": root / "config" / "real_inventory.json",
                    "config_dir": root / "config",
                    "accounts_dir": root / "accounts",
                    "log_dir": root / "logs",
                },
                lambda _path: [],
                "",
            )

        labels = [label.text() for label in scroll.findChildren(QLabel)]
        self.assertNotIn("库存信息", labels)
        self.assertFalse(any("real_inventory.json" in text for text in labels))
        app.processEvents()

    def test_settings_hotkey_save_button_is_short_and_in_title_row(self):
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QVBoxLayout

        from src.features.settings.page import build_settings_page

        app = QApplication.instance() or QApplication([])

        class Window:
            _log_enabled = False
            _hk_capture = "F9"
            _hk_finish = "F10"
            _hk_stop = "F8"

            def _card(self, title):
                card = QFrame()
                layout = QVBoxLayout(card)
                layout.addWidget(QLabel(title))
                return card

            def _toggle_log(self, *_args):
                pass

            def _save_hotkeys(self):
                pass

            def _check_updates(self, manual=True):
                pass

            def _open_update_homepage(self):
                pass

            def _open_url(self, _url):
                pass

            def _refresh_ss(self):
                pass

            def _clear_ss(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scroll = build_settings_page(
                Window(),
                "1.1.0",
                lambda: {
                    "screenshot_dir": root / "scanned_images",
                    "output_file": root / "config" / "real_inventory.json",
                    "config_dir": root / "config",
                    "accounts_dir": root / "accounts",
                    "log_dir": root / "logs",
                },
                lambda _path: [],
                "",
            )

        save_button = next(button for button in scroll.findChildren(QPushButton) if button.text() == "保存快捷键")
        title_label = next(label for label in scroll.findChildren(QLabel) if label.text() == "快捷键绑定")

        self.assertLessEqual(save_button.maximumWidth(), 130)
        self.assertIs(save_button.parentWidget(), title_label.parentWidget())
        app.processEvents()


class UsageGuideWorkflowTests(unittest.TestCase):
    def test_usage_guide_does_not_show_folder_open_buttons(self):
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget

        from src.features.onboarding import guide

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "guide.png"
            pixmap = QPixmap(16, 16)
            pixmap.fill()
            self.assertTrue(pixmap.save(str(image_path)))

            class Window(QWidget):
                def _guide_image_files(self):
                    return [image_path]

            captured_buttons = []
            original_exec = guide.QDialog.exec

            def fake_exec(dialog):
                captured_buttons.extend(button.text() for button in dialog.findChildren(QPushButton))
                return QDialog.Accepted

            guide.QDialog.exec = fake_exec
            try:
                guide._show_quick_start(Window())
            finally:
                guide.QDialog.exec = original_exec

        self.assertNotIn("打开截图文件夹", captured_buttons)
        self.assertNotIn("打开配置文件夹", captured_buttons)
        app.processEvents()


class RolePriorityWorkflowTests(unittest.TestCase):
    def test_stat_choice_resolution_prefers_exact_current_data(self):
        from src.features.allocation.role_selector import resolve_priority_choice

        stats = ["攻击力", "攻击力%", "防御力", "防御力%", "生命值", "生命值%"]
        self.assertEqual("攻击力", resolve_priority_choice(stats, "攻击力%", current_data="攻击力"))
        self.assertEqual("防御力", resolve_priority_choice(stats, "防御力%", current_data="防御力"))
        self.assertEqual("生命值", resolve_priority_choice(stats, "生命值%", current_data="生命值"))

    def test_priority_selector_has_permanent_and_temporary_slots(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "priority_config.json"
            selector = RoleSelector(priority_config_path_provider=lambda: path)
            selector.load_roles({"A": {}, "B": {}}, ["S"], [], ["攻击力"])

            selector.selected = ["A"]
            selector.save_priority_config(show_message=False)
            selector.selected = ["B"]
            selector.reset_selection()

            self.assertEqual([], selector.selected)
            self.assertTrue(path.exists())
            self.assertTrue((Path(tmp) / "priority_config.temp.json").exists())

            selector.load_priority_config()
            self.assertEqual(["A"], selector.selected)

            selector.restore_temporary_priority_config()
            self.assertEqual(["B"], selector.selected)
        app.processEvents()

    def test_priority_selector_startup_prefers_temporary_slot(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector, temporary_priority_config_path

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "priority_config.json"
            path.write_text(json.dumps({"priority_list": ["A"]}, ensure_ascii=False), encoding="utf-8")
            temporary_priority_config_path(path).write_text(
                json.dumps({"priority_list": ["B"]}, ensure_ascii=False),
                encoding="utf-8",
            )
            selector = RoleSelector(priority_config_path_provider=lambda: path)
            selector.load_roles({"A": {}, "B": {}}, ["S"], [], [])

            selector.load_startup_priority_config()

            self.assertEqual(["B"], selector.selected)
        app.processEvents()

    def test_priority_selector_persists_set_effect_modes_and_defaults_to_four_piece(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector
        from src.solver.set_effects import FOUR_PIECE, NO_EFFECT, TWO_PIECE

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "priority_config.json"
            selector = RoleSelector(priority_config_path_provider=lambda: path)
            selector.load_roles({"A": {}, "B": {}, "C": {}}, ["S"], [], [])
            selector.selected = ["A", "B", "C"]
            selector._set_set_effect_mode("A", TWO_PIECE)
            selector._set_set_effect_mode("B", NO_EFFECT)
            selector._set_set_effect_mode("C", FOUR_PIECE)

            selector.save_priority_config(show_message=False)

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({"A": TWO_PIECE, "B": NO_EFFECT}, data["set_effect_modes"])

            restored = RoleSelector(priority_config_path_provider=lambda: path)
            restored.load_roles({"A": {}, "B": {}, "C": {}}, ["S"], [], [])
            restored.load_priority_config()

            self.assertEqual(["A", "B", "C"], restored.selected)
            self.assertEqual(TWO_PIECE, restored.set_effect_modes["A"])
            self.assertEqual(NO_EFFECT, restored.set_effect_modes["B"])
            self.assertEqual({"A": TWO_PIECE, "B": NO_EFFECT}, restored.get_set_effect_modes())
            self.assertNotIn("C", restored.set_effect_modes)
        app.processEvents()

    def test_priority_save_shows_success_message(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation import role_selector
        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        messages = []
        original_information = role_selector.QMessageBox.information
        role_selector.QMessageBox.information = lambda _parent, title, text: messages.append((title, text))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "priority_config.json"
                selector = RoleSelector(priority_config_path_provider=lambda: path)
                selector.load_roles({"A": {}}, ["S"], [], [])
                selector.selected = ["A"]
                selector.save_priority_config()
        finally:
            role_selector.QMessageBox.information = original_information

        self.assertTrue(messages)
        self.assertIn("保存成功", messages[-1][0])
        self.assertIn("随时读取", messages[-1][1])
        app.processEvents()

    def test_priority_save_button_keeps_success_popup_enabled(self):
        from PySide6.QtWidgets import QApplication, QPushButton

        from src.features.allocation import role_selector
        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        messages = []
        original_information = role_selector.QMessageBox.information
        role_selector.QMessageBox.information = lambda _parent, title, text: messages.append((title, text))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "priority_config.json"
                selector = RoleSelector(priority_config_path_provider=lambda: path)
                selector.load_roles({"A": {}}, ["S"], [], [])
                selector.selected = ["A"]
                save_button = next(
                    button for button in selector.findChildren(QPushButton) if button.text() == "\u4fdd\u5b58"
                )

                save_button.click()
        finally:
            role_selector.QMessageBox.information = original_information

        self.assertTrue(messages)
        self.assertIn("\u4fdd\u5b58\u6210\u529f", messages[-1][0])
        app.processEvents()


class IdentificationWorkflowTests(unittest.TestCase):
    def test_set_combo_data_refreshes_searchable_combo_without_legacy_restore_api(self):
        from PySide6.QtWidgets import QApplication

        from src.features.identification.controller import _set_combo_data
        from src.ui.widgets import SearchableComboBox

        app = QApplication.instance() or QApplication([])
        combo = SearchableComboBox()
        combo.addItem("形状A", "A")

        _set_combo_data(None, combo, "A")

        self.assertEqual("A", combo.currentData())
        app.processEvents()


class ScanPromptWorkflowTests(unittest.TestCase):
    def test_cancel_message_does_not_claim_inventory_was_written(self):
        from src.features.scanning.controller import vision_cancel_message

        message = vision_cancel_message(3)

        self.assertIn("已停止继续解析", message)
        self.assertIn("已解析 3 张", message)
        self.assertNotIn("已入库", message)


class ExecutePageWorkflowTests(unittest.TestCase):
    def test_save_allocation_reload_keeps_current_priority_selector_state(self):
        from src.features.allocation.runner import _save_alloc

        class StateManager:
            def __init__(self):
                self.saved = []

            def save_allocation(self, final_plan, mode=""):
                self.saved.append((final_plan, mode))

        class Window:
            def __init__(self):
                self.final_plan = {"A": {"valid": True}}
                self.state_mgr = StateManager()
                self._pending_strat = "role_priority"
                self._allocation_dirty = True
                self.reload_priority_args = []

            def _load_data(self, reload_priority=True):
                self.reload_priority_args.append(reload_priority)

        window = Window()

        result = _save_alloc(window, show_message=False)

        self.assertTrue(result)
        self.assertFalse(window._allocation_dirty)
        self.assertEqual([({"A": {"valid": True}}, "role_priority")], window.state_mgr.saved)
        self.assertEqual([False], window.reload_priority_args)

    def test_save_allocation_button_keeps_success_popup_enabled(self):
        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QVBoxLayout, QWidget

        from src.features.allocation.execute_page import build_execute_page

        app = QApplication.instance() or QApplication([])

        class FakeRoleSelector(QWidget):
            orderChanged = Signal()

        class Window(QWidget):
            def __init__(self):
                super().__init__()
                self.save_args = []

            def _card(self, _title):
                card = QFrame()
                QVBoxLayout(card)
                return card

            def _on_scan_change(self, *_args):
                pass

            def _on_priority_changed(self, *_args):
                pass

            def _do_exec(self):
                pass

            def _save_alloc(self, show_message=True):
                self.save_args.append(show_message)
                return True

        window = Window()
        scroll = build_execute_page(
            window,
            FakeRoleSelector,
            {},
            {},
            {},
            lambda *_args: None,
        )
        save_button = window.btn_save

        save_button.click()

        self.assertEqual([True], window.save_args)
        self.assertIsNotNone(scroll)
        app.processEvents()

    def test_result_header_grade_uses_full_350_score_even_without_tape(self):
        from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout

        from src.features.allocation import results_view

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.result_card = QFrame()
                self.result_content_layout = QVBoxLayout(self.result_card)
                self.roles_db = {"A": {}}
                self.grade_areas = []
                self._pending_strat = "role_priority"

            def _calc_grade(self, score, area):
                self.grade_areas.append(area)
                return "A"

        window = Window()
        results_view._render_results(
            window,
            {
                "A": {
                    "valid": True,
                    "score": 150.8,
                    "blueprint": {},
                    "assigned_tape": None,
                    "assigned_set_drives": [],
                    "assigned_extra_drives": [],
                }
            },
        )

        self.assertEqual([35], window.grade_areas)
        app.processEvents()

    def test_result_view_marks_added_equipment_from_plan_diff(self):
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

        from src.features.allocation import results_view

        app = QApplication.instance() or QApplication([])
        drive = SimpleNamespace(
            uid="d1",
            shape_id="H_2",
            sub_stats={},
            role_scores={"A": 12.0},
            area=2,
            is_mvp=False,
            pick_order=0,
            quality="Gold",
        )

        class Window:
            def __init__(self):
                self.result_card = QFrame()
                self.result_content_layout = QVBoxLayout(self.result_card)
                self.roles_db = {"A": {"weights": {}}}
                self._pending_strat = "role_priority"
                self.allocation_plan_diff = {
                    "A": {
                        "changed": True,
                        "added_uids": {"d1"},
                        "added": [{"uid": "d1", "display_name": "new drive"}],
                        "removed": [{"uid": "old", "display_name": "old drive"}],
                    }
                }
                self.new_flags = []
                self.header_widgets = []

            def _calc_grade(self, *_args):
                return "A"

            def _section_label(self, text):
                return QLabel(text)

            def _bonus_summary_widget(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, *args, **kwargs):
                self.new_flags.append(kwargs.get("is_new", False))
                return QWidget()

            def _show_plan_diff_dialog(self, *_args, **_kwargs):
                pass

        window = Window()
        results_view._render_results(
            window,
            {
                "A": {
                    "valid": True,
                    "score": 12.0,
                    "blueprint": {},
                    "assigned_tape": None,
                    "assigned_set_drives": [drive],
                    "assigned_extra_drives": [],
                }
            },
        )

        self.assertEqual([True], window.new_flags)
        header_layout = window.result_content_layout.itemAt(0).widget().layout().itemAt(0).layout()
        window.header_widgets = [header_layout.itemAt(i).widget() for i in range(header_layout.count()) if header_layout.itemAt(i).widget()]
        diff_button_index = next(i for i, widget in enumerate(window.header_widgets) if isinstance(widget, QPushButton))
        mode_label_index = next(i for i, widget in enumerate(window.header_widgets) if isinstance(widget, QLabel) and widget.text() == "角色优先")
        score_badge_index = next(i for i, widget in enumerate(window.header_widgets) if isinstance(widget, QFrame) and not isinstance(widget, QLabel) and i > diff_button_index)
        self.assertLess(diff_button_index, mode_label_index)
        self.assertLess(mode_label_index, score_badge_index)
        self.assertLess(diff_button_index, score_badge_index)
        self.assertEqual(window.header_widgets[diff_button_index].sizeHint(), window.header_widgets[diff_button_index].minimumSizeHint())
        app.processEvents()

    def test_result_diff_dialog_renders_equipment_cards(self):
        from PySide6.QtWidgets import QApplication, QWidget

        from src.features.allocation import results_view

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.roles_db = {"A": {"weights": {}}}
                self.card_labels = []

            def _equip_card(self, label, *_args, **_kwargs):
                self.card_labels.append(label)
                return QWidget()

        window = Window()
        dialog = results_view._build_plan_diff_dialog(
            window,
            "A",
            {
                "removed": [{"uid": "old", "type": "drive", "shape_id": "H_2", "sub_stats": {}, "quality": "Gold"}],
                "added": [{"uid": "new", "type": "drive", "shape_id": "V_2", "sub_stats": {}, "quality": "Gold"}],
            },
        )

        self.assertEqual(["H_2"], window.card_labels)
        self.assertIn("配装变动", dialog.windowTitle())
        app.processEvents()

    def test_result_diff_card_hydrates_compact_item_from_saved_equipment(self):
        from src.features.allocation import results_view

        class Window:
            def __init__(self):
                self.roles_db = {"A": {"weights": {}}}
                self.equipped_state = {
                    "A": {
                        "equipped_tape": None,
                        "equipped_drives": [
                            {
                                "uid": "new_drive",
                                "shape_id": "V_2",
                                "sub_stats": {"攻击力%": 2.5, "暴击率%": 2.0},
                                "quality": "Gold",
                                "score": 18.8,
                                "grade": "S",
                                "score_area": 2,
                            }
                        ],
                    }
                }
                self.cards = []

            def _equip_card(self, *args, **kwargs):
                self.cards.append((args, kwargs))
                return None

        window = Window()
        results_view._diff_item_card(window, "A", {"uid": "new_drive", "display_name": "V_2-攻击力%_2.5"}, is_new=True)

        args, kwargs = window.cards[0]
        self.assertEqual("V_2", args[0])
        self.assertEqual({"攻击力%": 2.5, "暴击率%": 2.0}, args[2])
        self.assertEqual("V_2", args[3])
        self.assertEqual((18.8, "S"), args[6])
        self.assertTrue(kwargs["is_new"])

    def test_result_diff_card_hydrates_removed_item_from_inventory_file(self):
        from src.app import runtime
        from src.features.allocation import results_view

        class Window:
            def __init__(self):
                self.roles_db = {"A": {"weights": {}}}
                self.equipped_state = {}
                self.final_plan = None
                self.cards = []

            def _equip_card(self, *args, **kwargs):
                self.cards.append((args, kwargs))
                return None

            def _calc_grade(self, score, area):
                return "S" if score and area else "D"

        with tempfile.TemporaryDirectory() as tmp:
            old_output = getattr(runtime, "OUTPUT_FILE", None)
            runtime.OUTPUT_FILE = Path(tmp) / "real_inventory.json"
            runtime.OUTPUT_FILE.write_text(
                json.dumps(
                    [
                        {
                            "uid": "old_drive",
                            "item_type": "drive",
                            "quality": "Gold",
                            "area": 2,
                            "shape_id": "H_2",
                            "sub_stats": {"伤害%": 2.0, "攻击力": 16.0},
                            "role_scores": {"A": 16.6},
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            try:
                window = Window()
                results_view._diff_item_card(window, "A", {"uid": "old_drive", "display_name": "old"}, is_new=False)
            finally:
                if old_output is None:
                    delattr(runtime, "OUTPUT_FILE")
                else:
                    runtime.OUTPUT_FILE = old_output

        args, kwargs = window.cards[0]
        self.assertEqual("H_2", args[0])
        self.assertEqual({"伤害%": 2.0, "攻击力": 16.0}, args[2])
        self.assertEqual("H_2", args[3])
        self.assertEqual((16.6, "S"), args[6])
        self.assertFalse(kwargs["is_new"])

    def test_saved_equipment_grade_uses_full_350_score_even_without_tape(self):
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

        from src.features.inventory import page as inventory_page

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.equip_content = QFrame()
                self.equip_content_layout = QVBoxLayout(self.equip_content)
                self.equipped_state = {
                    "A": {
                        "equipped_tape": None,
                        "equipped_drives": [
                            {"uid": "d1", "shape_id": "H_2", "sub_stats": {}, "quality": "Gold"},
                        ],
                    }
                }
                self.roles_db = {"A": {"weights": {}}}
                self._shape_areas = {"H_2": 20}
                self.grade_areas = []

            def _score_drive_dict(self, *_args, **_kwargs):
                return 150.8

            def _score_tape_dict(self, *_args, **_kwargs):
                return 0.0

            def _calc_grade(self, score, area):
                self.grade_areas.append(area)
                return "A"

            def _section_label(self, text):
                return QLabel(text)

            def _bonus_summary_widget(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, *_args, **_kwargs):
                return QWidget()

        window = Window()
        inventory_page._refresh_equip(window)

        self.assertIn(35, window.grade_areas)
        self.assertNotIn(20, window.grade_areas[:1])
        app.processEvents()

    def test_saved_equipment_prefers_saved_score_snapshot(self):
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

        from src.features.inventory import page as inventory_page

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.equip_content = QFrame()
                self.equip_content_layout = QVBoxLayout(self.equip_content)
                self.equipped_state = {
                    "A": {
                        "total_score": 188.8,
                        "total_grade": "SS",
                        "score_area": 35,
                        "strategy_mode": "role_priority",
                        "last_diff": {
                            "changed": True,
                            "added": [{"uid": "d1", "display_name": "new drive"}],
                            "removed": [{"uid": "old", "display_name": "old drive"}],
                            "added_uids": ["d1"],
                        },
                        "equipped_tape": None,
                        "equipped_drives": [
                            {
                                "uid": "d1",
                                "shape_id": "H_2",
                                "sub_stats": {},
                                "quality": "Gold",
                                "score": 18.8,
                                "grade": "S",
                                "score_area": 2,
                                "is_new": True,
                            },
                        ],
                    }
                }
                self.roles_db = {"A": {"weights": {}}}
                self._shape_areas = {"H_2": 2}
                self.equip_cards = []
                self.new_flags = []

            def _score_drive_dict(self, *_args, **_kwargs):
                raise AssertionError("saved equipment should use stored score")

            def _score_tape_dict(self, *_args, **_kwargs):
                raise AssertionError("saved equipment should use stored score")

            def _calc_grade(self, *_args, **_kwargs):
                raise AssertionError("saved equipment should use stored grade")

            def _section_label(self, text):
                return QLabel(text)

            def _bonus_summary_widget(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, *args, **_kwargs):
                self.equip_cards.append(args)
                self.new_flags.append(_kwargs.get("is_new", False))
                return QWidget()

        window = Window()
        inventory_page._refresh_equip(window)

        self.assertEqual((18.8, "S"), window.equip_cards[0][6])
        self.assertEqual([True], window.new_flags)
        header_layout = window.equip_content_layout.itemAt(0).widget().layout().itemAt(0).layout()
        widgets = [header_layout.itemAt(i).widget() for i in range(header_layout.count()) if header_layout.itemAt(i).widget()]
        diff_button_index = next(i for i, widget in enumerate(widgets) if isinstance(widget, QPushButton) and widget.text() == "变动")
        mode_label_index = next(i for i, widget in enumerate(widgets) if isinstance(widget, QLabel) and widget.text() == "角色优先")
        score_badge_index = next(i for i, widget in enumerate(widgets) if isinstance(widget, QFrame) and not isinstance(widget, QLabel) and i > diff_button_index)
        self.assertLess(diff_button_index, mode_label_index)
        self.assertLess(mode_label_index, score_badge_index)
        self.assertLess(diff_button_index, score_badge_index)
        app.processEvents()

    def test_state_manager_saves_score_snapshot_for_new_allocations(self):
        from src.models.equipment import Drive, Tape
        from src.optimizer.state_manager import StateManager

        with tempfile.TemporaryDirectory() as tmp:
            manager = StateManager(config_dir=tmp)
            tape = Tape(
                uid="t1",
                quality="Gold",
                area=15,
                set_name="Set",
                main_stats="Main",
                sub_stats={"Sub": 1.0},
                role_scores={"A": 42.0},
            )
            drive = Drive(
                uid="d1",
                quality="Gold",
                area=2,
                shape_id="H_2",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                sub_stats={"Sub": 2.0},
                role_scores={"A": 18.8},
            )

            manager.save_allocation(
                {
                    "A": {
                        "valid": True,
                        "score": 188.8,
                        "blueprint": {"board": [[1]]},
                        "assigned_tape": tape,
                        "assigned_set_drives": [drive],
                        "assigned_extra_drives": [],
                    }
                },
                mode="role_priority",
            )

            saved = json.loads((Path(tmp) / "equipped_state.json").read_text(encoding="utf-8"))

        self.assertEqual(188.8, saved["A"]["total_score"])
        self.assertEqual(35, saved["A"]["score_area"])
        self.assertEqual(42.0, saved["A"]["equipped_tape"]["score"])
        self.assertEqual(15, saved["A"]["equipped_tape"]["score_area"])
        self.assertEqual(18.8, saved["A"]["equipped_drives"][0]["score"])
        self.assertEqual(2, saved["A"]["equipped_drives"][0]["score_area"])
        self.assertNotIn("is_new", saved["A"]["equipped_tape"])
        self.assertNotIn("is_new", saved["A"]["equipped_drives"][0])
        self.assertNotIn("last_diff", saved["A"])

    def test_state_manager_marks_only_changes_against_existing_saved_equipment(self):
        from src.models.equipment import Drive
        from src.optimizer.state_manager import StateManager

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "equipped_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "A": {
                            "equipped_tape": None,
                            "equipped_drives": [
                                {"uid": "old_drive", "display_name": "old drive", "shape_id": "X", "sub_stats": {}}
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager = StateManager(config_dir=tmp)
            new_drive = Drive(
                uid="new_drive",
                quality="Gold",
                area=1,
                shape_id="X",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                sub_stats={},
                role_scores={"A": 10.0},
            )

            manager.save_allocation(
                {
                    "A": {
                        "valid": True,
                        "score": 10.0,
                        "blueprint": {"board": [[1]]},
                        "assigned_tape": None,
                        "assigned_set_drives": [new_drive],
                        "assigned_extra_drives": [],
                    }
                }
            )

            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertTrue(saved["A"]["equipped_drives"][0]["is_new"])
        self.assertEqual(["old_drive"], [item["uid"] for item in saved["A"]["last_diff"]["removed"]])
        self.assertEqual(["new_drive"], [item["uid"] for item in saved["A"]["last_diff"]["added"]])
        self.assertEqual("X", saved["A"]["last_diff"]["removed"][0]["shape_id"])
        self.assertEqual("X", saved["A"]["last_diff"]["added"][0]["shape_id"])
        self.assertEqual(10.0, saved["A"]["last_diff"]["added"][0]["score"])

    def test_state_manager_clears_new_marker_when_same_equipment_is_saved_again(self):
        from src.models.equipment import Drive
        from src.optimizer.state_manager import StateManager

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "equipped_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "A": {
                            "last_diff": {"changed": True},
                            "equipped_tape": None,
                            "equipped_drives": [
                                {
                                    "uid": "same_drive",
                                    "display_name": "same drive",
                                    "shape_id": "X",
                                    "sub_stats": {},
                                    "is_new": True,
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager = StateManager(config_dir=tmp)
            same_drive = Drive(
                uid="same_drive",
                quality="Gold",
                area=1,
                shape_id="X",
                set_name="Set",
                main_stats={"m1": 1, "m2": 1},
                sub_stats={},
                role_scores={"A": 10.0},
            )

            manager.save_allocation(
                {
                    "A": {
                        "valid": True,
                        "score": 10.0,
                        "blueprint": {"board": [[1]]},
                        "assigned_tape": None,
                        "assigned_set_drives": [same_drive],
                        "assigned_extra_drives": [],
                    }
                }
            )

            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertNotIn("is_new", saved["A"]["equipped_drives"][0])
        self.assertNotIn("last_diff", saved["A"])

    def test_plan_diff_marks_added_and_removed_equipment(self):
        from src.models.equipment import Drive, Tape
        from src.optimizer.plan_diff import build_plan_diff

        old_state = {
            "A": {
                "equipped_tape": {"uid": "old_tape", "display_name": "old tape"},
                "equipped_drives": [{"uid": "old_drive", "display_name": "old drive"}],
            }
        }
        new_tape = Tape(
            uid="new_tape",
            quality="Gold",
            area=15,
            set_name="Set",
            main_stats="Main",
            sub_stats={},
            role_scores={"A": 1.0},
        )
        new_drive = Drive(
            uid="new_drive",
            quality="Gold",
            area=2,
            shape_id="H_2",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={},
            role_scores={"A": 2.0},
        )

        diff = build_plan_diff(
            old_state,
            {
                "A": {
                    "valid": True,
                    "assigned_tape": new_tape,
                    "assigned_set_drives": [new_drive],
                    "assigned_extra_drives": [],
                }
            },
        )

        self.assertTrue(diff["A"]["changed"])
        self.assertEqual({"new_tape", "new_drive"}, diff["A"]["added_uids"])
        self.assertEqual(["old_tape", "old_drive"], [item["uid"] for item in diff["A"]["removed"]])
        self.assertEqual(["new_tape", "new_drive"], [item["uid"] for item in diff["A"]["added"]])
        self.assertEqual("Set", diff["A"]["added"][0]["set_name"])
        self.assertEqual("H_2", diff["A"]["added"][1]["shape_id"])
        self.assertEqual(2.0, diff["A"]["added"][1]["score"])

    def test_plan_diff_does_not_mark_new_equipment_when_role_has_no_history(self):
        from src.models.equipment import Drive
        from src.optimizer.plan_diff import build_plan_diff

        drive = Drive(
            uid="new_drive",
            quality="Gold",
            area=1,
            shape_id="X",
            set_name="Set",
            main_stats={"m1": 1, "m2": 1},
            sub_stats={},
            role_scores={"A": 10.0},
        )

        diff = build_plan_diff(
            {},
            {
                "A": {
                    "valid": True,
                    "assigned_tape": None,
                    "assigned_set_drives": [drive],
                    "assigned_extra_drives": [],
                }
            },
        )

        self.assertFalse(diff["A"]["changed"])
        self.assertEqual(set(), diff["A"]["added_uids"])
        self.assertEqual([], diff["A"]["added"])
        self.assertEqual([], diff["A"]["removed"])

    def test_runner_builds_plan_diff_before_rendering_results(self):
        from src.features.allocation import runner

        class State:
            def load_state(self):
                return {"A": {"equipped_tape": None, "equipped_drives": [{"uid": "old"}]}}

        class Button:
            def setEnabled(self, _value):
                pass

            def setText(self, _value):
                pass

        class Window:
            def __init__(self):
                self.state_mgr = State()
                self.btn_run = Button()
                self._allocation_dirty = False
                self.rendered_diff = None

            def _render_results(self, _result):
                self.rendered_diff = self.allocation_plan_diff

        window = Window()
        result = {"A": {"valid": True, "assigned_tape": None, "assigned_set_drives": [], "assigned_extra_drives": []}}

        runner._on_done(window, result)

        self.assertTrue(window.rendered_diff["A"]["changed"])
        self.assertEqual(["old"], [item["uid"] for item in window.rendered_diff["A"]["removed"]])

    def test_console_grade_uses_full_350_score_even_without_tape(self):
        from src.solver import orchestrator as orchestrator_module
        from src.solver.orchestrator import NTEPipelineOrchestrator

        captured = []

        class Scoring:
            def get_grade_tag(self, score, area):
                captured.append((score, area))
                return "A"

        original_display = orchestrator_module.BoardVisualizer.display_final_plan
        orchestrator_module.BoardVisualizer.display_final_plan = (
            lambda **kwargs: captured.append(("display", kwargs["grade"]))
        )
        try:
            orchestrator = object.__new__(NTEPipelineOrchestrator)
            orchestrator.roles_db = {"A": {"default_set": "Set"}}
            orchestrator._render_results(
                {
                    "A": {
                        "valid": True,
                        "score": 150.8,
                        "blueprint": {"board": []},
                        "assigned_tape": None,
                        "assigned_set_drives": [],
                        "assigned_extra_drives": [],
                    }
                },
                Scoring(),
                {},
            )
        finally:
            orchestrator_module.BoardVisualizer.display_final_plan = original_display

        self.assertIn((150.8, 35), captured)
        self.assertNotIn((150.8, 20), captured)


class ScoringScreeningWorkflowTests(unittest.TestCase):
    def _write_scoring_config(self, config_dir: Path):
        roles = {
            "A": {
                "default_set": "Set",
                "extra_shape_label": "X",
                "board_matrix": [[1]],
                "weights": {
                    "Wanted": 1.0,
                    "Other": 2.0,
                    "Sub": 1.0,
                    "H1": 10.0,
                    "H2": 10.0,
                    "Crit": 16.0,
                },
            }
        }
        stats = {
            "gold_base_values": {"Sub": 1.0, "H1": 1.0, "H2": 1.0, "Crit": 1.0},
            "tape_main_stats_pool": ["Wanted", "Other"],
            "tape_main_stat_values": {"Wanted": 1.0, "Other": 1.0},
            "tape_stat_values": {},
            "main_only_keywords": [],
            "stat_alias_mapping": {},
            "benefit_one": {},
            "benefit_alias_mapping": {},
            "weight_pool": [],
        }
        (config_dir / "roles.json").write_text(json.dumps(roles, ensure_ascii=False), encoding="utf-8")
        (config_dir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")

    def test_tape_main_filter_is_applied_before_tape_top_limit(self):
        from src.models.equipment import Tape
        from src.optimizer.scoring import ScoringEngine

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            self._write_scoring_config(config_dir)
            tapes = [
                Tape(uid=f"other_{idx}", quality="Gold", area=15, set_name="Set", main_stats="Other", sub_stats={})
                for idx in range(4)
            ]
            tapes.append(
                Tape(uid="wanted", quality="Gold", area=15, set_name="Set", main_stats="Wanted", sub_stats={"Sub": 1})
            )

            result = ScoringEngine(str(config_dir)).evaluate_global_inventory(
                tapes,
                tape_top_k_per_set_per_role=3,
                tape_main_filters={"A": ["Wanted"]},
            )

            self.assertEqual(["wanted"], [tape.uid for tape in result["tapes"]["A"]])

    def test_stat_priority_is_applied_before_drive_top_limit(self):
        from src.models.equipment import Drive
        from src.optimizer.scoring import ScoringEngine

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            self._write_scoring_config(config_dir)
            drives = [
                Drive(
                    uid=f"high_{idx}",
                    quality="Gold",
                    area=1,
                    shape_id="S1",
                    set_name="Set",
                    main_stats={"m1": 1, "m2": 1},
                    sub_stats={"H1": 1, "H2": 1},
                )
                for idx in range(3)
            ]
            drives.append(
                Drive(
                    uid="crit",
                    quality="Gold",
                    area=1,
                    shape_id="S1",
                    set_name="Set",
                    main_stats={"m1": 1, "m2": 1},
                    sub_stats={"Crit": 1},
                )
            )

            result = ScoringEngine(str(config_dir)).evaluate_global_inventory(
                drives,
                top_k_per_shape_per_role=3,
                crit_priority_modes={"A": {"stats": ["Crit"], "equal_priority": False}},
            )

            self.assertIn("crit", [drive.uid for drive in result["drives"]])

    def test_orchestrator_reads_largest_priority_group_size(self):
        from src.solver.orchestrator import NTEPipelineOrchestrator

        orchestrator = object.__new__(NTEPipelineOrchestrator)

        self.assertEqual(1, orchestrator._max_priority_group_size(["A", "B"], None))
        self.assertEqual(
            3,
            orchestrator._max_priority_group_size(
                ["A", "B", "C", "D"],
                [["A", "B", "C"], ["D"]],
            ),
        )


class OfflineParseWorkflowTests(unittest.TestCase):
    def test_all_offline_scope_replaces_inventory(self):
        from src.features.scanning.controller import offline_scope_replaces_inventory

        self.assertTrue(offline_scope_replaces_inventory("all"))
        self.assertTrue(offline_scope_replaces_inventory("full"))
        self.assertFalse(offline_scope_replaces_inventory("incremental"))


class ConfigDraftWorkflowTests(unittest.TestCase):
    def test_config_form_changes_are_draft_until_save_button(self):
        from src.features.configuration import page as config_page

        class Window:
            _current_config_name = "roles.json"

            def __init__(self):
                self.loaded = False

            def _load_data(self):
                self.loaded = True

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            path = config_dir / "roles.json"
            path.write_text(json.dumps({"Old": {"weights": {}}}, ensure_ascii=False), encoding="utf-8")
            window = Window()

            config_page.save_config_data(window, {"New": {"weights": {}}}, config_dir)
            self.assertEqual({"Old": {"weights": {}}}, json.loads(path.read_text(encoding="utf-8")))
            self.assertTrue(window._config_dirty)

            original_information = config_page.QMessageBox.information
            config_page.QMessageBox.information = lambda *_args, **_kwargs: None
            try:
                config_page.save_config_form(window, config_dir, None)
            finally:
                config_page.QMessageBox.information = original_information
            self.assertEqual({"New": {"weights": {}}}, json.loads(path.read_text(encoding="utf-8")))
            self.assertFalse(window._config_dirty)
            self.assertTrue(window.loaded)

    def test_config_loader_reads_file_every_time_when_not_dirty(self):
        from src.features.configuration.page import load_config_data

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            path = config_dir / "roles.json"
            path.write_text(json.dumps({"A": {}}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual({"A": {}}, load_config_data("roles.json", config_dir))

            path.write_text(json.dumps({"B": {}}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual({"B": {}}, load_config_data("roles.json", config_dir))

    def test_roles_form_lazily_builds_role_tabs(self):
        from PySide6.QtWidgets import QApplication, QTabWidget, QVBoxLayout, QWidget

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

            def _del_role(self, *_args):
                pass

            def _add_weight(self, *_args):
                pass

            def _del_weight(self, *_args):
                pass

        data = {
            "A": {"default_set": "套装A", "extra_shape_buffs": {}, "board_matrix": [[0] * 5 for _ in range(5)], "weights": {}},
            "B": {"default_set": "套装A", "extra_shape_buffs": {}, "board_matrix": [[0] * 5 for _ in range(5)], "weights": {}},
        }
        window = Window()
        config_page.render_roles_form(window, data)
        tabs = window.container.findChild(QTabWidget)

        self.assertIsNotNone(tabs)
        self.assertTrue(tabs.widget(0).property("loaded"))
        self.assertFalse(tabs.widget(1).property("loaded"))

        tabs.setCurrentIndex(1)
        app.processEvents()

        self.assertTrue(tabs.widget(1).property("loaded"))

    def test_roles_form_can_open_newly_added_role_tab(self):
        from PySide6.QtWidgets import QApplication, QTabWidget, QVBoxLayout, QWidget

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

            def _del_role(self, *_args):
                pass

            def _add_weight(self, *_args):
                pass

            def _del_weight(self, *_args):
                pass

        data = {
            "A": {"default_set": "套装A", "extra_shape_buffs": {}, "board_matrix": [[0] * 5 for _ in range(5)], "weights": {}},
            "新角色": {"default_set": "套装A", "extra_shape_buffs": {}, "board_matrix": [[0] * 5 for _ in range(5)], "weights": {}},
        }
        window = Window()
        config_page.render_roles_form(window, data, active_role="新角色")
        tabs = window.container.findChild(QTabWidget)

        self.assertEqual("新角色", tabs.tabText(tabs.currentIndex()))
        app.processEvents()

    def test_confirm_pending_config_changes_can_cancel_navigation(self):
        from src.features.configuration import page as config_page

        class Window:
            _current_config_name = "roles.json"
            _config_dirty = True

        original_question = config_page.QMessageBox.question
        config_page.QMessageBox.question = lambda *_args, **_kwargs: config_page.QMessageBox.Cancel
        try:
            self.assertFalse(config_page.confirm_pending_config_changes(Window(), Path(".")))
        finally:
            config_page.QMessageBox.question = original_question


class AccountTransferWorkflowTests(unittest.TestCase):
    def _make_manager(self, root: Path):
        from src.features.accounts.manager import AccountManager

        return AccountManager(
            data_root=root,
            bundled_config_dir=root / "bundled",
            iter_image_files=lambda path: [p for p in path.rglob("*") if p.is_file()],
            core_config_files=("roles.json", "sets.json", "stats.json", "shapes.json"),
            account_user_files=("equipped_state.json", "real_inventory.json", "priority_config.json"),
        )

    def test_export_current_account_includes_only_baseline_screenshot(self):
        from src.features.accounts.manager import export_account_data

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = self._make_manager(root)
            account_id = manager.create_account("Main")
            account_root = manager.account_dir(account_id)
            (account_root / "config" / "real_inventory.json").write_text("[1]", encoding="utf-8")
            (account_root / "scanned_images" / "raw_drive_0001.png").write_bytes(b"baseline")
            (account_root / "scanned_images" / "raw_drive_0002.png").write_bytes(b"extra")

            zip_path = root / "main-export.zip"
            export_account_data(manager, account_id, zip_path)

            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("account/config/real_inventory.json", names)
            self.assertIn("account/scanned_images/raw_drive_0001.png", names)
            self.assertNotIn("account/scanned_images/raw_drive_0002.png", names)

    def test_import_account_with_same_name_replaces_existing_account(self):
        from src.features.accounts.manager import export_account_data, import_account_data

        with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as dst_tmp:
            src_root = Path(src_tmp)
            src_manager = self._make_manager(src_root)
            src_id = src_manager.create_account("Main")
            (src_manager.account_dir(src_id) / "config" / "real_inventory.json").write_text(
                "[{\"uid\":\"new\"}]", encoding="utf-8"
            )
            export_path = src_root / "main.zip"
            export_account_data(src_manager, src_id, export_path)

            dst_root = Path(dst_tmp)
            dst_manager = self._make_manager(dst_root)
            dst_id = dst_manager.create_account("Main")
            (dst_manager.account_dir(dst_id) / "config" / "real_inventory.json").write_text(
                "[{\"uid\":\"old\"}]", encoding="utf-8"
            )

            imported_id = import_account_data(dst_manager, export_path)
            imported_inventory = json.loads(
                (dst_manager.account_dir(imported_id) / "config" / "real_inventory.json").read_text(encoding="utf-8")
            )

            self.assertEqual(dst_id, imported_id)
            self.assertEqual([{"uid": "new"}], imported_inventory)


if __name__ == "__main__":
    unittest.main()
