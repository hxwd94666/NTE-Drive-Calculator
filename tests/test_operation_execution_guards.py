# 验证实际输入和原生装配在执行时复核工作模式，并在撤销后释放输入。
"""Use only fake input drivers and fake RPC dispatches."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from src.integrations.operation_guard import GuardedGuiInput, require_operation
from src.integrations.vision.mouse_scan_input import PyAutoGuiMouseScanInput
from src.services.equipment_apply_service import EquipmentApplyService
from src.services.bulk_equipment_apply_service import BulkEquipmentApplyService


class ExecutionGuardTests(TestCase):
    def test_corner_failsafe_does_not_block_release_only_fallback(self):
        class FailSafeError(Exception):
            pass

        driver = SimpleNamespace(FailSafeException=FailSafeError, mouseUp=Mock(side_effect=FailSafeError()))
        guarded = GuardedGuiInput(driver, None)
        with patch("ctypes.windll.user32.mouse_event") as release:
            guarded.mouseUp(button="left")
        release.assert_called_once_with(0x0004, 0, 0, 0, 0)

    def test_random_delay_revocation_prevents_later_actual_input(self):
        from src.features.drive_assembly.input_backends import PyAutoGuiMouseBackend

        for method, args, forbidden in (
            ("press_key", ("a",), "press"),
            ("cloud_click", ((1, 2),), "mouseDown"),
            ("drag", ((1, 2), (3, 4), 100), "mouseDown"),
        ):
            with self.subTest(method=method):
                allowed = [True]

                def guard(_):
                    if not allowed[0]:
                        raise PermissionError("revoked during delay")

                backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
                driver = Mock()
                backend.operation_guard = guard
                backend._pyautogui = GuardedGuiInput(driver, guard)
                backend._send_input = SimpleNamespace(available=False)
                backend._record_mouse_delivery = Mock()
                backend._sleeper = lambda _: allowed.__setitem__(0, False)
                with (
                    patch("src.features.drive_assembly.input_backends.random_input_delay", return_value=0.1),
                    self.assertRaises(PermissionError),
                ):
                    getattr(backend, method)(*args)
                getattr(driver, forbidden).assert_not_called()

    def test_cloud_click_release_is_unconditional_after_down(self):
        from src.features.drive_assembly.input_backends import PyAutoGuiMouseBackend

        backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
        driver = Mock()
        backend.operation_guard = lambda _: None
        backend._pyautogui = GuardedGuiInput(driver, backend.operation_guard)
        backend._record_mouse_delivery = Mock()
        backend._sleeper = Mock(side_effect=[None, PermissionError("revoked while held")])
        with self.assertRaises(PermissionError):
            backend.cloud_click((1, 2))
        driver.mouseDown.assert_called_once()
        self.assertEqual(2, driver.mouseUp.call_count)

    def test_missing_guard_never_authorizes_an_operation(self):
        with self.assertRaises(PermissionError):
            require_operation(None, "native_equipment")

    def test_native_rpc_denied_before_dispatch(self):
        dispatch = Mock()
        service = EquipmentApplyService(Mock(), Mock())
        with self.assertRaises(PermissionError):
            service._dispatch_with_busy_retry(dispatch, operation="test")
        dispatch.assert_not_called()

    def test_native_busy_retry_rechecks_revoked_authorization(self):
        dispatch = Mock(side_effect=RuntimeError("busy"))
        guard = Mock(side_effect=[None, PermissionError("revoked")])
        service = EquipmentApplyService(Mock(), Mock(), operation_guard=guard)
        with (
            patch("src.services.equipment_apply_service.is_mods_plugin_busy_error", return_value=True),
            patch("src.services.equipment_apply_service.time.sleep"),
            self.assertRaises(PermissionError),
        ):
            service._dispatch_with_busy_retry(dispatch, operation="test")
        self.assertEqual(1, dispatch.call_count)
        self.assertEqual([(("native_equipment",),)] * 2, guard.call_args_list)

    def test_bulk_denial_precedes_database_or_job_side_effects(self):
        factory = Mock()
        service = BulkEquipmentApplyService("unused.sqlite3", Mock(), dao_factory=factory)
        with self.assertRaises(PermissionError):
            service.run(slot_ids=[1])
        factory.assert_not_called()

    def test_mouse_scan_denied_without_input_but_release_remains_available(self):
        mouse = PyAutoGuiMouseScanInput.__new__(PyAutoGuiMouseScanInput)
        mouse.operation_guard = None
        mouse._pyautogui = Mock()
        mouse._disable_pyautogui_pause = False
        with self.assertRaises(PermissionError):
            mouse.click((10, 20), content_height=100)
        mouse._pyautogui.moveTo.assert_not_called()
        mouse._pyautogui.mouseDown.assert_not_called()
        mouse.release_left()
        mouse._pyautogui.mouseUp.assert_called_once()

    def test_all_assembly_input_entrypoints_deny_before_driver_use(self):
        from src.features.drive_assembly.input_backends import PyAutoGuiMouseBackend

        backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
        backend.operation_guard = None
        for method, args in (
            ("click", ((1, 2),)), ("cloud_click", ((1, 2),)),
            ("move_to", ((1, 2),)), ("drag", ((1, 2), (3, 4), 100)),
            ("drag_scroll", ((1, 2), (3, 4), 100)),
            ("scroll", ((1, 2), 1)), ("press_key", ("a",)),
            ("press_gamepad_button", ("a",)), ("push_left_joystick", (1, 0)),
        ):
            with self.subTest(method=method), self.assertRaises(PermissionError):
                getattr(backend, method)(*args)

    def test_gamepad_creation_is_denied_before_loading_driver(self):
        from src.scanner.gamepad_controller import GamepadScanner

        with patch.dict("sys.modules", {"vgamepad": None}), self.assertRaises(PermissionError):
            GamepadScanner(operation_guard=None)

    def test_gamepad_release_survives_revocation_during_hold(self):
        from src.scanner.gamepad_controller import GamepadScanner

        scanner = GamepadScanner.__new__(GamepadScanner)
        scanner.operation_guard = Mock()
        scanner.gamepad = Mock()
        with patch("src.scanner.gamepad_controller.time.sleep", side_effect=PermissionError("revoked")):
            with self.assertRaises(PermissionError):
                scanner._press_button("A")
        scanner.gamepad.release_button.assert_called_once_with(button="A")

    def test_drone_swipe_releases_button_when_permission_changes(self):
        from src.scanner.drone_scanner import DroneScanner, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP

        scanner = DroneScanner.__new__(DroneScanner)
        scanner.operation_guard = Mock(side_effect=[None, None, PermissionError("revoked")])
        scanner._window_rect = SimpleNamespace(left=0, top=0, width=100, height=100)
        scanner._screen_w = scanner._screen_h = 100
        scanner._scale_x = scanner._scale_y = 1
        scanner._content_left = scanner._content_top = 0
        scanner._stopped = False
        with (
            patch("src.scanner.drone_scanner.time.sleep"),
            patch("src.scanner.drone_scanner._send_input") as send,
            self.assertRaises(PermissionError),
        ):
            scanner._swipe_up()
        self.assertIn(((MOUSEEVENTF_LEFTDOWN,),), send.call_args_list)
        send.assert_called_with(MOUSEEVENTF_LEFTUP)
