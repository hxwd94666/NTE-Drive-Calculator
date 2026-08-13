# 验证设置与环境功能只消费 AppContext 提供的当前账号路径。
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.app.context import AccountContext, AppContext, ApplicationPaths
from src.features.settings.page import _settings_paths
from src.ui.controllers.environment_controller import _new_environment_operation
from src.ui.controllers.update_controller import _new_update_operation


class FakeSettings:
    def __init__(self, database_path, *, legacy_config_dir=None):
        self.database_path = Path(database_path)


def account(root: Path, account_id: str) -> AccountContext:
    account_root = root / "accounts" / account_id
    return AccountContext(
        active_account_id=account_id,
        active_account_name=account_id,
        account_data_root=account_root,
        user_database_path=account_root / "user_data.sqlite3",
        user_config_dir=account_root / "config",
        screenshot_dir=account_root / "scanned_images",
        log_dir=account_root / "logs",
    )


class SettingsContextTests(unittest.TestCase):
    def test_settings_paths_follow_context_account_switch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ApplicationPaths.from_roots(
                root=root,
                app_dir=root,
                data_root=root / "data",
                bundled_config_dir=root / "bundled_config",
                asset_dir=root / "assets",
                app_icon_path=root / "assets" / "app.ico",
            )
            first = account(paths.data_root, "first")
            second = account(paths.data_root, "second")
            context = AppContext(paths, first, settings_factory=FakeSettings)

            self.assertEqual(first.screenshot_dir, _settings_paths(context).screenshot_dir)
            context.switch_account(second)
            current = _settings_paths(context)
            self.assertEqual(second.screenshot_dir, current.screenshot_dir)
            self.assertEqual(second.log_dir, current.log_dir)
            self.assertEqual(paths.config_dir, current.config_dir)
            self.assertEqual(
                paths.config_dir / "global_ui_preferences.json",
                paths.global_ui_preferences_file,
            )

    def test_migrated_settings_controllers_do_not_import_runtime_globals(self):
        paths = [
            Path("src/features/settings/page.py"),
            Path("src/ui/controllers/configuration_controller.py"),
            Path("src/ui/controllers/environment_controller.py"),
            Path("src/ui/controllers/inventory_sync_controller.py"),
            Path("src/ui/controllers/update_controller.py"),
        ]
        violations = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "src.app":
                    if any(alias.name == "runtime" for alias in node.names):
                        violations.append(str(path))
                elif isinstance(node, ast.Import):
                    if any(alias.name == "src.app.runtime" for alias in node.names):
                        violations.append(str(path))
        self.assertEqual([], violations)

    def test_environment_and_update_operations_use_active_account_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = ApplicationPaths.from_roots(
                root=root,
                app_dir=root,
                data_root=root / "data",
                bundled_config_dir=root / "bundled_config",
                asset_dir=root / "assets",
                app_icon_path=root / "assets" / "app.ico",
            )
            context = AppContext(
                paths,
                account(paths.data_root, "diagnostic-account"),
                settings_factory=FakeSettings,
            )
            owner = SimpleNamespace(app_context=context)

            environment = _new_environment_operation(
                owner,
                "nte_core_diagnostics",
            )
            update = _new_update_operation(owner)

            self.assertEqual("diagnostic-account", environment.account_id)
            self.assertEqual("diagnostic-account", update.account_id)
            self.assertEqual(context.generation, environment.context_generation)
            self.assertEqual(context.generation, update.context_generation)

    def test_account_context_consumers_do_not_use_removed_account_id_field(self):
        violations = []
        for path in Path("src").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if ".account.account_id" in source:
                violations.append(str(path))
        self.assertEqual([], violations)

    def test_cloud_mode_development_label_uses_complete_feature_name(self):
        source = Path("src/features/settings/page.py").read_text(encoding="utf-8")
        self.assertIn('QLabel("云异环模式：正在开发中")', source)
        self.assertNotIn('QLabel("云异环：正在开发中', source)

    def test_account_switch_does_not_reload_or_apply_theme(self):
        tree = ast.parse(
            Path("src/ui/app.py").read_text(encoding="utf-8"),
            filename="src/ui/app.py",
        )
        handler = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_on_app_context_account_changed"
        )
        method_calls = {
            node.func.attr
            for node in ast.walk(handler)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assigned_attributes = {
            target.attr
            for node in ast.walk(handler)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Attribute)
        }

        self.assertNotIn("_load_theme_preference", method_calls)
        self.assertNotIn("_apply_theme_preference", method_calls)
        self.assertNotIn("_theme_preference", assigned_attributes)


if __name__ == "__main__":
    unittest.main()
