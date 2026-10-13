# 固定自动装配的显式依赖、控制器组合和账号切换任务边界。
from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.features.inventory.equipment import assembly_controller as controller


ROOT = Path(__file__).resolve().parents[2]


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
    def test_controller_and_ui_bridge_do_not_read_runtime_paths(self):
        controller_modules = imported_modules("src/features/inventory/equipment/assembly_controller.py")
        automatic_modules = imported_modules(
            "src/features/inventory/equipment/automatic_assembly_controller.py"
        )
        bridge_modules = imported_modules("src/features/drive_assembly/ui_bridge.py")

        self.assertNotIn("src.app.runtime", controller_modules)
        self.assertNotIn("src.app.runtime", automatic_modules)
        self.assertNotIn("src.app.runtime", bridge_modules)
        self.assertNotIn(
            "src.ui.main_window_method_install",
            controller_modules,
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
