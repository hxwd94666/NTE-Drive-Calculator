# 验证扫描与装备鉴定使用显式且冻结的账号路径依赖。
from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.app.context import AccountContext, AppContext, ApplicationPaths
from src.features.identification import controller as identification_controller
from src.features.identification.dependencies import IdentificationDependencies
from src.features.identification.lifecycle import identification_is_running
from src.features.scanning import controller as scanning_controller
from src.features.scanning import workflow as scanning_workflow
from src.features.scanning.dependencies import ScanningDependencies
from src.ui.main_window_mixins import FeatureMainWindowMixin


ROOT = Path(__file__).resolve().parents[1]


def imported_modules(relative_path: str) -> set[str]:
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def build_context(root: Path) -> AppContext:
    paths = ApplicationPaths.from_roots(
        root=root,
        app_dir=root / "src",
        data_root=root / "data",
        bundled_config_dir=root / "bundled_config",
        asset_dir=root / "assets",
        app_icon_path=root / "app.ico",
    )
    account = AccountContext(
        active_account_id="account-a",
        active_account_name="A",
        account_data_root=root / "data" / "accounts" / "account-a",
        user_database_path=root / "data" / "accounts" / "account-a" / "user.sqlite3",
        user_config_dir=root / "data" / "accounts" / "account-a" / "config",
        screenshot_dir=root / "data" / "accounts" / "account-a" / "screenshots",
        log_dir=root / "data" / "accounts" / "account-a" / "logs",
    )
    return AppContext(paths, account)


class ScanningIdentificationBoundaryTests(unittest.TestCase):
    def test_controllers_and_workers_do_not_import_runtime_or_dynamic_installer(self):
        for relative_path in (
            "src/app/workers.py",
            "src/features/scanning/controller.py",
            "src/features/identification/controller.py",
            "src/features/identification/dialogs.py",
        ):
            with self.subTest(path=relative_path):
                modules = imported_modules(relative_path)
                self.assertNotIn("src.app.runtime", modules)
                self.assertNotIn("src.ui.main_window_method_install", modules)

    def test_shared_ui_capabilities_are_owned_outside_feature_controllers(self):
        scanning_modules = imported_modules(
            "src/features/scanning/controller.py"
        )
        identification_modules = imported_modules(
            "src/features/identification/controller.py"
        )
        hotkey_modules = imported_modules(
            "src/integrations/global_hotkeys.py"
        )
        scanning_source = (
            ROOT / "src/features/scanning/controller.py"
        ).read_text(encoding="utf-8")
        identification_source = (
            ROOT / "src/features/identification/controller.py"
        ).read_text(encoding="utf-8")
        app_source = (ROOT / "src/ui/app.py").read_text(encoding="utf-8")

        self.assertNotIn(
            "src.features.identification.controller",
            scanning_modules,
        )
        self.assertNotIn(
            "src.features.scanning.controller",
            identification_modules,
        )
        self.assertFalse(
            any(
                module == "src.features"
                or module.startswith("src.features.")
                or module == "src.ui"
                or module.startswith("src.ui.")
                for module in hotkey_modules
            )
        )
        self.assertNotIn("set_identification_controller", scanning_source)
        self.assertNotIn("equipment_card_factory", identification_source)
        self.assertNotIn("register_hotkeys:", identification_source)
        self.assertIn(
            "self.equipment_presentation = EquipmentPresentation(",
            app_source,
        )
        self.assertIn(
            "self.global_hotkey_manager = GlobalHotkeyManager(",
            app_source,
        )
        self.assertGreaterEqual(
            app_source.count(
                "equipment_presentation=self.equipment_presentation"
            ),
            2,
        )
        self.assertGreaterEqual(
            app_source.count("hotkey_manager=self.global_hotkey_manager"),
            2,
        )

    def test_dependencies_pin_account_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = build_context(Path(temp_dir))
            scanning = ScanningDependencies.from_app_context(context)
            identification = IdentificationDependencies.from_app_context(context)

            replacement = AccountContext(
                active_account_id="account-b",
                active_account_name="B",
                account_data_root=Path(temp_dir) / "b",
                user_database_path=Path(temp_dir) / "b" / "user.sqlite3",
                user_config_dir=Path(temp_dir) / "b" / "config",
                screenshot_dir=Path(temp_dir) / "b" / "screenshots",
                log_dir=Path(temp_dir) / "b" / "logs",
            )
            context.switch_account(replacement)

            self.assertEqual("account-a", scanning.account_id)
            self.assertEqual("account-a", identification.account_id)
            self.assertNotEqual(context.account.user_database_path, scanning.user_database_path)
            self.assertNotEqual(context.account.user_database_path, identification.user_database_path)

    def test_running_task_detection_and_controller_composition(self):
        stopped = SimpleNamespace(isRunning=lambda: False)
        running = SimpleNamespace(isRunning=lambda: True)

        self.assertTrue(scanning_controller._scanning_is_running(SimpleNamespace(_vision_worker=running)))
        self.assertFalse(scanning_controller._scanning_is_running(SimpleNamespace(_vision_worker=stopped)))
        self.assertTrue(identification_is_running(SimpleNamespace(_identify_worker=running)))
        self.assertFalse(
            hasattr(FeatureMainWindowMixin, "_start_vision_processing")
        )
        self.assertTrue(
            callable(
                scanning_controller.ScanningController._start_vision_processing
            )
        )
        self.assertFalse(hasattr(FeatureMainWindowMixin, "_identify_start"))
        self.assertTrue(
            callable(identification_controller.IdentificationController.build_page)
        )

    def test_gamepad_pipeline_can_finish_without_a_separate_vision_worker(self):
        class Button:
            def __init__(self):
                self.enabled = False
                self.text = ""

            def setEnabled(self, enabled):
                self.enabled = enabled

            def setText(self, text):
                self.text = text

        owner = SimpleNamespace(
            _vision_worker=None,
            _progress_dlg=None,
            _pending_parse_only=True,
            _scan_dependencies=SimpleNamespace(
                account_id="account-a",
                generation=1,
                config_dir=Path("config"),
                user_database_path=Path("user.sqlite3"),
            ),
            dialog_parent=None,
            btn_run=Button(),
            _postprocess_vision_files=lambda _stats: {},
            _update_inventory_status=lambda: None,
        )

        original_scan_event = scanning_workflow._scan_event
        original_information = scanning_workflow.QMessageBox.information
        try:
            scanning_workflow._scan_event = lambda *_args, **_kwargs: None
            scanning_workflow.QMessageBox.information = (
                lambda *_args, **_kwargs: None
            )
            scanning_workflow._on_vision_done(owner, {})
        finally:
            scanning_workflow._scan_event = original_scan_event
            scanning_workflow.QMessageBox.information = original_information

        self.assertTrue(owner.btn_run.enabled)
        self.assertEqual("⚡  开始计算", owner.btn_run.text)
        self.assertFalse(owner._pending_parse_only)

    def test_worker_guards_do_not_confuse_existing_attribute_with_live_worker(self):
        unsafe_guard = re.compile(
            r'hasattr\([^\n]*["\'][^"\']*worker["\'][^\n]*\)'
            r'\s+and\s+[^\n]*worker[^\n]*\.isRunning\(\)'
        )
        violations = []
        for path in (ROOT / "src").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if unsafe_guard.search(source):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual([], violations)

    def test_dynamic_method_installer_is_deleted(self):
        self.assertFalse((ROOT / "src/ui/main_window_method_install.py").exists())


if __name__ == "__main__":
    unittest.main()
