# 验证游戏内装配动作执行器的动作展开、停止和 UI 接入。
"""Tests for executing drive assembly action plans."""

import unittest


class FakeMouseBackend:
    def __init__(self):
        self.calls = []

    def click(self, position):
        self.calls.append(("click", position))

    def drag(self, start, end, duration_ms):
        self.calls.append(("drag", start, end, duration_ms))

    def press_gamepad_button(self, button_name):
        self.calls.append(("gamepad", button_name))

    def push_left_joystick(self, x, y):
        self.calls.append(("left_joystick", x, y))

    def press_key(self, key_name):
        self.calls.append(("key", key_name))

    def pause(self, seconds):
        self.calls.append(("pause", round(seconds, 3)))


class FakeScreenshotMouseBackend(FakeMouseBackend):
    def __init__(self, image):
        super().__init__()
        self._image = image

    def screenshot(self):
        return self._image


class SequenceScreenshotMouseBackend(FakeMouseBackend):
    def __init__(self, images):
        super().__init__()
        self._images = list(images)

    def screenshot(self):
        if len(self._images) > 1:
            return self._images.pop(0)
        return self._images[0]


class FakeOcrEngine:
    def __init__(self, lines):
        self.lines = lines
        self.images = []

    def extract_lines(self, image):
        self.images.append(image)
        return self.lines


class DriveAssemblyActionExecutorTests(unittest.TestCase):
    def test_executes_pointer_only_move_without_a_click(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        class PointerBackend(FakeMouseBackend):
            def move_to(self, position):
                self.calls.append(("move", position))

        backend = PointerBackend()

        execute_action_sequence(
            [{"name": "role_list_wake_mouse_after_gamepad", "position": (165, 185), "mouse_move_only": True}],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("move", (165, 185))], backend.calls)

    def test_action_observer_runs_after_each_successful_action(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeMouseBackend()
        observed = []
        execute_action_sequence(
            [{"name": "filter_button", "position": (10, 20)}],
            backend=backend,
            pause_seconds=0.0,
            role_name="A",
            on_action_executed=lambda action, role: observed.append((action["name"], role)),
        )

        self.assertEqual([("filter_button", "A")], observed)

    def test_action_observer_failure_does_not_interrupt_assembly(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeMouseBackend()
        report = execute_action_sequence(
            [{"name": "filter_button", "position": (10, 20)}],
            backend=backend,
            pause_seconds=0.0,
            on_action_executed=lambda _action, _role: (_ for _ in ()).throw(RuntimeError("record failure")),
        )

        self.assertEqual(1, report.executed_actions)
        self.assertEqual([("click", (10, 20))], backend.calls)

    def test_executes_click_and_drag_actions_in_order(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeMouseBackend()
        report = execute_action_sequence(
            [
                {"name": "filter_button", "position": (10, 20)},
                {"name": "drag_first_tape_to_socket", "from": (30, 40), "to": (50, 60), "duration_ms": 700},
            ],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual(
            [
                ("click", (10, 20)),
                ("drag", (30, 40), (50, 60), 700),
            ],
            backend.calls,
        )
        self.assertEqual(2, report.executed_actions)
        self.assertEqual([], report.skipped_actions)

    def test_cloud_click_releases_before_and_after_the_click(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        class CloudAwareBackend(FakeMouseBackend):
            def force_mouse_release(self):
                self.calls.append(("release",))

            def cloud_click(self, position, hold_seconds=0.12):
                self.calls.append(("cloud_click", position, hold_seconds))

        backend = CloudAwareBackend()
        report = execute_action_sequence(
            [
                {
                    "name": "open_role_list",
                    "position": (2455, 1320),
                    "cloud_click": True,
                    "click_hold_seconds": 0.12,
                    "ensure_mouse_release": True,
                }
            ],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual(
            [
                ("release",),
                ("cloud_click", (2455, 1320), 0.12),
                ("release",),
            ],
            backend.calls,
        )
        self.assertEqual(1, report.executed_actions)

    def test_executes_filter_scrolls_with_the_backend_scroll_gesture(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        class ScrollAwareBackend(FakeMouseBackend):
            def drag_scroll(self, start, end, duration_ms):
                self.calls.append(("scroll", start, end, duration_ms))

        backend = ScrollAwareBackend()
        execute_action_sequence(
            [
                {
                    "name": "drive_filter_scroll_to_bottom",
                    "from": (200, 900),
                    "to": (200, 300),
                    "duration_ms": 700,
                }
            ],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("scroll", (200, 900), (200, 300), 700)], backend.calls)

    def test_role_list_reset_uses_two_upward_wheel_batches_without_clicking(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        class WheelAwareBackend(FakeMouseBackend):
            def scroll(self, position, clicks):
                self.calls.append(("wheel", position, clicks))

        backend = WheelAwareBackend()
        execute_action_sequence(
            [
                {"name": "role_list_grid_reset_to_first", "position": (415, 600), "wheel_clicks": 48},
                {"name": "role_list_grid_reset_to_first", "position": (415, 600), "wheel_clicks": 48},
            ],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("wheel", (415, 600), 48), ("wheel", (415, 600), 48)], backend.calls)


    def test_executes_wheel_action_at_the_mapped_position(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        class WheelAwareBackend(FakeMouseBackend):
            def scroll(self, position, clicks):
                self.calls.append(("wheel", position, clicks))

        backend = WheelAwareBackend()
        report = execute_action_sequence(
            [{"name": "role_list_wheel_next_page", "position": (720, 600), "wheel_clicks": -8}],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("wheel", (720, 600), -8)], backend.calls)
        self.assertEqual(1, report.executed_actions)

    def test_role_list_wheel_can_emit_six_visible_incremental_ticks(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        class WheelAwareBackend(FakeMouseBackend):
            def scroll(self, position, clicks):
                self.calls.append(("wheel", position, clicks))

        backend = WheelAwareBackend()
        execute_action_sequence(
            [{
                "name": "role_list_wheel_next_row",
                "position": (720, 600),
                "wheel_clicks": -6,
                "wheel_click_interval_seconds": 0.15,
            }],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual(
            [("wheel", (720, 600), -1)] * 6,
            [call for call in backend.calls if call[0] == "wheel"],
        )
        self.assertAlmostEqual(0.75, sum(call[1] for call in backend.calls if call[0] == "pause"))

    def test_default_pause_between_actions_is_half_second(self):
        from src.features.drive_assembly.executor import DEFAULT_ACTION_PAUSE_SECONDS

        self.assertEqual(0.5, DEFAULT_ACTION_PAUSE_SECONDS)

    def test_executes_escape_key_action(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeMouseBackend()
        execute_action_sequence(
            [{"name": "close_role_list_to_role_page", "keyboard_key": "esc"}],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("key", "esc")], backend.calls)

    def test_quality_filter_click_pauses_before_the_next_filter_action(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeMouseBackend()
        execute_action_sequence(
            [
                {"name": "quality_orange", "quality": "Gold", "position": (1861, 1075)},
                {
                    "name": "drive_filter_scroll_to_bottom",
                    "from": (2067, 1190),
                    "to": (2067, 395),
                    "duration_ms": 500,
                },
            ],
            backend=backend,
        )

        drag_index = backend.calls.index(("drag", (2067, 1190), (2067, 395), 500))
        self.assertEqual(("click", (1861, 1075)), backend.calls[0])
        self.assertAlmostEqual(0.25, sum(value for kind, value in backend.calls[1:drag_index] if kind == "pause"))
        self.assertAlmostEqual(0.5, sum(value for kind, value in backend.calls[drag_index + 1 :] if kind == "pause"))

    def test_retries_a_quality_click_only_when_the_button_is_not_selected(self):
        import numpy as np

        from src.features.drive_assembly.executor import execute_action_sequence

        image = np.zeros((100, 100, 3), dtype=np.uint8)
        backend = FakeScreenshotMouseBackend(image)
        execute_action_sequence(
            [
                {
                    "name": "verify_quality_selected",
                    "selection_probe_position": (20, 30),
                    "retry_position": (40, 50),
                }
            ],
            backend=backend,
        )

        self.assertEqual(("click", (40, 50)), backend.calls[0])
        self.assertAlmostEqual(0.75, sum(value for kind, value in backend.calls if kind == "pause"))

    def test_retries_a_drive_drag_when_its_target_is_still_empty(self):
        import numpy as np

        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeScreenshotMouseBackend(np.zeros((100, 100, 3), dtype=np.uint8))
        execute_action_sequence(
            [
                {
                    "name": "verify_drive_block_installed",
                    "block_id": 5,
                    "target_position": (50, 60),
                    "retry_from": (12, 20),
                    "retry_to": (50, 60),
                    "retry_duration_ms": 700,
                    "sample_radius": 4,
                    "brightness_threshold": 22.0,
                    "retry_prompt_wait_seconds": 0.3,
                    "retry_settle_seconds": 1.0,
                }
            ],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertIn(("drag", (12, 20), (50, 60), 700), backend.calls)
        self.assertAlmostEqual(1.3, sum(call[1] for call in backend.calls if call[0] == "pause"), places=2)

    def test_keeps_a_drive_without_retry_when_the_target_is_occupied(self):
        import numpy as np

        from src.features.drive_assembly.executor import execute_action_sequence

        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[56:65, 46:55] = 120
        backend = FakeScreenshotMouseBackend(image)
        execute_action_sequence(
            [
                {
                    "name": "verify_drive_block_installed",
                    "target_position": (50, 60),
                    "retry_from": (12, 20),
                    "retry_to": (50, 60),
                    "retry_duration_ms": 700,
                }
            ],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertFalse(any(call[0] == "drag" for call in backend.calls))

    def test_uses_target_image_change_to_avoid_an_unneeded_drive_retry(self):
        import numpy as np

        from src.features.drive_assembly.executor import execute_action_sequence

        before = np.zeros((100, 100, 3), dtype=np.uint8)
        after = before.copy()
        after[48:73, 38:63] = (180, 90, 40)
        backend = SequenceScreenshotMouseBackend([before, after])
        execute_action_sequence(
            [
                {
                    "name": "capture_drive_target_baseline",
                    "block_id": 5,
                    "target_position": (50, 60),
                    "sample_radius": 12,
                },
                {"name": "force_drag_first_drive_to_block", "block_id": 5, "from": (12, 20), "to": (50, 60), "duration_ms": 700},
                {
                    "name": "verify_drive_block_installed",
                    "block_id": 5,
                    "target_position": (50, 60),
                    "retry_from": (12, 20),
                    "retry_to": (50, 60),
                    "retry_duration_ms": 700,
                    "sample_radius": 12,
                    "change_threshold": 15.0,
                },
            ],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("drag", (12, 20), (50, 60), 700)], [call for call in backend.calls if call[0] == "drag"])

    def test_expands_drive_block_placeholders_before_execution(self):
        from src.features.drive_assembly.executor import execute_role_assembly_plan

        backend = FakeMouseBackend()
        plan = {
            "role_name": "真红",
            "available": True,
            "actions": [
                {
                    "name": "install_drives",
                    "sequence": [
                        {"name": "drive_tab", "position": (1, 1)},
                        {"name": "install_drive_block", "block_id": 7, "sequence_index": 0},
                    ],
                    "install_plans": [
                        {
                            "block_id": 7,
                            "install_sequence": [
                                {"name": "shape_select", "position": (2, 2)},
                                {"name": "drag_first_drive_to_block", "from": (3, 3), "to": (4, 4), "duration_ms": 500},
                            ],
                        }
                    ],
                }
            ],
        }

        report = execute_role_assembly_plan(plan, backend=backend, pause_seconds=0.0)

        self.assertEqual(
            [
                ("click", (1, 1)),
                ("click", (2, 2)),
                ("drag", (3, 3), (4, 4), 500),
            ],
            backend.calls,
        )
        self.assertEqual("真红", report.role_name)
        self.assertEqual(3, report.executed_actions)

    def test_stop_checker_prevents_later_actions(self):
        from src.features.drive_assembly.executor import AssemblyExecutionStopped, execute_action_sequence

        backend = FakeMouseBackend()
        checks = iter([False, True])

        with self.assertRaises(AssemblyExecutionStopped):
            execute_action_sequence(
                [
                    {"name": "first", "position": (1, 1)},
                    {"name": "second", "position": (2, 2)},
                ],
                backend=backend,
                pause_seconds=0.0,
                should_stop=lambda: next(checks),
            )

        self.assertEqual([("click", (1, 1))], backend.calls)

    def test_executes_wait_actions(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeMouseBackend()
        report = execute_action_sequence([{"name": "buffer", "wait_seconds": 1.25}], backend=backend, pause_seconds=0.0)

        self.assertAlmostEqual(1.25, sum(value for kind, value in backend.calls if kind == "pause"), places=2)
        self.assertEqual(1, report.executed_actions)

    def test_stop_checker_interrupts_wait_actions(self):
        from src.features.drive_assembly.executor import AssemblyExecutionStopped, execute_action_sequence

        backend = FakeMouseBackend()
        checks = iter([False, False, True])

        with self.assertRaises(AssemblyExecutionStopped):
            execute_action_sequence(
                [{"name": "buffer", "wait_seconds": 1.25}],
                backend=backend,
                pause_seconds=0.0,
                should_stop=lambda: next(checks),
            )

        self.assertEqual([("pause", 0.05)], backend.calls)

    def test_executes_gamepad_button_actions(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeMouseBackend()
        report = execute_action_sequence(
            [{"name": "role_dpad_next", "gamepad_button": "dpad_down"}],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("gamepad", "dpad_down")], backend.calls)
        self.assertEqual(1, report.executed_actions)

    def test_executes_left_stick_down_actions(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeMouseBackend()
        report = execute_action_sequence(
            [{"name": "main_stat_gamepad_down_to_expand", "gamepad_stick": "left_down"}],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("left_joystick", 0.0, -1.0)], backend.calls)
        self.assertEqual(1, report.executed_actions)

    def test_virtual_gamepad_close_resets_and_releases_the_controller(self):
        from src.features.drive_assembly.executor import _VirtualGamepadDriver

        calls = []

        class Gamepad:
            def reset(self):
                calls.append("reset")

            def update(self):
                calls.append("update")

        driver = _VirtualGamepadDriver.__new__(_VirtualGamepadDriver)
        driver._gamepad = Gamepad()
        driver._buttons = object()

        driver.close()
        driver.close()

        self.assertEqual(["reset", "update"], calls)
        self.assertIsNone(driver._gamepad)
        self.assertIsNone(driver._buttons)

    def test_virtual_gamepad_holds_rs_longer_than_other_buttons(self):
        from src.features.drive_assembly.executor import _VirtualGamepadDriver

        pauses = []

        class Gamepad:
            def press_button(self, button):
                pass

            def release_button(self, button):
                pass

            def update(self):
                pass

        class Buttons:
            XUSB_GAMEPAD_A = object()
            XUSB_GAMEPAD_RIGHT_THUMB = object()

        driver = _VirtualGamepadDriver.__new__(_VirtualGamepadDriver)
        driver._gamepad = Gamepad()
        driver._buttons = Buttons()
        driver._hold_seconds = 0.08
        driver._settle_seconds = 0.30
        driver._sleeper = pauses.append
        driver._ensure_connected = lambda: None

        driver.press("a")
        driver.press("rs")

        self.assertEqual([0.08, 0.30, 0.25, 0.30], pauses)

    def test_action_can_override_default_post_action_pause(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeMouseBackend()
        execute_action_sequence(
            [
                {
                    "name": "main_stat_gamepad_down_to_expand",
                    "gamepad_stick": "left_down",
                    "post_action_pause_seconds": 0.3,
                }
            ],
            backend=backend,
        )

        self.assertEqual(("left_joystick", 0.0, -1.0), backend.calls[0])
        self.assertAlmostEqual(0.3, sum(call[1] for call in backend.calls if call[0] == "pause"))

if __name__ == "__main__":
    unittest.main()
