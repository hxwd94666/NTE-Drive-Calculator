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


class DriveAssemblyExecutorTests(unittest.TestCase):
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

    def test_default_pause_between_actions_is_half_second(self):
        from src.features.drive_assembly.executor import DEFAULT_ACTION_PAUSE_SECONDS

        self.assertEqual(0.5, DEFAULT_ACTION_PAUSE_SECONDS)

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
        self.assertAlmostEqual(0.5, sum(value for kind, value in backend.calls[1:drag_index] if kind == "pause"))
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
        self.assertAlmostEqual(1.0, sum(value for kind, value in backend.calls if kind == "pause"))

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

    def test_clicks_optional_confirm_when_prompt_probe_is_bright(self):
        import numpy as np

        from src.features.drive_assembly.executor import execute_action_sequence

        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[20:81, 20:81] = 220
        backend = FakeScreenshotMouseBackend(image)

        report = execute_action_sequence(
            [
                {
                    "name": "confirm_equipment_reuse_prompt",
                    "optional_confirm_position": (70, 80),
                    "modal_probe_position": (50, 50),
                    "brightness_threshold": 150,
                }
            ],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("click", (70, 80))], backend.calls)
        self.assertEqual(1, report.executed_actions)

    def test_skips_optional_confirm_when_prompt_probe_is_dark(self):
        import numpy as np

        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeScreenshotMouseBackend(np.zeros((100, 100, 3), dtype=np.uint8))

        report = execute_action_sequence(
            [
                {
                    "name": "confirm_equipment_reuse_prompt",
                    "optional_confirm_position": (70, 80),
                    "modal_probe_position": (50, 50),
                    "brightness_threshold": 150,
                }
            ],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([], backend.calls)
        self.assertEqual(0, report.executed_actions)
        self.assertEqual(1, len(report.skipped_actions))

    def test_clicks_ocr_target_text_center_when_available(self):
        import numpy as np

        import src.features.drive_assembly.executor as executor

        original_instance = executor._OCR_ENGINE_INSTANCE
        original_factory = executor._OCR_ENGINE_FACTORY
        try:
            executor._OCR_ENGINE_INSTANCE = None
            executor._OCR_ENGINE_FACTORY = lambda: FakeOcrEngine(
                [{"text": "Attack Percent", "box": (20, 10, 120, 50)}]
            )
            backend = FakeScreenshotMouseBackend(np.zeros((500, 500, 3), dtype=np.uint8))

            report = executor.execute_action_sequence(
                [
                    {
                        "name": "main_stat_option",
                        "ocr_target_text": "Attack Percent",
                        "ocr_search_region": (100, 200, 300, 420),
                        "fallback_position": (9, 9),
                    }
                ],
                backend=backend,
                pause_seconds=0.0,
            )
        finally:
            executor._OCR_ENGINE_INSTANCE = original_instance
            executor._OCR_ENGINE_FACTORY = original_factory

        self.assertEqual([("click", (170, 230))], backend.calls)
        self.assertEqual(1, report.executed_actions)

    def test_ocr_target_action_falls_back_to_static_position(self):
        import numpy as np

        import src.features.drive_assembly.executor as executor

        original_instance = executor._OCR_ENGINE_INSTANCE
        original_factory = executor._OCR_ENGINE_FACTORY
        try:
            executor._OCR_ENGINE_INSTANCE = None
            executor._OCR_ENGINE_FACTORY = lambda: FakeOcrEngine([{"text": "Other", "box": (0, 0, 20, 20)}])
            backend = FakeScreenshotMouseBackend(np.zeros((500, 500, 3), dtype=np.uint8))

            report = executor.execute_action_sequence(
                [
                    {
                        "name": "main_stat_option",
                        "ocr_target_text": "Attack Percent",
                        "ocr_search_region": (100, 200, 300, 420),
                        "fallback_position": (9, 9),
                    }
                ],
                backend=backend,
                pause_seconds=0.0,
            )
        finally:
            executor._OCR_ENGINE_INSTANCE = original_instance
            executor._OCR_ENGINE_FACTORY = original_factory

        self.assertEqual([("click", (9, 9))], backend.calls)
        self.assertEqual(1, report.executed_actions)

    def test_ocr_target_resizes_large_search_region_before_matching(self):
        import numpy as np

        import src.features.drive_assembly.executor as executor

        original_instance = executor._OCR_ENGINE_INSTANCE
        original_factory = executor._OCR_ENGINE_FACTORY
        engine = FakeOcrEngine([{"text": "Attack Percent", "box": (12, 8, 60, 28)}])
        try:
            executor._OCR_ENGINE_INSTANCE = None
            executor._OCR_ENGINE_FACTORY = lambda: engine
            backend = FakeScreenshotMouseBackend(np.zeros((900, 5000, 3), dtype=np.uint8))

            report = executor.execute_action_sequence(
                [
                    {
                        "name": "main_stat_option",
                        "ocr_target_text": "Attack Percent",
                        "ocr_search_region": (0, 0, 4800, 736),
                        "fallback_position": (9, 9),
                    }
                ],
                backend=backend,
                pause_seconds=0.0,
            )
        finally:
            executor._OCR_ENGINE_INSTANCE = original_instance
            executor._OCR_ENGINE_FACTORY = original_factory

        self.assertEqual(1, report.executed_actions)
        self.assertEqual(1, len(engine.images))
        self.assertEqual(np.uint8, engine.images[0].dtype)
        self.assertLessEqual(engine.images[0].shape[1], 1200)
        self.assertEqual([("click", (144, 72))], backend.calls)

    def test_sendinput_drag_uses_long_press_segmented_motion(self):
        from src.features.drive_assembly.executor import (
            MOUSEEVENTF_ABSOLUTE,
            MOUSEEVENTF_LEFTDOWN,
            MOUSEEVENTF_LEFTUP,
            MOUSEEVENTF_MOVE,
            _WindowsSendInputMouseDriver,
        )

        sent = []
        sleeps = []
        driver = _WindowsSendInputMouseDriver.__new__(_WindowsSendInputMouseDriver)
        driver._sleeper = lambda seconds: sleeps.append(round(seconds, 3))
        driver._send = lambda flags, dx=0, dy=0: sent.append((flags, dx, dy))
        driver._move_to = lambda position: sent.append((MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, *position))
        driver._user32 = object()
        driver._input_cls = object()
        driver._mouse_input_cls = object()

        driver.drag((100, 900), (100, 200), 700)

        self.assertEqual((MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 100, 900), sent[0])
        self.assertEqual((MOUSEEVENTF_LEFTDOWN, 0, 0), sent[1])
        self.assertNotIn((MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 100, 200), sent)
        self.assertEqual((MOUSEEVENTF_LEFTUP, 0, 0), sent[-1])
        relative_moves = [call for call in sent if call[0] == MOUSEEVENTF_MOVE]
        self.assertGreaterEqual(len(relative_moves), 50)
        self.assertEqual(0, sum(dx for _flags, dx, _dy in relative_moves))
        self.assertEqual(-700, sum(dy for _flags, _dx, dy in relative_moves))
        self.assertTrue(any(dy < 0 for _flags, _dx, dy in relative_moves))
        self.assertIn(0.3, sleeps)

    def test_backend_uses_sendinput_for_clicks_before_pyautogui(self):
        from src.features.drive_assembly.executor import PyAutoGuiMouseBackend

        calls = []

        class SendInput:
            available = True

            def click(self, position):
                calls.append(("sendinput_click", position))

        class PyAutoGui:
            def mouseUp(self):
                raise AssertionError("pyautogui must not run when SendInput is available")

        backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
        backend._send_input = SendInput()
        backend._pyautogui = PyAutoGui()

        backend.click((320, 240))

        self.assertEqual([("sendinput_click", (320, 240))], calls)

    def test_backend_keeps_pyautogui_for_equipment_dragging(self):
        from src.features.drive_assembly.executor import PyAutoGuiMouseBackend

        calls = []

        class SendInput:
            available = True

        class PyAutoGui:
            def moveTo(self, *position):
                calls.append(("move", position))

            def mouseDown(self, **kwargs):
                calls.append(("down", kwargs))

            def mouseUp(self, **kwargs):
                calls.append(("up", kwargs))

            def dragTo(self, *position, **kwargs):
                calls.append(("drag", position, kwargs))

        backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
        backend._send_input = SendInput()
        backend._pyautogui = PyAutoGui()
        backend._sleeper = lambda seconds: calls.append(("sleep", round(seconds, 3)))

        backend.drag((120, 840), (120, 260), 700)

        down_index = calls.index(("down", {"button": "left"}))
        drag_index = calls.index(("drag", (120, 260), {"duration": 0.7, "button": "left"}))
        final_up_index = max(index for index, call in enumerate(calls) if call == ("up", {"button": "left"}))

        self.assertEqual(("up", {"button": "left"}), calls[0])
        self.assertEqual(("move", (120, 840)), calls[1])
        self.assertLess(down_index, drag_index)
        self.assertLess(drag_index, final_up_index)
        self.assertIn(("sleep", 0.35), calls)

    def test_backend_uses_segmented_sendinput_for_filter_scrolls(self):
        from src.features.drive_assembly.executor import PyAutoGuiMouseBackend

        calls = []

        class SendInput:
            available = True

            def drag(self, start, end, duration_ms):
                calls.append(("scroll", start, end, duration_ms))

        class PyAutoGui:
            def moveTo(self, *_position):
                raise AssertionError("filter scroll should use SendInput")

        backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
        backend._send_input = SendInput()
        backend._pyautogui = PyAutoGui()

        backend.drag_scroll((200, 900), (200, 300), 700)

        self.assertEqual([("scroll", (200, 900), (200, 300), 700)], calls)

    def test_executes_all_ready_role_plans(self):
        from src.features.drive_assembly.executor import execute_all_role_assembly_plan

        backend = FakeMouseBackend()
        plan = {
            "role_plans": [
                {"role_name": "A", "available": True, "actions": [{"name": "install_tape", "sequence": [{"name": "a", "position": (1, 1)}]}]},
                {"role_name": "B", "available": False, "actions": []},
                {"role_name": "C", "available": True, "actions": [{"name": "install_tape", "sequence": [{"name": "c", "position": (3, 3)}]}]},
            ]
        }

        report = execute_all_role_assembly_plan(plan, backend=backend, pause_seconds=0.0)

        self.assertEqual([("click", (1, 1)), ("click", (3, 3))], backend.calls)
        self.assertEqual(["A", "C"], [role.role_name for role in report.role_reports])
        self.assertEqual(2, report.executed_actions)

    def test_executes_role_traversal_and_runs_matching_assembly_plan(self):
        from src.features.drive_assembly.executor import execute_role_traversal_assembly_plan

        backend = FakeMouseBackend()
        traversal_plan = {
            "missing_roles": ["B"],
            "duplicates": [{"role_name": "A", "page_index": 1, "slot_index": 0}],
            "unrecognized": [{"page_index": 0, "slot_index": 4}],
            "plans": [
                {
                    "role_name": "A",
                    "action_sequence": [
                        {"name": "role_slot", "position": (100, 100)},
                        {"name": "left_kongmu_tab", "position": (200, 200)},
                        {"name": "assemble_button", "position": (300, 300)},
                        {"name": "assemble_current_role_from_blueprint", "role_name": "A"},
                    ],
                },
                {
                    "role_name": None,
                    "action_sequence": [
                        {"name": "role_scroll_next_page", "from": (400, 900), "to": (400, 200), "duration_ms": 700}
                    ],
                },
            ]
        }
        assembly_plan = {
            "role_plans": [
                {"role_name": "A", "available": True, "actions": [{"name": "install_tape", "sequence": [{"name": "filter", "position": (10, 10)}]}]},
            ]
        }

        report = execute_role_traversal_assembly_plan(
            traversal_plan,
            assembly_plan,
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual(
            [
                ("click", (100, 100)),
                ("click", (200, 200)),
                ("click", (300, 300)),
                ("click", (10, 10)),
                ("drag", (400, 900), (400, 200), 700),
            ],
            backend.calls,
        )
        self.assertEqual(["A"], [role.role_name for role in report.role_reports])
        self.assertEqual(5, report.executed_actions)
        self.assertEqual(["B"], report.missing_roles)
        self.assertEqual(1, len(report.duplicate_roles))
        self.assertEqual(1, len(report.unrecognized_roles))

    def test_role_traversal_collects_verification_failures(self):
        from src.features.drive_assembly.executor import execute_role_traversal_assembly_plan

        backend = FakeMouseBackend()
        traversal_plan = {
            "plans": [
                {"role_name": "A", "action_sequence": [{"name": "run_drive_assembly_for_role", "role_name": "A"}]}
            ]
        }
        assembly_plan = {
            "role_plans": [
                {"role_name": "A", "available": True, "actions": [{"name": "install_tape", "sequence": []}]},
            ]
        }

        report = execute_role_traversal_assembly_plan(
            traversal_plan,
            assembly_plan,
            backend=backend,
            pause_seconds=0.0,
            role_verifier=lambda role_name, _plan: {"ok": False, "reason": role_name},
        )

        self.assertEqual([{"role_name": "A", "ok": False, "reason": "A"}], report.verification_failures)

    def test_role_traversal_reports_missing_assembly_payload(self):
        from src.features.drive_assembly.executor import execute_role_traversal_assembly_plan

        backend = FakeMouseBackend()
        traversal_plan = {
            "plans": [
                {
                    "role_name": "A",
                    "action_sequence": [
                        {"name": "role_slot", "position": (100, 100)},
                        {"name": "run_drive_assembly_for_role", "role_name": "A"},
                    ],
                }
            ]
        }
        assembly_plan = {"role_plans": []}

        report = execute_role_traversal_assembly_plan(
            traversal_plan,
            assembly_plan,
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("click", (100, 100))], backend.calls)
        self.assertEqual(["A"], report.skipped_roles)
        self.assertEqual(1, report.executed_actions)


if __name__ == "__main__":
    unittest.main()
