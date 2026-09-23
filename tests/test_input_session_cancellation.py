# 验证 F12 和同样允许输入的模式切换能在随机等待后的最终输入边界取消动作。
import threading
from concurrent.futures import CancelledError
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from src.integrations.operation_guard import GuardedGuiInput, bind_execution_guard, bind_stop_guard


class InputSessionCancellationTests(TestCase):
    def test_scanning_dependencies_freeze_mode_and_account_generation(self):
        from pathlib import Path
        from src.features.scanning.dependencies import current_scanning_dependencies

        root = Path("unused")
        account = SimpleNamespace(active_account_id="test", screenshot_dir=root, user_config_dir=root, user_database_path=root / "unused.sqlite3")
        paths = SimpleNamespace(
            config_dir=root,
            template_dir=root,
            equipment_allocation_database_path=root / "static.sqlite3",
            equipment_allocation_asset_root=root / "game_ui",
        )
        context = SimpleNamespace(account=account, generation=1, paths=paths)
        mode_revision = [1]
        owner = SimpleNamespace(app_context=context, operation_guard=lambda _: None, operation_generation=lambda: (mode_revision[0], context.generation))
        frozen = current_scanning_dependencies(owner)
        self.assertEqual(paths.equipment_allocation_database_path, frozen.static_database_path)
        self.assertEqual(paths.equipment_allocation_asset_root, frozen.game_ui_asset_root)
        frozen.operation_guard("interface_input")
        mode_revision[0] = 2  # New mode also allows input; old scan still stops.
        with self.assertRaises(CancelledError):
            frozen.operation_guard("interface_input")

    def test_f12_and_mode_generation_change_cancel_after_random_sleep(self):
        from src.features.drive_assembly.input_backends import PyAutoGuiMouseBackend

        for change in ("stop", "generation"):
            for method, args, forbidden in (("press_key", ("a",), "press"), ("cloud_click", ((1, 2),), "mouseDown"), ("drag", ((1, 2), (3, 4), 100), "mouseDown")):
                with self.subTest(change=change, method=method):
                    stop = threading.Event()
                    generation = [1]
                    mode_guard = Mock()  # Both modes still permit interface_input.
                    guard = bind_execution_guard(mode_guard, should_stop=stop.is_set, generation=lambda: generation[0])
                    driver = Mock()
                    backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
                    backend.operation_guard = guard
                    backend._pyautogui = GuardedGuiInput(driver, guard)
                    backend._send_input = SimpleNamespace(available=False)
                    backend._record_mouse_delivery = Mock()
                    backend._sleeper = lambda _: stop.set() if change == "stop" else generation.__setitem__(0, 2)
                    with patch("src.features.drive_assembly.input_backends.random_input_delay", return_value=0.1), self.assertRaises(CancelledError):
                        getattr(backend, method)(*args)
                    getattr(driver, forbidden).assert_not_called()
                    backend._pyautogui.mouseUp(button="left")
                    driver.mouseUp.assert_called()

    def test_missing_generation_is_not_implicit_permission(self):
        guard = bind_execution_guard(lambda _: None, should_stop=lambda: False, generation=None)
        with self.assertRaises(PermissionError):
            guard("interface_input")

    def test_mouse_scanner_stop_between_move_and_down_prevents_press(self):
        from src.integrations.vision.mouse_inventory_scan import MouseInventoryScanner

        driver = Mock()
        with TemporaryDirectory() as directory, patch.dict("sys.modules", {"pyautogui": driver}):
            scanner = MouseInventoryScanner(directory, operation_guard=lambda _: None, sleep_fn=lambda _: None)
            driver.moveTo.side_effect = lambda *_, **__: scanner.emergency_stop()
            with self.assertRaises(CancelledError):
                scanner._input.click((1, 2), content_height=100)
            driver.mouseDown.assert_not_called()
            scanner.close()
            driver.mouseUp.assert_called()

    def test_drone_f12_during_click_pause_never_presses(self):
        from src.scanner.drone_scanner import DroneScanner, MOUSEEVENTF_LEFTDOWN

        scanner = DroneScanner.__new__(DroneScanner)
        scanner._stopped = False
        scanner.operation_guard = bind_stop_guard(lambda _: None, lambda: scanner._stopped)
        scanner._window_rect = SimpleNamespace(left=0, top=0)
        scanner._screen_w = scanner._screen_h = 100
        with patch("src.scanner.drone_scanner.time.sleep", side_effect=lambda _: scanner.emergency_stop()), patch("src.scanner.drone_scanner._send_input") as send, self.assertRaises(CancelledError):
            scanner._click_at(1, 2)
        self.assertNotIn(((MOUSEEVENTF_LEFTDOWN,),), send.call_args_list)
