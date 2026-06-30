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
        self.assertNotIn("一键导入", [button.text() for button in window.result_card.findChildren(QPushButton)])
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

            def _score_tape_dict(self, *_args, **_kwargs):
                return 10.0

            def _score_drive_dict(self, *_args, **_kwargs):
                return 10.0

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

            def _bonus_summary_widget(self, *_args, **_kwargs):
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

    def test_equip_card_uses_new_and_change_labels_separately(self):
        from PySide6.QtWidgets import QApplication, QLabel, QWidget

        from src.app import runtime
        from src.features.allocation import results_view

        app = QApplication.instance() or QApplication([])

        class Window(QWidget):
            def _stat_w(self, *_args):
                return 1.0

            def _stat_c(self, *_args):
                return "#58a6ff"

        window = Window()
        old_template_dir = getattr(runtime, "TEMPLATE_DIR", None)
        with tempfile.TemporaryDirectory() as tmp:
            runtime.TEMPLATE_DIR = Path(tmp)
            try:
                drive_card = results_view._equip_card(
                    window,
                    "H_2",
                    "",
                    {"攻击力": 16},
                    "H_2",
                    "drive_1",
                    {},
                    (12.0, "A"),
                    "Gold",
                    is_new=True,
                )
                drive_texts = [label.text() for label in drive_card.findChildren(QLabel)]
                self.assertIn("NEW", drive_texts)
                self.assertNotIn("CHANGE", drive_texts)

                tape_card = results_view._equip_card(
                    window,
                    "套装A",
                    "攻击力%",
                    {"暴击率%": 2.0},
                    None,
                    "tape_1",
                    {},
                    (20.0, "S"),
                    "Gold",
                    is_changed=True,
                )
            finally:
                if old_template_dir is None:
                    delattr(runtime, "TEMPLATE_DIR")
                else:
                    runtime.TEMPLATE_DIR = old_template_dir
        tape_texts = [label.text() for label in tape_card.findChildren(QLabel)]
        self.assertIn("CHANGE", tape_texts)
        self.assertGreater(tape_texts.index("CHANGE"), tape_texts.index("攻击力%"))
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

            def _bonus_summary_widget(self, *_args, **_kwargs):
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

            def _bonus_summary_widget(self, *_args, **_kwargs):
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

            def _bonus_summary_widget(self, *_args, **_kwargs):
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

            def _bonus_summary_widget(self, *_args, **_kwargs):
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

            def _bonus_summary_widget(self, *_args, **_kwargs):
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

            def _bonus_summary_widget(self, *_args, **_kwargs):
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

    def test_inventory_import_all_saved_equipment_updates_loaded_role_form_data(self):
        from src.features.inventory import page as inventory_page

        class FakeMessageBox:
            Yes = 1
            No = 2
            records = []

            @classmethod
            def question(cls, *_args, **_kwargs):
                return cls.Yes

            @classmethod
            def information(cls, _parent, title, text):
                cls.records.append(("information", title, text))

            @classmethod
            def warning(cls, _parent, title, text):
                cls.records.append(("warning", title, text))

            @classmethod
            def critical(cls, _parent, title, text):
                cls.records.append(("critical", title, text))

        class Window:
            def __init__(self):
                self.equipped_state = {"A": {"equipped_drives": [{"uid": "d1"}]}}
                self._my_role_form_data = {"A": {"old": True}}

        calls = []

        def fake_import_all(equipped_state):
            calls.append(equipped_state)
            return {
                "imported": 1,
                "skipped": 0,
                "failed": [],
                "my_roles": {"A": {"drive": {"drives": [{"uid": "d1"}]}}},
            }

        old_message_box = inventory_page.QMessageBox
        old_import_all = getattr(inventory_page, "import_all_role_equipment", None)
        inventory_page.QMessageBox = FakeMessageBox
        inventory_page.import_all_role_equipment = fake_import_all
        try:
            window = Window()
            inventory_page._import_all_to_my_roles(window)
        finally:
            inventory_page.QMessageBox = old_message_box
            if old_import_all is None:
                delattr(inventory_page, "import_all_role_equipment")
            else:
                inventory_page.import_all_role_equipment = old_import_all

        self.assertEqual([{"A": {"equipped_drives": [{"uid": "d1"}]}}], calls)
        self.assertEqual({"drive": {"drives": [{"uid": "d1"}]}}, window._my_role_form_data["A"])
        self.assertIn("已导入 1 个角色配装", FakeMessageBox.records[-1][2])

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
        app.processEvents()

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


