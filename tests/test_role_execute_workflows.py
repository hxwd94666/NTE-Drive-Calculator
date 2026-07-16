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

class IdentifyTempFileWorkflowTests(unittest.TestCase):
    def test_identify_clipboard_cleanup_removes_only_generated_account_root_files(self):
        from src.features.identification.temp_files import (
            cleanup_identify_clipboard_files,
            iter_identify_clipboard_files,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = root / "identify_clipboard_123.png"
            other_png = root / "other.png"
            nested = root / "nested"
            nested.mkdir()
            nested_generated = nested / "identify_clipboard_456.png"
            for path in (generated, other_png, nested_generated):
                path.write_bytes(b"png")

            self.assertEqual([generated], iter_identify_clipboard_files(root))
            removed = cleanup_identify_clipboard_files([generated, other_png, nested_generated], root)

            self.assertEqual(1, removed)
            self.assertFalse(generated.exists())
            self.assertTrue(other_png.exists())
            self.assertTrue(nested_generated.exists())

    def test_identify_finished_cleans_pending_clipboard_paths_from_input(self):
        from src.app import runtime
        from src.features.identification import controller

        class PathEdit:
            def __init__(self, text):
                self.value = text

            def setText(self, text):
                self.value = text

            def text(self):
                return self.value

        class Window:
            def __init__(self, text):
                self.ident_path_edit = PathEdit(text)
                self._pending_identify_clipboard_cleanup = []

            def _identify_paths_from_text(self):
                return controller.parse_identify_paths(self.ident_path_edit.text())

        with tempfile.TemporaryDirectory() as tmp:
            old_root = getattr(runtime, "ACCOUNT_DATA_ROOT", None)
            root = Path(tmp)
            runtime.ACCOUNT_DATA_ROOT = root
            generated = root / "identify_clipboard_123.png"
            selected = root / "manual.png"
            generated.write_bytes(b"png")
            selected.write_bytes(b"png")
            window = Window(f"{generated};{selected}")
            window._pending_identify_clipboard_cleanup = [generated]
            try:
                controller._cleanup_pending_identify_clipboard_files(window)
            finally:
                if old_root is not None:
                    runtime.ACCOUNT_DATA_ROOT = old_root

            self.assertFalse(generated.exists())
            self.assertTrue(selected.exists())
            self.assertEqual(str(selected), window.ident_path_edit.text())


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

    def test_role_selector_persists_weapon_and_crit_rate_limit_with_old_config_compatibility(self):
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "priority_config.json"
            selector = RoleSelector(priority_config_path_provider=lambda: path)
            selector.load_roles(
                {"A": {}, "B": {}},
                ["S"],
                [],
                ["暴击率%"],
                weapons_db={"弧盘A": {"level_sub_stats": {"80": {"暴击率%": 24.0}}}},
            )
            selector.selected = ["A", "B"]
            selector._set_custom_weapon("A", "弧盘A")
            self.assertEqual({"A": 76.0}, selector.get_crit_rate_caps())
            selector.weapons_db["弧盘B"] = {"level_sub_stats": {"80": {"暴击率%": 12.5}}}
            selector._set_custom_weapon("A", "弧盘B")
            self.assertEqual({"A": 87.5}, selector.get_crit_rate_caps())
            selector._set_crit_rate_cap("A", 88.8)
            selector.save_priority_config(show_message=False)

            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({"A": "弧盘B"}, data["custom_weapons"])
            self.assertEqual({"A": 88.8}, data["crit_rate_caps"])

            restored = RoleSelector(priority_config_path_provider=lambda: path)
            restored.load_roles({"A": {}, "B": {}}, ["S"], [], ["暴击率%"], weapons_db={"弧盘B": {}})
            restored.load_priority_config()

            self.assertEqual({"A": "弧盘B"}, restored.get_custom_weapons())
            self.assertEqual({"A": 88.8}, restored.get_crit_rate_caps())

            path.write_text(
                json.dumps({"priority_list": ["A"], "custom_sets": {"A": "S"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            restored.load_priority_config()
            self.assertEqual({}, restored.get_custom_weapons())
            self.assertEqual({}, restored.get_crit_rate_caps())
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
    def setUp(self):
        from src.app import runtime

        self._old_user_config_dir = getattr(runtime, "USER_CONFIG_DIR", None)
        runtime.USER_CONFIG_DIR = None

    def tearDown(self):
        from src.app import runtime

        runtime.USER_CONFIG_DIR = self._old_user_config_dir

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

    def test_save_allocation_syncs_equipment_lock_to_role_feature(self):
        from src.features.allocation import runner

        class StateManager:
            def save_allocation(self, _final_plan, mode=""):
                self.mode = mode

        class Window:
            def __init__(self):
                self.final_plan = {"A": {"valid": True}}
                self.state_mgr = StateManager()
                self._pending_strat = "role_priority"
                self._allocation_dirty = True
                self.equipped_state = {"A": {"equipped_drives": [{"uid": "d1"}]}}
                self._my_role_form_data = {"A": {"old": True}}

            def _load_data(self, reload_priority=True):
                self.reload_priority = reload_priority

        calls = []
        old_import = runner.import_all_role_equipment
        runner.import_all_role_equipment = lambda state: calls.append(state) or {
            "imported": 1,
            "skipped": 0,
            "failed": [],
            "my_roles": {"A": {"drive": {"drives": [{"uid": "d1"}]}}},
        }
        try:
            window = Window()
            self.assertTrue(runner._save_alloc(window, show_message=False))
        finally:
            runner.import_all_role_equipment = old_import

        self.assertEqual([{"A": {"equipped_drives": [{"uid": "d1"}]}}], calls)
        self.assertEqual({"drive": {"drives": [{"uid": "d1"}]}}, window._my_role_form_data["A"])

    def test_execute_page_shows_post_action_manager_only_for_full_scan(self):
        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

        from src.features.allocation.execute_page import build_execute_page
        from src.features.scanning.controller import _on_scan_change

        app = QApplication.instance() or QApplication([])

        class FakeRoleSelector(QWidget):
            orderChanged = Signal()

        class Window(QWidget):
            def _card(self, _title):
                card = QFrame()
                QVBoxLayout(card)
                return card

            def _on_scan_change(self, scan_id):
                _on_scan_change(self, scan_id)

            def _on_priority_changed(self, *_args):
                pass

            def _do_exec(self):
                pass

            def _save_alloc(self, show_message=True):
                return True

        window = Window()
        help_calls = []
        scroll = build_execute_page(window, FakeRoleSelector, {}, {}, {}, lambda *args: help_calls.append(args))

        self.assertEqual("管理", window.scan_post_action_btn.text())
        self.assertTrue(window.total_count_frame.isHidden())

        window._on_scan_change(1)
        self.assertFalse(window.total_count_frame.isHidden())
        self.assertTrue(window.scan_post_action_btn.isEnabled())

        window._on_scan_change(4)
        self.assertTrue(window.total_count_frame.isHidden())
        self.assertIsNotNone(scroll)
        self.assertEqual([], help_calls)
        app.processEvents()

    def test_scan_post_action_defaults_match_plan(self):
        from src.features.scanning.post_action_dialog import load_scan_post_action_config

        with tempfile.TemporaryDirectory() as tmp:
            config = load_scan_post_action_config(Path(tmp))

        self.assertEqual("default", config["server_region"])
        self.assertFalse(config["discard"]["enabled"])
        self.assertEqual("S", config["discard"]["grade"])
        self.assertEqual("all", config["discard"]["role_scope"])
        self.assertEqual("gold_purple", config["discard"]["quality_scope"])
        self.assertEqual("all", config["discard"]["type_scope"])
        self.assertIsNone(config["discard"]["shape_ids"])
        self.assertIsNone(config["discard"]["set_names"])
        self.assertEqual("skip", config["discard"]["on_locked"])
        self.assertEqual("normal", config["discard"]["on_discarded"])
        self.assertFalse(config["lock"]["enabled"])
        self.assertEqual("SSS", config["lock"]["grade"])
        self.assertEqual("all", config["lock"]["role_scope"])
        self.assertEqual("gold_purple", config["lock"]["quality_scope"])
        self.assertEqual("all", config["lock"]["type_scope"])
        self.assertIsNone(config["lock"]["shape_ids"])
        self.assertIsNone(config["lock"]["set_names"])
        self.assertEqual("skip", config["lock"]["on_locked"])
        self.assertEqual("normal", config["lock"]["on_discarded"])

    def test_scan_post_action_dialog_collects_preserve_rules(self):
        from PySide6.QtWidgets import QApplication

        from src.features.scanning.post_action_dialog import ScanPostActionDialog

        app = QApplication.instance() or QApplication([])
        rule = {
            "enabled": True,
            "name": "双爆卡带",
            "item_type": "tape",
            "action": "keep",
            "main_stats": ["攻击力%"],
            "sub_stats": ["暴击率%", "暴击伤害%"],
            "sub_match": "all",
            "quality_scope": "gold_purple",
            "shape_ids": None,
            "set_names": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            dialog = ScanPostActionDialog(None, Path(tmp))
            self.assertEqual(["评分处理", "预留规则"], [dialog._main_tabs.tabText(index) for index in range(2)])
            dialog._preserve_rules = [rule]
            expected_rule = dict(rule, required_sub_stats=[], sub_match=2)
            self.assertEqual([expected_rule], dialog._collect_config()["preserve_rules"])
            dialog.close()
        app.processEvents()

    def test_scan_post_action_config_passes_to_full_scan(self):
        from src.features.scanning import controller
        from src.storage.json_store import write_json
        from src.app import runtime

        original_information = controller.QMessageBox.information
        original_user_config_dir = runtime.USER_CONFIG_DIR
        controller.QMessageBox.information = lambda *_args, **_kwargs: None
        try:
            class RoleSelector:
                def get_selected(self):
                    return []

                def get_custom_sets(self):
                    return {}

                def get_tape_main_filters(self):
                    return {}

                def get_crit_priority_modes(self):
                    return {}

                def get_crit_rate_caps(self):
                    return {}

                def get_set_effect_modes(self):
                    return {}

                def get_priority_groups(self):
                    return None

            class ScanGroup:
                def checkedId(self):
                    return 1

            class CountEdit:
                def text(self):
                    return "10"

            class Window:
                def __init__(self):
                    self.role_selector = RoleSelector()
                    self.scan_group = ScanGroup()
                    self.total_count_edit = CountEdit()
                    self.scan_dual_thread_check = SimpleNamespace(isChecked=lambda: True)
                    self.scan_discrete_gpu_check = SimpleNamespace(isChecked=lambda: False)
                    self.strategy_group = SimpleNamespace(checkedId=lambda: 0)
                    self.btn_run = SimpleNamespace(setEnabled=lambda _value: None, setText=lambda _text: None)
                    self.result_card = SimpleNamespace(setVisible=lambda _value: None)
                    self.scan_args = []

                def _start_gamepad_scan(
                    self,
                    total_drives,
                    post_actions_config=None,
                    selected_roles=None,
                    parse_during_scan=True,
                    discrete_gpu_acceleration=False,
                    amd_compatibility=False,
                ):
                    self.scan_args.append(
                        (
                            total_drives,
                            post_actions_config,
                            selected_roles,
                            parse_during_scan,
                            discrete_gpu_acceleration,
                            amd_compatibility,
                        )
                    )

                def _confirm_unsaved_allocation_before_recompute(self):
                    return True

            with tempfile.TemporaryDirectory() as tmp:
                runtime.USER_CONFIG_DIR = Path(tmp)
                write_json(
                    Path(tmp) / "scan_post_actions.json",
                    {
                        "discard": {"enabled": True, "grade": "SS"},
                        "lock": {"enabled": True, "grade": "SSS"},
                    },
                )
                window = Window()
                controller._do_exec(window)
        finally:
            controller.QMessageBox.information = original_information
            runtime.USER_CONFIG_DIR = original_user_config_dir

        total, config, selected_roles, parse_during_scan, discrete_gpu_acceleration, amd_compatibility = window.scan_args[0]
        self.assertEqual(10, total)
        self.assertTrue(config["discard"]["enabled"])
        self.assertEqual("SS", config["discard"]["grade"])
        self.assertTrue(config["lock"]["enabled"])
        self.assertEqual("SSS", config["lock"]["grade"])
        self.assertEqual([], selected_roles)
        self.assertTrue(parse_during_scan)
        self.assertFalse(discrete_gpu_acceleration)
        self.assertFalse(amd_compatibility)

    def test_full_scan_dual_thread_checkbox_can_disable_streaming_parse(self):
        from src.features.scanning import controller
        from src.app import runtime

        original_information = controller.QMessageBox.information
        original_user_config_dir = runtime.USER_CONFIG_DIR
        controller.QMessageBox.information = lambda *_args, **_kwargs: None
        try:
            class RoleSelector:
                def get_selected(self):
                    return []

                def get_custom_sets(self):
                    return {}

                def get_tape_main_filters(self):
                    return {}

                def get_crit_priority_modes(self):
                    return {}

                def get_crit_rate_caps(self):
                    return {}

                def get_set_effect_modes(self):
                    return {}

                def get_priority_groups(self):
                    return None

            class ScanGroup:
                def checkedId(self):
                    return 1

            class CountEdit:
                def text(self):
                    return "10"

            class Window:
                def __init__(self):
                    self.role_selector = RoleSelector()
                    self.scan_group = ScanGroup()
                    self.total_count_edit = CountEdit()
                    self.scan_dual_thread_check = SimpleNamespace(isChecked=lambda: False)
                    self.scan_discrete_gpu_check = SimpleNamespace(isChecked=lambda: False)
                    self.strategy_group = SimpleNamespace(checkedId=lambda: 0)
                    self.btn_run = SimpleNamespace(setEnabled=lambda _value: None, setText=lambda _text: None)
                    self.result_card = SimpleNamespace(setVisible=lambda _value: None)
                    self.parse_during_scan = None

                def _start_gamepad_scan(
                    self,
                    total_drives,
                    post_actions_config=None,
                    selected_roles=None,
                    parse_during_scan=True,
                    discrete_gpu_acceleration=False,
                    amd_compatibility=False,
                ):
                    self.parse_during_scan = parse_during_scan

                def _confirm_unsaved_allocation_before_recompute(self):
                    return True

            with tempfile.TemporaryDirectory() as tmp:
                runtime.USER_CONFIG_DIR = Path(tmp)
                window = Window()
                controller._do_exec(window)
        finally:
            controller.QMessageBox.information = original_information
            runtime.USER_CONFIG_DIR = original_user_config_dir

        self.assertFalse(window.parse_during_scan)

    def test_full_scan_discrete_gpu_checkbox_requests_acceleration(self):
        from src.features.scanning import controller
        from src.app import runtime

        original_information = controller.QMessageBox.information
        original_user_config_dir = runtime.USER_CONFIG_DIR
        controller.QMessageBox.information = lambda *_args, **_kwargs: None
        try:
            class RoleSelector:
                def get_selected(self):
                    return []

                def get_custom_sets(self):
                    return {}

                def get_tape_main_filters(self):
                    return {}

                def get_crit_priority_modes(self):
                    return {}

                def get_crit_rate_caps(self):
                    return {}

                def get_set_effect_modes(self):
                    return {}

                def get_priority_groups(self):
                    return None

            class Window:
                def __init__(self):
                    self.role_selector = RoleSelector()
                    self.scan_group = SimpleNamespace(checkedId=lambda: 1)
                    self.total_count_edit = SimpleNamespace(text=lambda: "10")
                    self.scan_dual_thread_check = SimpleNamespace(isChecked=lambda: True)
                    self.scan_discrete_gpu_check = SimpleNamespace(isChecked=lambda: True)
                    self.strategy_group = SimpleNamespace(checkedId=lambda: 0)
                    self.btn_run = SimpleNamespace(setEnabled=lambda _value: None, setText=lambda _text: None)
                    self.result_card = SimpleNamespace(setVisible=lambda _value: None)
                    self.discrete_gpu_acceleration = None

                def _start_gamepad_scan(
                    self,
                    total_drives,
                    post_actions_config=None,
                    selected_roles=None,
                    parse_during_scan=True,
                    discrete_gpu_acceleration=False,
                    amd_compatibility=False,
                ):
                    self.discrete_gpu_acceleration = discrete_gpu_acceleration

                def _confirm_unsaved_allocation_before_recompute(self):
                    return True

            with tempfile.TemporaryDirectory() as tmp:
                runtime.USER_CONFIG_DIR = Path(tmp)
                window = Window()
                controller._do_exec(window)
        finally:
            controller.QMessageBox.information = original_information
            runtime.USER_CONFIG_DIR = original_user_config_dir

        self.assertTrue(window.discrete_gpu_acceleration)

    def test_full_scan_amd_compatibility_forces_low_load_options(self):
        from src.features.scanning import controller
        from src.app import runtime

        original_information = controller.QMessageBox.information
        original_user_config_dir = runtime.USER_CONFIG_DIR
        controller.QMessageBox.information = lambda *_args, **_kwargs: None
        try:
            class RoleSelector:
                def get_selected(self):
                    return []

                def get_custom_sets(self):
                    return {}

                def get_tape_main_filters(self):
                    return {}

                def get_crit_priority_modes(self):
                    return {}

                def get_crit_rate_caps(self):
                    return {}

                def get_set_effect_modes(self):
                    return {}

                def get_priority_groups(self):
                    return None

            class Window:
                def __init__(self):
                    self.role_selector = RoleSelector()
                    self.scan_group = SimpleNamespace(checkedId=lambda: 1)
                    self.total_count_edit = SimpleNamespace(text=lambda: "10")
                    self.scan_dual_thread_check = SimpleNamespace(isChecked=lambda: True)
                    self.scan_discrete_gpu_check = SimpleNamespace(isChecked=lambda: True)
                    self.scan_amd_compat_check = SimpleNamespace(isChecked=lambda: True)
                    self.strategy_group = SimpleNamespace(checkedId=lambda: 0)
                    self.btn_run = SimpleNamespace(setEnabled=lambda _value: None, setText=lambda _text: None)
                    self.result_card = SimpleNamespace(setVisible=lambda _value: None)
                    self.scan_args = None

                def _start_gamepad_scan(
                    self,
                    total_drives,
                    post_actions_config=None,
                    selected_roles=None,
                    parse_during_scan=True,
                    discrete_gpu_acceleration=False,
                    amd_compatibility=False,
                ):
                    self.scan_args = (
                        parse_during_scan,
                        discrete_gpu_acceleration,
                        amd_compatibility,
                    )

                def _confirm_unsaved_allocation_before_recompute(self):
                    return True

            with tempfile.TemporaryDirectory() as tmp:
                runtime.USER_CONFIG_DIR = Path(tmp)
                window = Window()
                controller._do_exec(window)
        finally:
            controller.QMessageBox.information = original_information
            runtime.USER_CONFIG_DIR = original_user_config_dir

        self.assertEqual((False, False, True), window.scan_args)

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
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

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

            def _calc_grade(self, *_args):
                return "A"

            def _score_tape_dict(self, *_args, **_kwargs):
                return 10.0

            def _score_drive_dict(self, *_args, **_kwargs):
                return 10.0

            def _section_label(self, text):
                return QLabel(text)

            def _role_stat_priority_stats(self, *_args, **_kwargs):
                return []

            def _role_bonus_summary_panel(self, *_args, **_kwargs):
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
        app.processEvents()

    def test_result_view_passes_role_replacement_change_marker_to_cards(self):
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

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
            is_changed=True,
        )
        tape = SimpleNamespace(
            uid="t1",
            set_name="套装A",
            main_stats="攻击力%",
            sub_stats={},
            role_scores={"A": 10.0},
            quality="Gold",
            is_changed=True,
        )

        class Window:
            def __init__(self):
                self.result_card = QFrame()
                self.result_content_layout = QVBoxLayout(self.result_card)
                self.roles_db = {"A": {"weights": {}}}
                self._pending_strat = "role_priority"
                self.allocation_plan_diff = {"A": {"changed": True, "added_uids": {"t1", "d1"}}}
                self.changed_flags = []
                self.new_flags = []

            def _calc_grade(self, *_args):
                return "A"

            def _score_tape_dict(self, *_args, **_kwargs):
                return 10.0

            def _score_drive_dict(self, *_args, **_kwargs):
                return 10.0

            def _section_label(self, text):
                return QLabel(text)

            def _role_stat_priority_stats(self, *_args, **_kwargs):
                return []

            def _role_bonus_summary_panel(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, *args, **kwargs):
                self.changed_flags.append(kwargs.get("is_changed", False))
                self.new_flags.append(kwargs.get("is_new", False))
                return QWidget()

        window = Window()
        results_view._render_results(
            window,
            {
                "A": {
                    "valid": True,
                    "score": 22.0,
                    "blueprint": {},
                    "assigned_tape": tape,
                    "assigned_set_drives": [drive],
                    "assigned_extra_drives": [],
                }
            },
        )

        self.assertEqual([True, True], window.changed_flags)
        self.assertEqual([False, False], window.new_flags)
        app.processEvents()

    def test_role_drive_detail_marks_only_role_replacement_changes(self):
        from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

        from src.app import runtime
        from src.features.role import drive_widget

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.cards = []
                self._shape_areas = {"H_2": 2}

            def _equip_card(self, *args, **kwargs):
                self.cards.append((args, kwargs))
                return QLabel(str(args[0]))

            def _score_drive_dict(self, *_args, **_kwargs):
                return 10.0

            def _score_tape_dict(self, *_args, **_kwargs):
                return 20.0

            def _calc_grade(self, *_args, **_kwargs):
                return "A"

            def _role_stat_priority_stats(self, *_args, **_kwargs):
                return []

            def _role_bonus_summary_panel(self, *_args, **_kwargs):
                return QWidget()

        window = Window()
        parent = QWidget()
        layout = QVBoxLayout(parent)
        role_data = {
            "weights": {},
            "tape": {
                "uid": "t1",
                "set_name": "套装A",
                "main_stats": {"攻击力%": 30.0},
                "sub_stats": {},
                "quality": "Gold",
                "is_changed": True,
            },
            "drive": {
                "blueprint_layout": [],
                "drives": [
                    {
                        "uid": "d1",
                        "shape_id": "H_2",
                        "sub_stats": {},
                        "quality": "Gold",
                        "is_changed": True,
                    }
                ],
            },
        }

        old_config_dir = getattr(runtime, "CONFIG_DIR", None)
        runtime.CONFIG_DIR = Path("F:/NTE/config")
        try:
            drive_widget._build_drive_detail_content(window, layout, "A", [], role_data["drive"]["drives"], role_data["drive"]["drives"], role_data)
        finally:
            if old_config_dir is None:
                delattr(runtime, "CONFIG_DIR")
            else:
                runtime.CONFIG_DIR = old_config_dir

        self.assertEqual([True, True], [kwargs.get("is_changed", False) for _args, kwargs in window.cards])
        self.assertEqual([False, False], [kwargs.get("is_new", False) for _args, kwargs in window.cards])
        self.assertEqual(["inventory", "inventory"], [kwargs.get("card_variant") for _args, kwargs in window.cards])
        app.processEvents()

    def test_role_drive_detail_scores_with_dynamic_weights_and_base_fallback_per_stat(self):
        from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

        from src.app import runtime
        from src.features.role import drive_widget

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.roles_db = {"A": {"weights": {"攻击力白值": 1.0, "攻击力%": 1.0, "环合强度": 2.0}}}
                self.used_weights = []
                self._shape_areas = {"H_2": 2}

            def _equip_card(self, *_args, **_kwargs):
                return QLabel("card")

            def _score_drive_dict(self, _sub_stats, _shape_id, weights, _quality):
                self.used_weights.append(weights)
                return 1.0

            def _score_tape_dict(self, _main_stat, _sub_stats, weights, _quality):
                self.used_weights.append(weights)
                return 1.0

            def _calc_grade(self, *_args, **_kwargs):
                return "A"

        window = Window()
        parent = QWidget()
        layout = QVBoxLayout(parent)
        role_data = {
            "weights": {"stale_my_role_weight": 1.0},
            "sub_stats": {
                "攻击力白值": 100.0,
                "攻击力%": 10.0,
                "暴击率%": 5.0,
                "暴击伤害%": 50.0,
                "伤害增加%": 1.0,
            },
            "tape": {
                "uid": "t1",
                "set_name": "套装A",
                "main_stats": {"攻击力%": 30.0},
                "sub_stats": {},
                "quality": "Gold",
            },
            "drive": {
                "blueprint_layout": [],
                "drives": [
                    {
                        "uid": "d1",
                        "shape_id": "H_2",
                        "sub_stats": {"攻击力": 1.0},
                        "quality": "Gold",
                    }
                ],
            },
        }

        old_config_dir = getattr(runtime, "CONFIG_DIR", None)
        runtime.CONFIG_DIR = Path("F:/NTE/config")
        try:
            drive_widget._build_drive_detail_content(
                window,
                layout,
                "A",
                [],
                role_data["drive"]["drives"],
                role_data["drive"]["drives"],
                role_data,
            )
        finally:
            if old_config_dir is None:
                delattr(runtime, "CONFIG_DIR")
            else:
                runtime.CONFIG_DIR = old_config_dir

        self.assertTrue(window.used_weights)
        self.assertTrue(all("stale_my_role_weight" not in weights for weights in window.used_weights))
        self.assertTrue(all(set(weights) == {"攻击力白值", "攻击力%", "环合强度"} for weights in window.used_weights))
        self.assertTrue(all(weights["攻击力白值"] != 1.0 for weights in window.used_weights))
        self.assertTrue(all(weights["环合强度"] == 2.0 for weights in window.used_weights))
        app.processEvents()

    def test_role_drive_detail_scores_with_roles_db_weights_when_damage_is_unavailable(self):
        from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

        from src.app import runtime
        from src.features.role import drive_widget

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.roles_db = {"A": {"weights": {"roles_weight": 9.0}}}
                self.used_weights = []
                self._shape_areas = {"H_2": 2}

            def _equip_card(self, *_args, **_kwargs):
                return QLabel("card")

            def _score_drive_dict(self, _sub_stats, _shape_id, weights, _quality):
                self.used_weights.append(weights)
                return 1.0

            def _score_tape_dict(self, _main_stat, _sub_stats, weights, _quality):
                self.used_weights.append(weights)
                return 1.0

            def _calc_grade(self, *_args, **_kwargs):
                return "A"

        window = Window()
        parent = QWidget()
        layout = QVBoxLayout(parent)
        role_data = {
            "weights": {"stale_my_role_weight": 1.0},
            "tape": {
                "uid": "t1",
                "set_name": "套装A",
                "main_stats": {"攻击力%": 30.0},
                "sub_stats": {},
                "quality": "Gold",
            },
            "drive": {
                "blueprint_layout": [],
                "drives": [
                    {
                        "uid": "d1",
                        "shape_id": "H_2",
                        "sub_stats": {"攻击力": 1.0},
                        "quality": "Gold",
                    }
                ],
            },
        }

        old_config_dir = getattr(runtime, "CONFIG_DIR", None)
        runtime.CONFIG_DIR = Path("F:/NTE/config")
        try:
            drive_widget._build_drive_detail_content(
                window,
                layout,
                "A",
                [],
                role_data["drive"]["drives"],
                role_data["drive"]["drives"],
                role_data,
            )
        finally:
            if old_config_dir is None:
                delattr(runtime, "CONFIG_DIR")
            else:
                runtime.CONFIG_DIR = old_config_dir

        self.assertTrue(window.used_weights)
        self.assertTrue(all(weights == {"roles_weight": 9.0} for weights in window.used_weights))
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

        self.assertEqual(["H_2", "V_2"], window.card_labels)
        self.assertIn("配装变动", dialog.windowTitle())
        app.processEvents()

    def test_result_diff_dialog_pairs_drives_by_area_when_families_differ(self):
        from PySide6.QtWidgets import QApplication, QLabel, QWidget

        from src.features.allocation import results_view

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.roles_db = {"A": {"weights": {}}}
                self._shape_areas = {"L_3_TR": 3, "V_3": 3}
                self.card_labels = []

            def _equip_card(self, label, *_args, **_kwargs):
                self.card_labels.append(label)
                return QWidget()

        window = Window()
        dialog = results_view._build_plan_diff_dialog(
            window,
            "A",
            {
                "removed": [{"uid": "old", "type": "drive", "shape_id": "L_3_TR", "sub_stats": {}, "quality": "Gold", "area": 3}],
                "added": [{"uid": "new", "type": "drive", "shape_id": "V_3", "sub_stats": {}, "quality": "Gold", "area": 3}],
            },
        )

        self.assertEqual(["L_3_TR", "V_3"], window.card_labels)
        titles = [label.text() for label in dialog.findChildren(QLabel) if "变动" in label.text()]
        self.assertTrue(any("L_3_TR → V_3" in title for title in titles), titles)
        self.assertFalse(any("卸下 L_3_TR" == title.split("：")[-1] for title in titles))
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

            def _role_stat_priority_stats(self, *_args, **_kwargs):
                return []

            def _role_bonus_summary_panel(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, *_args, **_kwargs):
                return QWidget()

        window = Window()
        inventory_page._refresh_equip(window)
        app.processEvents()

        self.assertIn(35, window.grade_areas)
        self.assertNotIn(20, window.grade_areas[:1])
        app.processEvents()

    def test_saved_equipment_refresh_renders_in_batches(self):
        from PySide6.QtWidgets import QApplication, QFrame, QGroupBox, QLabel, QVBoxLayout, QWidget

        from src.features.inventory import page as inventory_page

        app = QApplication.instance() or QApplication([])
        callbacks = []

        class FakeTimer:
            @staticmethod
            def singleShot(_ms, callback):
                callbacks.append(callback)

        class Window:
            def __init__(self):
                self.equip_content = QFrame()
                self.equip_content_layout = QVBoxLayout(self.equip_content)
                self.equipped_state = {
                    f"Role{i}": {
                        "total_score": 0.0,
                        "total_grade": "D",
                        "equipped_tape": None,
                        "equipped_drives": [],
                        "blueprint_layout": [],
                    }
                    for i in range(inventory_page.EQUIPMENT_INITIAL_RENDER_COUNT + 1)
                }
                self.roles_db = {}
                self._shape_areas = {}

            def _score_drive_dict(self, *_args, **_kwargs):
                raise AssertionError("batched refresh should use saved score")

            def _score_tape_dict(self, *_args, **_kwargs):
                raise AssertionError("batched refresh should use saved score")

            def _calc_grade(self, *_args, **_kwargs):
                raise AssertionError("batched refresh should use saved grade")

            def _section_label(self, text):
                return QLabel(text)

            def _role_stat_priority_stats(self, *_args, **_kwargs):
                return []

            def _role_bonus_summary_panel(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, *_args, **_kwargs):
                return QWidget()

        window = Window()
        old_timer = inventory_page.QTimer
        inventory_page.QTimer = FakeTimer
        try:
            inventory_page._refresh_equip(window)
            self.assertEqual(1, len(callbacks))
            self.assertEqual(
                inventory_page.EQUIPMENT_INITIAL_RENDER_COUNT,
                len(window.equip_content.findChildren(QGroupBox)),
            )

            while callbacks:
                callbacks.pop(0)()

            self.assertEqual(
                inventory_page.EQUIPMENT_INITIAL_RENDER_COUNT + 1,
                len(window.equip_content.findChildren(QGroupBox)),
            )
        finally:
            inventory_page.QTimer = old_timer
        app.processEvents()

    def test_saved_equipment_refresh_reloads_state_from_disk(self):
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

        from src.app import runtime
        from src.features.inventory import page as inventory_page

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.equip_content = QFrame()
                self.equip_content_layout = QVBoxLayout(self.equip_content)
                self.equipped_state = {
                    "A": {
                        "total_score": 1.0,
                        "total_grade": "D",
                        "equipped_tape": None,
                        "equipped_drives": [{"uid": "old_drive", "shape_id": "H_2", "sub_stats": {}, "score": 1.0, "grade": "D"}],
                        "blueprint_layout": [],
                    }
                }
                self.roles_db = {}
                self._shape_areas = {"H_2": 2}
                self.rendered_uids = []

            def _score_drive_dict(self, *_args, **_kwargs):
                return 0.0

            def _score_tape_dict(self, *_args, **_kwargs):
                return 0.0

            def _calc_grade(self, *_args, **_kwargs):
                return "D"

            def _section_label(self, text):
                return QLabel(text)

            def _role_stat_priority_stats(self, *_args, **_kwargs):
                return []

            def _role_bonus_summary_panel(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, _label, _main_stat, _sub_stats, _shape_id, uid, *_args, **_kwargs):
                self.rendered_uids.append(uid)
                return QWidget()

        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp)
            (user_dir / "equipped_state.json").write_text(
                json.dumps(
                    {
                        "A": {
                            "total_score": 7.0,
                            "total_grade": "A",
                            "equipped_tape": None,
                            "equipped_drives": [
                                {"uid": "new_drive", "shape_id": "H_2", "sub_stats": {}, "score": 7.0, "grade": "A"}
                            ],
                            "blueprint_layout": [],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            old_user_dir = getattr(runtime, "USER_CONFIG_DIR", None)
            runtime.USER_CONFIG_DIR = user_dir
            try:
                window = Window()
                inventory_page._refresh_equip(window)
            finally:
                if old_user_dir is None:
                    delattr(runtime, "USER_CONFIG_DIR")
                else:
                    runtime.USER_CONFIG_DIR = old_user_dir

        self.assertEqual(["new_drive"], window.rendered_uids)
        self.assertEqual("new_drive", window.equipped_state["A"]["equipped_drives"][0]["uid"])
        app.processEvents()

    def test_saved_equipment_refresh_passes_change_marker_to_cards(self):
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

        from src.app import runtime
        from src.features.inventory import page as inventory_page

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.equip_content = QFrame()
                self.equip_content_layout = QVBoxLayout(self.equip_content)
                self.equipped_state = {}
                self.roles_db = {}
                self._shape_areas = {"H_2": 2}
                self.cards = []

            def _score_drive_dict(self, *_args, **_kwargs):
                return 0.0

            def _score_tape_dict(self, *_args, **_kwargs):
                return 0.0

            def _calc_grade(self, *_args, **_kwargs):
                return "D"

            def _section_label(self, text):
                return QLabel(text)

            def _role_stat_priority_stats(self, *_args, **_kwargs):
                return []

            def _role_bonus_summary_panel(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, *args, **kwargs):
                self.cards.append((args, kwargs))
                return QWidget()

        with tempfile.TemporaryDirectory() as tmp:
            user_dir = Path(tmp)
            (user_dir / "equipped_state.json").write_text(
                json.dumps(
                    {
                        "A": {
                            "total_score": 12.0,
                            "total_grade": "A",
                            "equipped_tape": {
                                "uid": "tape_changed",
                                "set_name": "套装A",
                                "main_stats": "攻击力%",
                                "sub_stats": {},
                                "score": 5.0,
                                "grade": "A",
                                "is_changed": True,
                            },
                            "equipped_drives": [
                                {
                                    "uid": "drive_changed",
                                    "shape_id": "H_2",
                                    "sub_stats": {},
                                    "score": 7.0,
                                    "grade": "A",
                                    "is_changed": True,
                                }
                            ],
                            "blueprint_layout": [],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            old_user_dir = getattr(runtime, "USER_CONFIG_DIR", None)
            runtime.USER_CONFIG_DIR = user_dir
            try:
                window = Window()
                inventory_page._refresh_equip(window)
            finally:
                if old_user_dir is None:
                    delattr(runtime, "USER_CONFIG_DIR")
                else:
                    runtime.USER_CONFIG_DIR = old_user_dir

        self.assertEqual([True, True], [kwargs.get("is_changed", False) for _args, kwargs in window.cards])
        self.assertEqual([False, False], [kwargs.get("is_new", False) for _args, kwargs in window.cards])
        app.processEvents()

    def test_role_equipment_state_preserves_replacement_change_marker(self):
        from src.features.role import page as role_page

        class Window:
            roles_db = {"A": {"weights": {}}}
            _shape_areas = {"H_2": 2}

            def _score_drive_dict(self, *_args, **_kwargs):
                return 7.0

            def _score_tape_dict(self, *_args, **_kwargs):
                return 5.0

            def _calc_grade(self, *_args, **_kwargs):
                return "A"

        role_state = role_page._role_drive_state(
            Window(),
            "A",
            {
                "tape": {
                    "uid": "tape_changed",
                    "set_name": "套装A",
                    "main_stats": {"攻击力%": 30.0},
                    "sub_stats": {},
                    "quality": "Gold",
                    "is_changed": True,
                },
                "drive": {
                    "blueprint_layout": [],
                    "drives": [
                        {
                            "uid": "drive_changed",
                            "shape_id": "H_2",
                            "sub_stats": {},
                            "quality": "Gold",
                            "is_changed": True,
                        }
                    ],
                },
            },
            {},
        )

        self.assertTrue(role_state["equipped_tape"]["is_changed"])
        self.assertTrue(role_state["equipped_drives"][0]["is_changed"])

    def test_role_drive_replacement_syncs_saved_equipment_state(self):
        from src.features.allocation import results_view

        class Window:
            def __init__(self):
                self.roles_db = {"A": {"weights": {"攻击力": 1.0}}}
                self._shape_areas = {"H_2": 2}
                self.final_plan = {
                    "A": {
                        "valid": True,
                        "assigned_tape": None,
                        "assigned_set_drives": [],
                        "assigned_extra_drives": [
                            {"uid": "old_drive", "shape_id": "H_2", "sub_stats": {"攻击力": 1.0}, "role_scores": {"A": 1.0}}
                        ],
                    }
                }
                self.equipped_state = {
                    "A": {
                        "equipped_tape": None,
                        "equipped_drives": [
                            {"uid": "old_drive", "shape_id": "H_2", "sub_stats": {"攻击力": 1.0}, "score": 1.0, "grade": "D"}
                        ],
                        "total_score": 1.0,
                        "total_grade": "D",
                    }
                }
                self.saved = 0
                self.refreshed = 0
                self.rendered = 0

            def _score_drive_dict(self, sub_stats, *_args):
                return float(sum(sub_stats.values()))

            def _calc_grade(self, score, area):
                return f"G{int(score)}-{area}"

            def _save_eq(self):
                self.saved += 1

            def _refresh_equip(self):
                self.refreshed += 1

            def _render_results(self, _plan):
                self.rendered += 1

        window = Window()

        changed = results_view._sync_role_drive_replacement(
            window,
            "A",
            "old_drive",
            {"uid": "new_drive", "shape_id": "H_2", "sub_stats": {"攻击力": 7.0}, "quality": "Gold", "is_new": True},
        )

        drive = window.equipped_state["A"]["equipped_drives"][0]
        self.assertTrue(changed)
        self.assertEqual("new_drive", drive["uid"])
        self.assertEqual("old_drive", window.final_plan["A"]["assigned_extra_drives"][0]["uid"])
        self.assertEqual({"攻击力": 7.0}, drive["sub_stats"])
        self.assertTrue(drive["is_changed"])
        self.assertNotIn("is_new", drive)
        self.assertEqual(7.0, window.equipped_state["A"]["total_score"])
        self.assertEqual(1, window.saved)
        self.assertEqual(1, window.refreshed)
        self.assertEqual(0, window.rendered)

    def test_role_replacement_dialog_does_not_persist_equipment_before_role_save(self):
        source = Path("src/features/role/drive_widget.py").read_text(encoding="utf-8")

        self.assertNotIn("_sync_role_drive_replacement", source)
        self.assertNotIn("_sync_role_tape_replacement", source)

    def test_role_tape_replacement_syncs_saved_equipment_state(self):
        from src.features.allocation import results_view

        class Window:
            def __init__(self):
                self.roles_db = {"A": {"weights": {"攻击力%": 1.0}}}
                self.final_plan = {
                    "A": {
                        "valid": True,
                        "assigned_tape": {"uid": "old_tape", "main_stats": "生命值", "sub_stats": {}, "role_scores": {"A": 1.0}},
                        "assigned_set_drives": [],
                        "assigned_extra_drives": [],
                    }
                }
                self.equipped_state = {
                    "A": {
                        "equipped_tape": {"uid": "old_tape", "main_stats": "生命值", "sub_stats": {}, "score": 1.0, "grade": "D"},
                        "equipped_drives": [],
                        "total_score": 1.0,
                        "total_grade": "D",
                    }
                }
                self.saved = 0
                self.refreshed = 0
                self.rendered = 0

            def _score_tape_dict(self, _main_stat, sub_stats, *_args):
                return float(sum(sub_stats.values()) + 30.0)

            def _calc_grade(self, score, area):
                return f"G{int(score)}-{area}"

            def _save_eq(self):
                self.saved += 1

            def _refresh_equip(self):
                self.refreshed += 1

            def _render_results(self, _plan):
                self.rendered += 1

        window = Window()

        changed = results_view._sync_role_tape_replacement(
            window,
            "A",
            "old_tape",
            {
                "uid": "new_tape",
                "set_name": "套装A",
                "main_stats": {"攻击力%": 30.0},
                "sub_stats": {"攻击力%": 5.0},
                "quality": "Gold",
                "is_new": True,
            },
        )

        tape = window.equipped_state["A"]["equipped_tape"]
        self.assertTrue(changed)
        self.assertEqual("new_tape", tape["uid"])
        self.assertEqual("攻击力%", tape["main_stats"])
        self.assertTrue(tape["is_changed"])
        self.assertNotIn("is_new", tape)
        self.assertEqual("old_tape", window.final_plan["A"]["assigned_tape"]["uid"])
        self.assertEqual(35.0, window.equipped_state["A"]["total_score"])
        self.assertEqual(1, window.saved)
        self.assertEqual(1, window.refreshed)
        self.assertEqual(0, window.rendered)

    def test_role_replacement_marks_object_plan_equipment_as_changed(self):
        from src.features.allocation import results_view
        from src.models.equipment import Drive, Tape

        drive = Drive(
            uid="old_drive",
            item_type="drive",
            quality="Gold",
            area=2,
            shape_id="H_2",
            main_stats={"攻击力": 10.0, "生命值": 100.0},
            sub_stats={"攻击力": 1.0},
            role_scores={"A": 1.0},
        )
        tape = Tape(
            uid="old_tape",
            item_type="tape",
            quality="Gold",
            area=15,
            set_name="套装A",
            main_stats="生命值",
            sub_stats={},
            role_scores={"A": 1.0},
        )

        class Window:
            def __init__(self):
                self.roles_db = {"A": {"weights": {"攻击力": 1.0, "攻击力%": 1.0}}}
                self._shape_areas = {"H_2": 2}
                self.final_plan = {
                    "A": {
                        "valid": True,
                        "assigned_tape": tape,
                        "assigned_set_drives": [],
                        "assigned_extra_drives": [drive],
                    }
                }
                self.equipped_state = {
                    "A": {
                        "equipped_tape": {"uid": "old_tape", "main_stats": "生命值", "sub_stats": {}, "score": 1.0, "grade": "D"},
                        "equipped_drives": [{"uid": "old_drive", "shape_id": "H_2", "sub_stats": {"攻击力": 1.0}, "score": 1.0, "grade": "D"}],
                    }
                }

            def _score_drive_dict(self, sub_stats, *_args):
                return float(sum(sub_stats.values()))

            def _score_tape_dict(self, _main_stat, sub_stats, *_args):
                return float(sum(sub_stats.values()) + 30.0)

            def _calc_grade(self, *_args):
                return "A"

            def _save_eq(self):
                pass

            def _refresh_equip(self):
                pass

            def _render_results(self, _plan):
                pass

        window = Window()

        results_view._sync_role_drive_replacement(
            window,
            "A",
            "old_drive",
            {"uid": "new_drive", "shape_id": "H_2", "sub_stats": {"攻击力": 7.0}, "quality": "Gold"},
        )
        results_view._sync_role_tape_replacement(
            window,
            "A",
            "old_tape",
            {"uid": "new_tape", "set_name": "套装B", "main_stats": {"攻击力%": 30.0}, "sub_stats": {}, "quality": "Gold"},
        )

        self.assertNotIn("changed_uids", window.final_plan["A"])
        self.assertEqual("old_drive", window.final_plan["A"]["assigned_extra_drives"][0].uid)
        self.assertEqual("old_tape", window.final_plan["A"]["assigned_tape"].uid)
        self.assertFalse(hasattr(window.final_plan["A"]["assigned_extra_drives"][0], "is_changed"))
        self.assertFalse(hasattr(window.final_plan["A"]["assigned_tape"], "is_changed"))

    def test_role_replacement_uses_saved_role_diff_after_equipped_state_refresh(self):
        from src.features.allocation import results_view
        from src.features.role import page as role_page
        from src.optimizer.state_manager import StateManager

        with tempfile.TemporaryDirectory() as tmp:
            state_mgr = StateManager(tmp)
            old_state = {
                "A": {
                    "equipped_tape": {
                        "uid": "old_tape",
                        "set_name": "套装A",
                        "main_stats": "生命值",
                        "sub_stats": {},
                        "quality": "Gold",
                        "score": 1.0,
                        "grade": "D",
                    },
                    "equipped_drives": [],
                    "total_score": 1.0,
                    "total_grade": "D",
                }
            }
            state_mgr.state_file.write_text(json.dumps(old_state, ensure_ascii=False), encoding="utf-8")

            class Window:
                def __init__(self):
                    self.state_mgr = state_mgr
                    self.roles_db = {"A": {"weights": {"攻击力%": 1.0}}}
                    self._shape_areas = {}
                    self._my_role_equipment_dirty_roles = {"A"}
                    self.equipped_state = old_state
                    self.final_plan = {
                        "A": {
                            "valid": True,
                            "assigned_tape": {
                                "uid": "old_tape",
                                "set_name": "套装A",
                                "main_stats": "生命值",
                                "sub_stats": {},
                                "quality": "Gold",
                                "role_scores": {"A": 1.0},
                            },
                            "assigned_set_drives": [],
                            "assigned_extra_drives": [],
                        }
                    }

                def _score_tape_dict(self, _main_stat, sub_stats, *_args):
                    return float(sum(sub_stats.values()) + 30.0)

                def _calc_grade(self, *_args):
                    return "A"

                def _refresh_equip(self):
                    pass

                def _render_results(self, _plan):
                    pass

            window = Window()
            role_page._save_pending_role_equipment_state(
                window,
                {
                    "A": {
                        "tape": {
                            "uid": "new_tape",
                            "set_name": "套装B",
                            "main_stats": {"攻击力%": 30.0},
                            "sub_stats": {},
                            "quality": "Gold",
                            "is_changed": True,
                        },
                        "drive": {"blueprint_layout": [], "drives": []},
                    }
                },
            )

            self.assertEqual("new_tape", window.equipped_state["A"]["equipped_tape"]["uid"])

            results_view._sync_role_tape_replacement(
                window,
                "A",
                "old_tape",
                {"uid": "new_tape", "set_name": "套装B", "main_stats": {"攻击力%": 30.0}, "sub_stats": {}, "quality": "Gold"},
            )

        diff = window.allocation_plan_diff["A"]
        self.assertTrue(diff["changed"])
        self.assertEqual(["old_tape"], [item["uid"] for item in diff["removed"]])
        self.assertEqual(["new_tape"], [item["uid"] for item in diff["added"]])

    def test_role_replacement_change_overrides_existing_new_marker_and_keeps_diff_button(self):
        from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QVBoxLayout, QWidget

        from src.features.allocation import results_view

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.result_card = QFrame()
                self.result_content_layout = QVBoxLayout(self.result_card)
                self.roles_db = {"A": {"weights": {}}}
                self._pending_strat = "role_priority"
                self.card_flags = []
                self.allocation_plan_diff = {
                    "A": {
                        "changed": True,
                        "added_uids": {"same_drive"},
                        "added": [{"uid": "same_drive", "display_name": "new"}],
                        "removed": [{"uid": "old_drive", "display_name": "old"}],
                    }
                }

            def _calc_grade(self, *_args):
                return "A"

            def _section_label(self, text):
                return QWidget()

            def _role_stat_priority_stats(self, *_args, **_kwargs):
                return []

            def _role_bonus_summary_panel(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, *args, **kwargs):
                self.card_flags.append((kwargs.get("is_new", False), kwargs.get("is_changed", False)))
                return QWidget()

            def _show_plan_diff_dialog(self, *_args, **_kwargs):
                pass

        window = Window()
        results_view._render_results(
            window,
            {
                "A": {
                    "valid": True,
                    "score": 10.0,
                    "blueprint": {},
                    "assigned_tape": None,
                    "assigned_set_drives": [],
                    "assigned_extra_drives": [
                        SimpleNamespace(
                            uid="same_drive",
                            shape_id="H_2",
                            sub_stats={},
                            role_scores={"A": 10.0},
                            quality="Gold",
                            area=2,
                            is_changed=True,
                            is_mvp=False,
                            pick_order=0,
                        )
                    ],
                }
            },
        )

        buttons = window.result_card.findChildren(QPushButton)
        self.assertIn("变动", [button.text() for button in buttons])
        self.assertEqual([(False, True)], window.card_flags)
        app.processEvents()

    def test_saved_equipment_refresh_prefers_change_over_stale_new_marker(self):
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

        from src.features.inventory import page as inventory_page

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self.equip_content = QFrame()
                self.equip_content_layout = QVBoxLayout(self.equip_content)
                self.equipped_state = {
                    "A": {
                        "equipped_tape": {
                            "uid": "t1",
                            "set_name": "套装A",
                            "main_stats": "攻击力%",
                            "sub_stats": {},
                            "quality": "Gold",
                            "score": 10.0,
                            "grade": "A",
                            "is_new": True,
                            "is_changed": True,
                        },
                        "equipped_drives": [
                            {
                                "uid": "d1",
                                "shape_id": "H_2",
                                "sub_stats": {},
                                "quality": "Gold",
                                "score": 10.0,
                                "grade": "A",
                                "score_area": 2,
                                "is_new": True,
                                "is_changed": True,
                            }
                        ],
                    }
                }
                self.roles_db = {"A": {"weights": {}}}
                self._shape_areas = {"H_2": 2}
                self.card_flags = []

            def _calc_grade(self, *_args):
                return "A"

            def _score_tape_dict(self, *_args, **_kwargs):
                return 10.0

            def _score_drive_dict(self, *_args, **_kwargs):
                return 10.0

            def _section_label(self, text):
                return QLabel(text)

            def _role_stat_priority_stats(self, *_args, **_kwargs):
                return []

            def _role_bonus_summary_panel(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, *args, **kwargs):
                self.card_flags.append((kwargs.get("is_new", False), kwargs.get("is_changed", False)))
                return QWidget()

        window = Window()
        inventory_page._render_equip_role(window, "A", window.equipped_state["A"])

        self.assertEqual([(False, True), (False, True)], window.card_flags)
        app.processEvents()

    def test_role_equipment_state_does_not_mark_changed_drive_as_new(self):
        from src.features.role import page as role_page

        class Window:
            roles_db = {"A": {"weights": {}}}
            _shape_areas = {"H_2": 2}

            def _score_drive_dict(self, *_args, **_kwargs):
                return 7.0

            def _calc_grade(self, *_args, **_kwargs):
                return "A"

        role_state = role_page._role_drive_state(
            Window(),
            "A",
            {
                "drive": {
                    "blueprint_layout": [],
                    "drives": [
                        {
                            "uid": "new_drive",
                            "shape_id": "H_2",
                            "sub_stats": {},
                            "quality": "Gold",
                            "is_new": True,
                            "is_changed": True,
                        }
                    ],
                }
            },
            {
                "equipped_drives": [
                    {"uid": "old_drive", "shape_id": "H_2", "sub_stats": {}, "quality": "Gold"}
                ]
            },
        )

        drive = role_state["equipped_drives"][0]
        self.assertTrue(drive["is_changed"])
        self.assertNotIn("is_new", drive)

    def test_role_equipment_state_preserves_empty_drive_slot_for_refill(self):
        from src.features.role import page as role_page

        class Window:
            roles_db = {"A": {"weights": {}}}
            _shape_areas = {"H_2": 2}

            def _score_drive_dict(self, *_args, **_kwargs):
                return 7.0

            def _calc_grade(self, *_args, **_kwargs):
                return "A"

        role_state = role_page._role_drive_state(
            Window(),
            "A",
            {
                "drive": {
                    "blueprint_layout": [["H_2", "H_2"]],
                    "drives": [
                        {
                            "uid": "empty_taken_drive",
                            "shape_id": "H_2",
                            "sub_stats": {},
                            "quality": "Gold",
                        }
                    ],
                }
            },
            {},
        )

        empty_slot = role_state["equipped_drives"][0]
        self.assertEqual("empty_taken_drive", empty_slot["uid"])
        self.assertEqual("H_2", empty_slot["shape_id"])
        self.assertEqual({}, empty_slot["sub_stats"])
        self.assertEqual(0.0, empty_slot["score"])
        self.assertTrue(empty_slot["is_changed"])

    def test_import_all_saved_equipment_overwrites_my_roles_once(self):
        from src.app import runtime
        from src.features.role import equipment_import

        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            user_dir = Path(tmp) / "user"
            config_dir.mkdir()
            user_dir.mkdir()
            (config_dir / "my_roles_model.json").write_text("{}", encoding="utf-8")
            (config_dir / "stats.json").write_text(
                json.dumps(
                    {
                        "tape_main_stat_values": {"攻击力%": 30.0},
                        "stat_alias_mapping": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (config_dir / "tapes.json").write_text(
                json.dumps(
                    {
                        "套装A": {
                            "display_name": "套装A",
                            "skill": {"攻击力%": 1.0},
                            "skill_2": {"暴击率%": 2.0},
                            "skill_cover": 0.8,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            roles_path = user_dir / "my_roles.json"
            roles_path.write_text(
                json.dumps(
                    {
                        "A": {
                            "name": "A",
                            "drive": {"blueprint_layout": [["old"]], "drives": [], "extra_shape_buffs": 2},
                            "tape": {"uid": "old_tape"},
                        },
                        "B": {
                            "name": "B",
                            "tape": {"uid": "stale_tape"},
                            "set_bonus": {"display_name": "旧套装"},
                        },
                        "C": {"name": "C"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            equipped_state = {
                "A": {
                    "blueprint_layout": [[1, -1]],
                    "equipped_tape": {
                        "uid": "t1",
                        "set_name": "套装A",
                        "main_stats": "攻击力%",
                        "sub_stats": {"暴击率%": 2.0},
                        "quality": "Gold",
                    },
                    "equipped_drives": [
                        {
                            "uid": "d1",
                            "shape_id": "H_2",
                            "quality": "Gold",
                            "sub_stats": {"攻击力": 16.0},
                        }
                    ],
                },
                "B": {
                    "blueprint_layout": [],
                    "equipped_tape": None,
                    "equipped_drives": [
                        {
                            "uid": "d2",
                            "shape_id": "V_2",
                            "quality": "Gold",
                            "sub_stats": {"生命值": 200.0},
                        }
                    ],
                },
                "Empty": {"blueprint_layout": [], "equipped_tape": None, "equipped_drives": []},
            }

            old_config_dir = getattr(runtime, "CONFIG_DIR", None)
            old_user_dir = getattr(runtime, "USER_CONFIG_DIR", None)
            runtime.CONFIG_DIR = config_dir
            runtime.USER_CONFIG_DIR = user_dir
            try:
                result = equipment_import.import_all_role_equipment(equipped_state)
            finally:
                if old_config_dir is None:
                    delattr(runtime, "CONFIG_DIR")
                else:
                    runtime.CONFIG_DIR = old_config_dir
                if old_user_dir is None:
                    delattr(runtime, "USER_CONFIG_DIR")
                else:
                    runtime.USER_CONFIG_DIR = old_user_dir

            self.assertEqual(2, result["imported"])
            self.assertEqual(1, result["skipped"])
            saved = json.loads(roles_path.read_text(encoding="utf-8"))
            self.assertEqual([[1, -1]], saved["A"]["drive"]["blueprint_layout"])
            self.assertEqual(2, saved["A"]["drive"]["extra_shape_buffs"])
            self.assertEqual({"攻击力": 16.0}, saved["A"]["drive"]["info"])
            self.assertEqual({"攻击力%": 30.0}, saved["A"]["tape"]["main_stats"])
            self.assertEqual("套装A", saved["A"]["set_bonus"]["display_name"])
            self.assertEqual("V_2", saved["B"]["drive"]["drives"][0]["shape_id"])
            self.assertNotIn("tape", saved["B"])
            self.assertNotIn("set_bonus", saved["B"])
            self.assertEqual({"name": "C"}, saved["C"])

    def test_saved_equipment_prefers_saved_score_snapshot(self):
        from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

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

            def _role_stat_priority_stats(self, *_args, **_kwargs):
                return []

            def _role_bonus_summary_panel(self, *_args, **_kwargs):
                return QWidget()

            def _equip_card(self, *args, **_kwargs):
                self.equip_cards.append(args)
                self.new_flags.append(_kwargs.get("is_new", False))
                return QWidget()

        window = Window()
        inventory_page._refresh_equip(window)
        app.processEvents()

        self.assertEqual((18.8, "S"), window.equip_cards[0][6])
        self.assertEqual([True], window.new_flags)
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

    def test_state_manager_new_allocation_overrides_old_change_marker(self):
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
                                {
                                    "uid": "old_changed",
                                    "display_name": "old changed",
                                    "shape_id": "X",
                                    "sub_stats": {},
                                    "is_changed": True,
                                }
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
        self.assertNotIn("is_changed", saved["A"]["equipped_drives"][0])

    def test_state_manager_new_tape_does_not_mark_unchanged_drive_as_new(self):
        from src.models.equipment import Drive, Tape
        from src.optimizer.state_manager import StateManager

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "equipped_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "A": {
                            "equipped_tape": {
                                "uid": "manual_tape",
                                "display_name": "manual tape",
                                "set_name": "Set",
                                "main_stats": "暴击率",
                                "sub_stats": {},
                                "is_changed": True,
                            },
                            "equipped_drives": [
                                {
                                    "uid": "same_drive",
                                    "display_name": "same drive",
                                    "shape_id": "H_2",
                                    "sub_stats": {},
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager = StateManager(config_dir=tmp)
            new_tape = Tape(
                uid="calc_tape",
                quality="Gold",
                area=15,
                set_name="Set",
                main_stats="攻击力%",
                sub_stats={},
                role_scores={"A": 10.0},
            )
            same_drive = Drive(
                uid="same_drive",
                quality="Gold",
                area=2,
                shape_id="H_2",
                set_name="Set",
                main_stats={"攻击力": 1.0, "生命值": 1.0},
                sub_stats={},
                role_scores={"A": 5.0},
            )

            manager.save_allocation(
                {
                    "A": {
                        "valid": True,
                        "score": 15.0,
                        "blueprint": {"board": [[1]]},
                        "assigned_tape": new_tape,
                        "assigned_set_drives": [same_drive],
                        "assigned_extra_drives": [],
                    }
                }
            )

            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertTrue(saved["A"]["equipped_tape"]["is_new"])
        self.assertNotIn("is_changed", saved["A"]["equipped_tape"])
        self.assertEqual("same_drive", saved["A"]["equipped_drives"][0]["uid"])
        self.assertNotIn("is_new", saved["A"]["equipped_drives"][0])
        self.assertNotIn("is_changed", saved["A"]["equipped_drives"][0])

    def test_state_manager_new_allocation_claims_same_uid_old_change_marker(self):
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
                                {
                                    "uid": "same_changed",
                                    "display_name": "same changed",
                                    "shape_id": "X",
                                    "sub_stats": {},
                                    "is_changed": True,
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
                uid="same_changed",
                quality="Gold",
                area=1,
                shape_id="X",
                set_name="Set",
                main_stats={"攻击力": 1, "生命值": 1},
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

        self.assertEqual("same_changed", saved["A"]["equipped_drives"][0]["uid"])
        self.assertTrue(saved["A"]["equipped_drives"][0]["is_new"])
        self.assertNotIn("is_changed", saved["A"]["equipped_drives"][0])
        self.assertTrue(saved["A"]["last_diff"]["changed"])

    def test_role_replacement_sync_does_not_mutate_pending_allocation_plan(self):
        from src.features.allocation import results_view
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
                                {
                                    "uid": "manual_changed",
                                    "display_name": "manual changed",
                                    "shape_id": "H_2",
                                    "sub_stats": {},
                                    "quality": "Gold",
                                    "score": 8.0,
                                    "grade": "A",
                                    "score_area": 2,
                                    "is_changed": True,
                                }
                            ],
                            "last_diff": {
                                "changed": True,
                                "added_uids": ["manual_changed"],
                                "added": [{"uid": "manual_changed", "display_name": "manual changed"}],
                                "removed": [{"uid": "old_drive", "display_name": "old drive"}],
                            },
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            class Window:
                def __init__(self):
                    self.state_mgr = StateManager(config_dir=tmp)
                    self.equipped_state = self.state_mgr.load_state()
                    self.roles_db = {"A": {"weights": {}}}
                    self._shape_areas = {"H_2": 2}
                    self.allocation_plan_diff = {}
                    self.final_plan = {
                        "A": {
                            "valid": True,
                            "score": 10.0,
                            "blueprint": {"board": [[1]]},
                            "assigned_tape": None,
                            "assigned_set_drives": [
                                Drive(
                                    uid="calc_new",
                                    quality="Gold",
                                    area=2,
                                    shape_id="H_2",
                                    set_name="Set",
                                    main_stats={"m1": 1, "m2": 1},
                                    sub_stats={},
                                    role_scores={"A": 10.0},
                                )
                            ],
                            "assigned_extra_drives": [],
                        }
                    }

                def _score_drive_dict(self, *_args, **_kwargs):
                    return 7.0

                def _calc_grade(self, *_args, **_kwargs):
                    return "A"

                def _save_eq(self):
                    with open(state_path, "w", encoding="utf-8") as f:
                        json.dump(self.equipped_state, f, ensure_ascii=False, indent=4)

                def _refresh_equip(self):
                    pass

                def _render_results(self, *_args, **_kwargs):
                    pass

            window = Window()
            results_view._sync_role_drive_replacement(
                window,
                "A",
                "calc_new",
                {
                    "uid": "manual_changed",
                    "shape_id": "H_2",
                    "sub_stats": {},
                    "quality": "Gold",
                },
            )

            self.assertEqual("calc_new", window.final_plan["A"]["assigned_set_drives"][0].uid)
            window.state_mgr.save_allocation(window.final_plan, mode="role_priority")
            saved = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertTrue(saved["A"]["last_diff"]["changed"])
        self.assertEqual(["calc_new"], saved["A"]["last_diff"]["added_uids"])
        self.assertTrue(saved["A"]["equipped_drives"][0]["is_new"])
        self.assertNotIn("is_changed", saved["A"]["equipped_drives"][0])

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
