# 固定渐进类型检查的依赖、配置和首批已迁移模块清单。
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATED_CONTROLLER_MODULES = {
    "src/features/allocation/runner.py",
    "src/features/blueprints/controller.py",
    "src/features/configuration/controller.py",
    "src/features/identification/controller.py",
    "src/features/inventory/equipment_assembly_controller.py",
    "src/features/inventory/equipment_display_controller.py",
    "src/features/inventory/warehouse_controller.py",
    "src/features/inventory/warehouse_identification_controller.py",
    "src/features/official_role/controller.py",
    "src/features/scanning/controller.py",
    "src/ui/controllers/configuration_controller.py",
    "src/ui/controllers/environment_controller.py",
    "src/ui/controllers/hotkey_controller.py",
    "src/ui/controllers/inventory_sync_controller.py",
    "src/ui/controllers/official_role_replacement_controller.py",
    "src/ui/controllers/update_controller.py",
}


class MypyConfigurationTests(unittest.TestCase):
    def test_mypy_is_a_dev_dependency(self):
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"mypy>=', project)
        self.assertIn("[tool.mypy]", project)

    def test_allowlist_contains_only_existing_python_modules(self):
        allowlist = ROOT / "tools/quality/mypy_allowlist.txt"
        paths = [
            line.strip()
            for line in allowlist.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertGreaterEqual(len(paths), 20)
        self.assertEqual(len(paths), len(set(paths)))
        missing = [
            relative_path
            for relative_path in paths
            if not (ROOT / relative_path).is_file()
            or not relative_path.endswith(".py")
        ]
        self.assertEqual([], missing)

    def test_migrated_controllers_remain_in_allowlist(self):
        paths = {
            line.strip()
            for line in (
                ROOT / "tools/quality/mypy_allowlist.txt"
            ).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertEqual(set(), MIGRATED_CONTROLLER_MODULES - paths)


if __name__ == "__main__":
    unittest.main()
