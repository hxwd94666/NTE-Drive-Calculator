# 固定自动装配的显式依赖、控制器组合和账号切换任务边界。
from __future__ import annotations

import ast
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.features.inventory import equipment_automatic_assembly_controller as automatic_controller
from src.features.inventory import equipment_assembly_controller as controller
from src.services.bulk_equipment_apply_service import BulkEquipmentApplyService
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


class EquipmentAssemblyBoundaryTests(unittest.TestCase):
    def test_bulk_service_is_independent_from_qt_features_and_runtime(self):
        modules = imported_modules("src/services/bulk_equipment_apply_service.py")

        self.assertFalse(
            any(
                module == "PySide6"
                or module.startswith("PySide6.")
                or module.startswith("src.features")
                or module.startswith("src.ui")
                or module == "src.app.runtime"
                for module in modules
            )
        )

    def test_controller_and_ui_bridge_do_not_read_runtime_paths(self):
        controller_modules = imported_modules("src/features/inventory/equipment_assembly_controller.py")
        automatic_modules = imported_modules(
            "src/features/inventory/equipment_automatic_assembly_controller.py"
        )
        bridge_modules = imported_modules("src/features/drive_assembly/ui_bridge.py")

        self.assertNotIn("src.app.runtime", controller_modules)
        self.assertNotIn("src.app.runtime", automatic_modules)
        self.assertNotIn("src.app.runtime", bridge_modules)
        self.assertNotIn(
            "src.ui.main_window_method_install",
            controller_modules,
        )

    def test_automatic_assembly_has_an_independent_controller_owner(self):
        self.assertEqual(
            "src.features.inventory.equipment_automatic_assembly_controller",
            automatic_controller._start_automatic_equipment_assembly.__module__,
        )
        self.assertIs(
            controller._preview_automatic_assemble_all_roles,
            automatic_controller._preview_automatic_assemble_all_roles,
        )

    def test_fast_apply_controller_is_only_a_service_adapter(self):
        source = inspect.getsource(controller._run_nte_core_equipment_apply)

        self.assertIn("BulkEquipmentApplyService", source)
        self.assertNotIn("create_equipment_apply_job", source)
        self.assertNotIn("wait_for_snapshot", source)
        self.assertNotIn("mark_equipment_apply_job_item", source)
        self.assertNotIn("_legacy_run_nte_core_equipment_apply", dir(controller))
        self.assertIsNotNone(BulkEquipmentApplyService)

    def test_assembly_methods_are_composed_explicitly(self):
        self.assertIs(
            FeatureMainWindowMixin._preview_assemble_role,
            controller._preview_assemble_role,
        )
        self.assertIs(
            FeatureMainWindowMixin._preview_fast_assemble_all_roles,
            controller._preview_fast_assemble_all_roles,
        )

    def test_running_worker_blocks_account_bound_assembly_transition(self):
        stopped = SimpleNamespace(isRunning=lambda: False)
        running = SimpleNamespace(isRunning=lambda: True)

        self.assertFalse(controller._equipment_assembly_is_running(SimpleNamespace(_equipment_apply_worker=stopped)))
        self.assertTrue(
            controller._equipment_assembly_is_running(SimpleNamespace(_automatic_equipment_apply_worker=running))
        )


if __name__ == "__main__":
    unittest.main()
