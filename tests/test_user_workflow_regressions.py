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


class ConfigurationWorkflowTests(unittest.TestCase):
    def test_role_board_cell_change_updates_draft_data(self):
        from src.features.configuration import page

        window = SimpleNamespace(_current_config_name="roles.json")
        data = {"role": {"board_matrix": [[0] * 5 for _ in range(5)]}}

        page.save_role_board_cell(window, "role", 1, 2, "-1", data, Path("."))

        self.assertTrue(window._config_dirty)
        self.assertEqual(-1, data["role"]["board_matrix"][1][2])


class UpdateWorkflowTests(unittest.TestCase):
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
