# 验证真实扫描控制器到工作线程的授权代次注入，避免仅用模拟宿主掩盖构造遗漏。
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget

from src.app.workers import FullVisualScanParseWorkerThread, ScanWorkerThread
from src.features.scanning.controller import ScanningController


class ScanningControllerOperationGenerationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_real_controller_propagates_frozen_generation_to_both_scan_workers(self):
        root = Path("unused")
        account = SimpleNamespace(active_account_id="test", screenshot_dir=root, user_config_dir=root, user_database_path=root / "unused.sqlite3")
        paths = SimpleNamespace(
            config_dir=root,
            template_dir=root,
            equipment_allocation_database_path=root / "static.sqlite3",
            equipment_allocation_asset_root=root / "game_ui",
        )
        context = SimpleNamespace(account=account, generation=1, paths=paths)
        revision = [1]
        guard = Mock()
        parent = QWidget()
        hotkeys = SimpleNamespace(start=Mock(), stop=Mock(), configuration=SimpleNamespace(stop="F12"))
        controller = ScanningController(
            app_context=context, dialog_parent=parent, minimize_window=lambda: None,
            restore_window=lambda: None, activate_window=lambda: None,
            update_inventory_status=lambda: None, refresh_home=lambda: None,
            preferences_provider=lambda: {}, save_preferences=lambda: None,
            refresh_roles=lambda: None, refresh_equipment=lambda: None,
            card_factory=Mock(), equipment_presentation=Mock(), hotkey_manager=hotkeys,
            operation_entry=lambda *_: True, operation_guard=guard, operation_generation=lambda: (revision[0], context.generation),
        )
        controller.btn_run = QPushButton(parent)
        with patch.object(ScanWorkerThread, "start"), patch.object(FullVisualScanParseWorkerThread, "start"), patch("src.features.scanning.workflow.QMessageBox.question", return_value=QMessageBox.Ok):
            controller._start_scan("auto")
            first = controller._scan_worker
            self.assertIsInstance(first, ScanWorkerThread)
            self.assertEqual(
                paths.equipment_allocation_database_path,
                controller._scan_dependencies.static_database_path,
            )
            first.operation_guard("interface_input")
            guard.assert_called_with("interface_input")
            revision[0] = 2
            with self.assertRaises(CancelledError):
                first.operation_guard("interface_input")
            controller._start_gamepad_scan(1, capture_driver="mouse")
            second = controller._gamepad_worker
            self.assertIsInstance(second, FullVisualScanParseWorkerThread)
            self.assertEqual(paths.equipment_allocation_database_path, second.static_database_path)
            second.operation_guard("interface_input")
            context.generation = 2
            with self.assertRaises(CancelledError):
                second.operation_guard("interface_input")
        parent.close()
