# 测试截图解析和重复过滤辅助逻辑。
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from src.features.scanning.file_lifecycle import (
    ScanFileLifecycle,
    build_screenshot_cleanup_plan,
    execute_screenshot_cleanup,
    managed_screenshot_usage,
)




class IncrementalBaselineTests(unittest.TestCase):
    def test_screenshot_cleanup_policy_preserves_incremental_baseline_and_cleans_temp_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            account_root = Path(tmp)
            screenshot_dir = account_root / "scanned_images"
            screenshot_dir.mkdir()
            baseline = screenshot_dir / "raw_drive_0001.png"
            extra = screenshot_dir / "raw_drive_0002.png"
            nested = screenshot_dir / "failed" / "raw_drive_bad.png"
            temp = account_root / "identify_clipboard_123.png"
            other_account_file = account_root / "manual.png"
            nested.parent.mkdir()
            baseline.write_bytes(b"baseline")
            extra.write_bytes(b"extra")
            nested.write_bytes(b"nested")
            temp.write_bytes(b"temp")
            other_account_file.write_bytes(b"manual")

            usage = managed_screenshot_usage(screenshot_dir, account_root)
            plan = build_screenshot_cleanup_plan(screenshot_dir, account_root)
            result = execute_screenshot_cleanup(plan)

            self.assertEqual(4, usage.count)
            self.assertEqual(3, plan.total_count)
            self.assertEqual(2, plan.scan_delete_count)
            self.assertEqual(1, plan.temp_delete_count)
            self.assertFalse(plan.baseline_missing)
            self.assertEqual(3, result.deleted)
            self.assertTrue(baseline.exists())
            self.assertTrue(other_account_file.exists())
            self.assertFalse(extra.exists())
            self.assertFalse(nested.exists())
            self.assertFalse(temp.exists())

    def test_screenshot_cleanup_policy_warns_when_scan_images_exist_without_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            account_root = Path(tmp)
            screenshot_dir = account_root / "scanned_images"
            screenshot_dir.mkdir()
            (screenshot_dir / "raw_drive_0002.png").write_bytes(b"extra")

            plan = build_screenshot_cleanup_plan(screenshot_dir, account_root)

            self.assertTrue(plan.baseline_missing)
            self.assertIn("丢失用于对比的截图", plan.confirmation_text())

    def test_corrupt_raw_drive_0001_marks_incremental_baseline_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            screenshot_dir = root / "scanned_images"
            screenshot_dir.mkdir()
            (screenshot_dir / "raw_drive_probe_0001.png").write_bytes(b"not an image")
            (screenshot_dir / "raw_drive_0001.png").write_bytes(b"not an image")

            lifecycle = ScanFileLifecycle(
                screenshot_dir=screenshot_dir,
                output_file=root / "config" / "real_inventory.json",
                config_dir=root / "config",
            )
            result = lifecycle.prepare_incremental_parse("incremental_auto")

        self.assertTrue(result.baseline_missing)

    def test_failed_incremental_probe_does_not_replace_raw_drive_0001(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            screenshot_dir = root / "scanned_images"
            screenshot_dir.mkdir()
            baseline = screenshot_dir / "raw_drive_0001.png"
            probe = screenshot_dir / "raw_drive_probe_0001.png"
            baseline.write_bytes(b"baseline")
            probe.write_bytes(b"probe")

            lifecycle = ScanFileLifecycle(
                screenshot_dir=screenshot_dir,
                output_file=root / "config" / "real_inventory.json",
                config_dir=root / "config",
            )
            post = lifecycle.postprocess_vision_files(
                {
                    "parse_scope": "incremental_auto",
                    "added_paths": [],
                    "duplicate_paths": [],
                    "failed_paths": [str(probe)],
                }
            )

            self.assertTrue(baseline.exists())
            self.assertEqual(b"baseline", baseline.read_bytes())
            self.assertFalse(probe.exists())
            self.assertTrue((screenshot_dir / "failed" / "raw_drive_probe_0001.png").exists())
            self.assertEqual(1, post["moved_failed"])
            self.assertEqual(0, post["renamed"])


class GamepadScannerTests(unittest.TestCase):
    def test_close_resets_and_releases_virtual_gamepad_reference(self):
        from src.scanner import gamepad_controller

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        calls = []
        scanner._closed = False
        scanner.gamepad = SimpleNamespace(
            reset=lambda: calls.append("reset"),
            update=lambda: calls.append("update"),
        )
        scanner._buttons = object()
        original_collect = gamepad_controller.gc.collect
        gamepad_controller.gc.collect = lambda: calls.append("gc")
        try:
            scanner.close()
            scanner.close()
        finally:
            gamepad_controller.gc.collect = original_collect

        self.assertEqual(["reset", "update", "gc"], calls)
        self.assertIsNone(scanner.gamepad)
        self.assertIsNone(scanner._buttons)
        self.assertTrue(scanner._closed)

    def test_capture_panel_uses_mss_png_writer(self):
        from src.scanner import gamepad_controller

        class FakeScreenshot:
            size = (2, 2)
            rgb = b"\x00" * 2 * 2 * 3

        original_to_png = gamepad_controller.mss.tools.to_png
        calls = []

        def fake_to_png(rgb, size, output):
            calls.append((rgb, size, output))

        gamepad_controller.mss.tools.to_png = fake_to_png
        try:
            gamepad_controller._save_png(FakeScreenshot(), "unused.png")
        finally:
            gamepad_controller.mss.tools.to_png = original_to_png

        self.assertEqual([(FakeScreenshot.rgb, FakeScreenshot.size, "unused.png")], calls)

    def test_push_left_joystick_uses_lenient_timing(self):
        from src.scanner import gamepad_controller

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        updates = []
        sleeps = []
        scanner.gamepad = SimpleNamespace(
            left_joystick_float=lambda **kwargs: updates.append(kwargs),
            update=lambda: updates.append("update"),
        )
        original_sleep = gamepad_controller.time.sleep
        gamepad_controller.time.sleep = lambda seconds, *_args, **_kwargs: sleeps.append(seconds)
        try:
            scanner.push_left_joystick(1.0, 0.0)
        finally:
            gamepad_controller.time.sleep = original_sleep

        self.assertEqual([0.10, 0.25], sleeps)
        self.assertEqual(
            [
                {"x_value_float": 1.0, "y_value_float": 0.0},
                "update",
                {"x_value_float": 0.0, "y_value_float": 0.0},
                "update",
            ],
            updates,
        )

    def test_apply_moves_uses_more_stable_timing_for_row_transition_down(self):
        from src.scanner import gamepad_controller

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        updates = []
        sleeps = []
        scanner.gamepad = SimpleNamespace(
            left_joystick_float=lambda **kwargs: updates.append(kwargs),
            update=lambda: updates.append("update"),
        )
        original_sleep = gamepad_controller.time.sleep
        gamepad_controller.time.sleep = lambda seconds, *_args, **_kwargs: sleeps.append(seconds)
        try:
            scanner._apply_moves(["R", "D"])
        finally:
            gamepad_controller.time.sleep = original_sleep

        self.assertEqual([0.10, 0.25, 0.15, 0.30], sleeps)

    def test_capture_panel_saves_single_current_frame_without_waiting_for_change(self):
        from src.scanner import gamepad_controller

        class FakeScreenshot:
            size = (4, 4)
            rgb = b"\x00" * 4 * 4 * 3

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        scanner.capture_dir = "unused"

        original_capture = gamepad_controller.capture_foreground_window
        original_save_png = gamepad_controller._save_png
        original_sleep = gamepad_controller.time.sleep
        writes = []
        captures = []
        sleeps = []

        def fake_capture(_sct):
            captures.append(True)
            return FakeScreenshot(), None

        gamepad_controller.capture_foreground_window = fake_capture
        gamepad_controller._save_png = lambda *_args, **_kwargs: writes.append(True)
        gamepad_controller.time.sleep = lambda seconds, *_args, **_kwargs: sleeps.append(seconds)
        try:
            captured = scanner.capture_panel(object(), 1)
        finally:
            gamepad_controller.capture_foreground_window = original_capture
            gamepad_controller._save_png = original_save_png
            gamepad_controller.time.sleep = original_sleep

        self.assertTrue(captured)
        self.assertEqual([True], writes)
        self.assertEqual([True], captures)
        self.assertEqual([], sleeps)

    def test_start_scan_does_not_retry_move_when_capture_is_stale(self):
        from src.scanner import gamepad_controller

        class FakeScreenshot:
            size = (4, 4)
            rgb = b"\x00" * 4 * 4 * 3

            def __init__(self, value):
                self.value = value

            def __array__(self, dtype=None):
                arr = np.full((4, 4, 4), self.value, dtype=np.uint8)
                return arr.astype(dtype) if dtype is not None else arr

        class FakeMSS:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        scanner.output_dir = "unused"
        scanner.capture_dir = "unused"
        scanner._stopped = False
        scanner.cols = 7
        moves = []
        commits = []
        scanner.push_left_joystick = lambda x, y: moves.append((x, y))
        scanner._prepare_temp_output = lambda: None
        scanner._commit_temp_output = lambda: commits.append(True)
        reset_swipes = []
        scanner._drag_inventory_list_to_top = lambda: reset_swipes.append(True)

        frames = [FakeScreenshot(1), FakeScreenshot(1)]

        original_capture = gamepad_controller.capture_foreground_window
        original_save_png = gamepad_controller._save_png
        original_mss = gamepad_controller.mss.MSS
        original_sleep = gamepad_controller.time.sleep
        writes = []
        gamepad_controller.capture_foreground_window = lambda _sct: (frames.pop(0), None)
        gamepad_controller._save_png = lambda *_args, **_kwargs: writes.append(True)
        gamepad_controller.mss.MSS = FakeMSS
        gamepad_controller.time.sleep = lambda *_args, **_kwargs: None
        try:
            count = scanner.start_scan(2)
        finally:
            gamepad_controller.capture_foreground_window = original_capture
            gamepad_controller._save_png = original_save_png
            gamepad_controller.mss.MSS = original_mss
            gamepad_controller.time.sleep = original_sleep

        right_moves = [move for move in moves if move == (1.0, 0.0)]
        self.assertEqual(2, count)
        self.assertEqual(2, len(writes))
        self.assertEqual(1, len(right_moves))
        self.assertEqual([True], commits)
        self.assertEqual([True], reset_swipes)

    def test_start_scan_can_notify_captures_before_deferred_commit(self):
        from src.scanner import gamepad_controller

        class FakeScreenshot:
            size = (4, 4)
            rgb = b"\x00" * 4 * 4 * 3

        class FakeMSS:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with tempfile.TemporaryDirectory() as tmp:
            scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
            scanner.output_dir = tmp
            scanner.capture_dir = tmp
            scanner._stopped = False
            scanner.cols = 7
            scanner.push_left_joystick = lambda *_args, **_kwargs: None
            reset_swipes = []
            scanner._drag_inventory_list_to_top = lambda: reset_swipes.append(True)

            original_capture = gamepad_controller.capture_foreground_window
            original_save_png = gamepad_controller._save_png
            original_mss = gamepad_controller.mss.MSS
            original_sleep = gamepad_controller.time.sleep
            commits = []
            notifications = []
            gamepad_controller.capture_foreground_window = lambda _sct: (FakeScreenshot(), None)
            gamepad_controller._save_png = lambda _screenshot, filename: Path(filename).write_bytes(b"png")
            gamepad_controller.mss.MSS = FakeMSS
            gamepad_controller.time.sleep = lambda *_args, **_kwargs: None
            scanner._commit_temp_output = lambda: commits.append(True)
            try:
                count = scanner.start_scan(
                    2,
                    on_capture=lambda path, index, total: notifications.append((Path(path).name, index, total)),
                    commit_on_complete=False,
                )
            finally:
                gamepad_controller.capture_foreground_window = original_capture
                gamepad_controller._save_png = original_save_png
                gamepad_controller.mss.MSS = original_mss
                gamepad_controller.time.sleep = original_sleep

        self.assertEqual(2, count)
        self.assertEqual(
            [("raw_drive_0001.png", 1, 2), ("raw_drive_0002.png", 2, 2)],
            notifications,
        )
        self.assertEqual([], commits)
        self.assertEqual([True], reset_swipes)

    def test_gamepad_list_reset_swipe_matches_reversed_incremental_motion(self):
        from src.scanner import gamepad_controller
        from src.scanner.window_capture import WindowRect

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        scanner._stopped = False
        drags = []
        scanner._inventory_reset_input = SimpleNamespace(
            drag=lambda start, end, **kwargs: drags.append((start, end, kwargs))
        )
        original_rect = gamepad_controller.get_foreground_client_rect
        gamepad_controller.get_foreground_client_rect = lambda: WindowRect(40, 60, 2600, 1500)
        try:
            scanner._drag_inventory_list_to_top()
        finally:
            gamepad_controller.get_foreground_client_rect = original_rect

        self.assertEqual(
            [((1320, 360), (1320, 1360), {"hold_seconds": 0.3, "duration_seconds": 0.6})],
            drags,
        )

    def test_sync_equipment_state_menu_sequences(self):
        from src.scanner import gamepad_controller

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        scanner.cols = 7
        scanner._stopped = False
        moves = []
        presses = []
        scanner._apply_moves = lambda batch: moves.append(batch)
        scanner._press_menu = lambda: presses.append("menu")
        scanner._press_a = lambda: presses.append("a")

        cases = [
            ("normal", "discarded", ["menu", "a", "menu"], [], [0.3, 0.3, 0.3]),
            ("locked", "discarded", ["menu", "a", "a", "menu"], [], [0.3, 0.6, 0.6, 0.3]),
            ("discarded", "locked", ["menu", "a", "menu"], [["R"]], [0.3, 0.15, 0.3, 0.3]),
            ("normal", "locked", ["menu", "a", "menu"], [["R"]], [0.3, 0.15, 0.3, 0.3]),
            ("locked", "normal", ["menu", "a", "menu"], [["R"]], [0.3, 0.15, 0.3, 0.3]),
            ("discarded", "normal", ["menu", "a", "menu"], [], [0.3, 0.3, 0.3]),
        ]

        original_sleep = gamepad_controller.time.sleep
        pauses = []
        gamepad_controller.time.sleep = lambda seconds: pauses.append(seconds)
        try:
            for current, target, expected_presses, expected_moves, expected_pauses in cases:
                presses.clear()
                moves.clear()
                pauses.clear()
                changed = scanner._sync_selected_equipment_state(current, target)
                self.assertTrue(changed)
                self.assertEqual(expected_presses, presses)
                self.assertEqual(expected_moves, moves)
                self.assertEqual(expected_pauses, pauses)
        finally:
            gamepad_controller.time.sleep = original_sleep

    def test_sync_equipment_state_hmt_uses_dpad_without_menu(self):
        from src.scanner import gamepad_controller

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        presses = []
        scanner._press_dpad_left = lambda: presses.append("dpad_left")
        scanner._press_dpad_right = lambda: presses.append("dpad_right")
        scanner._press_a = lambda: presses.append("a")

        cases = [
            ("normal", "discarded", ["dpad_left"], [0.3]),
            ("discarded", "normal", ["dpad_left"], [0.3]),
            ("normal", "locked", ["dpad_right"], [0.3]),
            ("locked", "normal", ["dpad_right"], [0.3]),
            ("discarded", "locked", ["dpad_right"], [0.3]),
            ("locked", "discarded", ["dpad_left", "a"], [0.6, 0.6]),
        ]

        original_sleep = gamepad_controller.time.sleep
        pauses = []
        gamepad_controller.time.sleep = lambda seconds: pauses.append(seconds)
        try:
            for current, target, expected_presses, expected_pauses in cases:
                presses.clear()
                pauses.clear()
                changed = scanner._sync_selected_equipment_state_hmt(current, target)
                self.assertTrue(changed)
                self.assertEqual(expected_presses, presses)
                self.assertEqual(expected_pauses, pauses)
        finally:
            gamepad_controller.time.sleep = original_sleep

    def test_sync_equipment_states_activates_then_moves_backward_from_last_item(self):
        from src.scanner import gamepad_controller

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        scanner.cols = 7
        scanner._stopped = False
        moves = []
        presses = []
        down_sticks = []
        scanner._apply_moves = lambda batch: moves.append(batch)
        scanner.push_left_joystick = lambda x, y, **_kwargs: down_sticks.append((x, y))
        scanner._press_menu = lambda: presses.append("menu")
        scanner._press_a = lambda: presses.append("a")

        original_sleep = gamepad_controller.time.sleep
        pauses = []
        gamepad_controller.time.sleep = lambda seconds: pauses.append(seconds)
        try:
            applied = scanner.sync_equipment_states(
                14,
                [
                    {"index": 8, "current_state": "normal", "target_state": "locked"},
                    {"index": 2, "current_state": "discarded", "target_state": "normal"},
                ],
            )
        finally:
            gamepad_controller.time.sleep = original_sleep

        self.assertEqual(2, applied)
        self.assertEqual([(0.0, -1.0), (0.0, -1.0)], down_sticks)
        self.assertEqual(["menu", "a", "menu", "menu", "a", "menu"], presses)
        self.assertEqual([[], ["R"], ["U", "L", "L", "L", "L", "L"]], moves)
        self.assertEqual(
            [0.3, 0.15, 0.3, 0.3, 0.2, 0.2, 0.3, 0.3, 0.3],
            pauses,
        )

    def test_sync_equipment_states_starts_from_odd_or_even_final_scan_row(self):
        from src.scanner import gamepad_controller

        cases = [
            # 13 items end on the odd S row at column 0, but down-stick focus
            # is the physical final cell at column 5.
            (13, (1, 0), (1, 5), [["L", "L", "L", "L", "L"], ["R"], ["R", "R", "R", "R", "R"]]),
            # 15 items end on the even third row at column 0, matching the
            # final physical cell.
            (15, (2, 0), (2, 0), [[], ["R"], ["U", "R", "R", "R", "R", "R", "R"]]),
        ]
        original_sleep = gamepad_controller.time.sleep
        gamepad_controller.time.sleep = lambda _seconds: None
        try:
            for total_drives, expected_scan_end, expected_origin, expected_moves in cases:
                with self.subTest(total_drives=total_drives):
                    scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
                    scanner.cols = 7
                    scanner._stopped = False
                    moves = []
                    presses = []
                    down_sticks = []
                    scanner._apply_moves = lambda batch: moves.append(list(batch))
                    scanner.push_left_joystick = lambda x, y, **_kwargs: down_sticks.append((x, y))
                    scanner._press_menu = lambda: presses.append("menu")
                    scanner._press_a = lambda: presses.append("a")

                    self.assertEqual(expected_scan_end, scanner._scan_positions(total_drives)[-1])
                    self.assertEqual(expected_origin, scanner._last_inventory_position(total_drives))
                    applied = scanner.sync_equipment_states(
                        total_drives,
                        [
                            {"index": total_drives, "current_state": "normal", "target_state": "locked"},
                            {"index": 8, "current_state": "normal", "target_state": "discarded"},
                        ],
                    )

                    self.assertEqual(2, applied)
                    self.assertEqual([(0.0, -1.0), (0.0, -1.0)], down_sticks)
                    self.assertEqual(["menu", "a", "menu", "menu", "a", "menu"], presses)
                    self.assertEqual(expected_moves, moves)
        finally:
            gamepad_controller.time.sleep = original_sleep

    def test_gamepad_vision_profile_is_bound_to_action_profile(self):
        from src.scanner.gamepad_controller import GamepadActionProfile

        cn_profile = GamepadActionProfile.state_management("cn")
        hmt_profile = GamepadActionProfile.state_management("hmt")

        self.assertEqual("cn", cn_profile.vision.region)
        self.assertEqual("hmt", hmt_profile.vision.region)
        self.assertTrue(cn_profile.vision.detail_page_rules)
        self.assertTrue(hmt_profile.vision.inventory_first_item_rules)

    def test_gamepad_vision_rule_debug_reason_reports_failed_rule(self):
        from src.scanner import gamepad_controller

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        image = np.zeros((10, 10, 3), dtype=np.uint8)
        rules = (
            gamepad_controller.GamepadVisionRule("全黑区域白色占比", (0.0, 0.0, 1.0, 1.0), "white", "gt", 0.5),
        )

        matched, reason = scanner._evaluate_vision_rules(image, rules)

        self.assertFalse(matched)
        self.assertIn("全黑区域白色占比=0.000gt0.500:fail", reason)

    def test_gamepad_vision_roi_uses_top_aligned_16_9_canvas_on_16_10(self):
        from src.scanner import gamepad_controller

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        image = np.zeros((1600, 2560, 3), dtype=np.uint8)

        roi = scanner._relative_roi(image, 0.68, 0.20, 0.98, 0.40)

        self.assertEqual((288, 768, 3), roi.shape)

    def test_detail_page_detection_accepts_common_max_level_layout(self):
        from src.scanner import gamepad_controller

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        scanner.action_profile = gamepad_controller.GamepadActionProfile.state_management("cn")
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        image[20:55, 68:98] = 80

        matched, reason = scanner._looks_like_detail_page(image)

        self.assertTrue(matched)
        self.assertIn("详情规则组2", reason)

if __name__ == "__main__":
    unittest.main()
