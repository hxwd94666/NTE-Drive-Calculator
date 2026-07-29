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


class DriveAssemblyInputBackendTests(unittest.TestCase):
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
        import src.features.drive_assembly.assembly_ocr as assembly_ocr

        original_instance = assembly_ocr._OCR_ENGINE_INSTANCE
        original_factory = assembly_ocr._OCR_ENGINE_FACTORY
        try:
            assembly_ocr._OCR_ENGINE_INSTANCE = None
            assembly_ocr._OCR_ENGINE_FACTORY = lambda: FakeOcrEngine(
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
            assembly_ocr._OCR_ENGINE_INSTANCE = original_instance
            assembly_ocr._OCR_ENGINE_FACTORY = original_factory

        self.assertEqual([("click", (170, 230))], backend.calls)
        self.assertEqual(1, report.executed_actions)

    def test_ocr_target_action_falls_back_to_static_position(self):
        import numpy as np

        import src.features.drive_assembly.executor as executor
        import src.features.drive_assembly.assembly_ocr as assembly_ocr

        original_instance = assembly_ocr._OCR_ENGINE_INSTANCE
        original_factory = assembly_ocr._OCR_ENGINE_FACTORY
        try:
            assembly_ocr._OCR_ENGINE_INSTANCE = None
            assembly_ocr._OCR_ENGINE_FACTORY = lambda: FakeOcrEngine([{"text": "Other", "box": (0, 0, 20, 20)}])
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
            assembly_ocr._OCR_ENGINE_INSTANCE = original_instance
            assembly_ocr._OCR_ENGINE_FACTORY = original_factory

        self.assertEqual([("click", (9, 9))], backend.calls)
        self.assertEqual(1, report.executed_actions)

    def test_ocr_target_resizes_large_search_region_before_matching(self):
        import numpy as np

        import src.features.drive_assembly.executor as executor
        import src.features.drive_assembly.assembly_ocr as assembly_ocr

        original_instance = assembly_ocr._OCR_ENGINE_INSTANCE
        original_factory = assembly_ocr._OCR_ENGINE_FACTORY
        engine = FakeOcrEngine([{"text": "Attack Percent", "box": (12, 8, 60, 28)}])
        try:
            assembly_ocr._OCR_ENGINE_INSTANCE = None
            assembly_ocr._OCR_ENGINE_FACTORY = lambda: engine
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
            assembly_ocr._OCR_ENGINE_INSTANCE = original_instance
            assembly_ocr._OCR_ENGINE_FACTORY = original_factory

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

            def click(self, position, hold_seconds=None):
                calls.append(("sendinput_click", position))

        class PyAutoGui:
            def mouseUp(self):
                raise AssertionError("pyautogui must not run when SendInput is available")

        backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
        backend._send_input = SendInput()
        backend._pyautogui = PyAutoGui()

        backend.click((320, 240))

        self.assertEqual([("sendinput_click", (320, 240))], calls)

    def test_backend_keeps_mouse_pressed_through_equipment_dragging(self):
        from src.features.drive_assembly.executor import PyAutoGuiMouseBackend

        calls = []

        class SendInput:
            available = True

        class PyAutoGui:
            def moveTo(self, *position, **kwargs):
                calls.append(("move", position, kwargs))

            def mouseDown(self, **kwargs):
                calls.append(("down", kwargs))

            def mouseUp(self, **kwargs):
                calls.append(("up", kwargs))

        backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
        backend._send_input = SendInput()
        backend._pyautogui = PyAutoGui()
        backend._sleeper = lambda seconds: calls.append(("sleep", round(seconds, 3)))

        backend.drag((120, 840), (720, 260), 700)

        down_index = calls.index(("down", {"button": "left"}))
        drag_move_index = next(
            index for index, call in enumerate(calls)
            if call[0] == "move" and call[1] == (720, 260)
        )
        final_up_index = max(index for index, call in enumerate(calls) if call == ("up", {"button": "left"}))

        self.assertEqual(("up", {"button": "left"}), calls[0])
        self.assertEqual(("move", (120, 840), {}), calls[1])
        self.assertLess(down_index, drag_move_index)
        self.assertLess(drag_move_index, final_up_index)
        self.assertEqual(1, len([call for call in calls if call[0] == "down"]))
        self.assertAlmostEqual(0.7, sum(call[2]["duration"] for call in calls if call[0] == "move" and call[2]))
        self.assertIn(("sleep", 0.45), calls)

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

if __name__ == "__main__":
    unittest.main()
