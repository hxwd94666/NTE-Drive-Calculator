# 验证鼠标全量视觉扫描的布局、随机输入和滚轮反馈公共契约。
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from src.integrations.vision.mouse_inventory_scan import (
    MouseCapturedFrame,
    MouseInventoryLayout,
    MouseInventoryScanner,
    MouseScanSlot,
)
from src.integrations.vision.mouse_scan_runtime import (
    probe_mouse_scan_runtime,
    require_mouse_scan_runtime,
)
from src.integrations.vision.mouse_scan_grid import detect_grid_occupancy
from src.scanner.window_capture import WindowRect


class MouseInventoryLayoutTests(unittest.TestCase):
    def test_supplied_2k_inventory_image_maps_to_seven_columns_and_four_rows(self) -> None:
        layout = MouseInventoryLayout()

        centers = [
            layout.cell_center(row=row, column=column, target_width=2560, target_height=1440)
            for row in range(4)
            for column in range(7)
        ]

        self.assertEqual((254, 322), centers[0])
        self.assertEqual((1576, 322), centers[6])
        self.assertEqual((254, 1101), centers[21])
        self.assertEqual((1576, 1101), centers[-1])

    def test_layout_scales_to_1080p_and_4k(self) -> None:
        layout = MouseInventoryLayout()

        self.assertEqual((191, 242), layout.cell_center(0, 0, 1920, 1080))
        self.assertEqual((1182, 826), layout.cell_center(3, 6, 1920, 1080))
        self.assertEqual((381, 483), layout.cell_center(0, 0, 3840, 2160))
        self.assertEqual((2364, 1652), layout.cell_center(3, 6, 3840, 2160))

    def test_tall_client_uses_top_aligned_16_9_content(self) -> None:
        layout = MouseInventoryLayout()

        self.assertEqual((254, 322), layout.cell_center(0, 0, 2560, 1600))
        self.assertEqual((1576, 1101), layout.cell_center(3, 6, 2560, 1600))

    def test_tracked_item_center_maps_to_the_nearest_visible_row(self) -> None:
        layout = MouseInventoryLayout()

        self.assertEqual(0, layout.nearest_visible_row(322, 2560, 1440))
        self.assertEqual(2, layout.nearest_visible_row(841, 2560, 1440))

    def test_initial_frame_preflight_accepts_expected_grid_at_1080p_2k_and_4k(self) -> None:
        layout = MouseInventoryLayout()
        for width, height in ((1920, 1080), (2560, 1440), (3840, 2160)):
            with self.subTest(resolution=(width, height)):
                image = np.zeros((height, width, 3), dtype=np.uint8)
                scale = height / 1440
                half_width = round(65 * scale)
                half_height = round(55 * scale)
                for row in range(4):
                    for column in range(7):
                        x, y = layout.cell_center(row, column, width, height)
                        image[
                            y - half_height : y + half_height,
                            x - half_width : x + half_width,
                        ] = (20, 160, 245)

                report = layout.verify_initial_frame(image, 277)

                self.assertTrue(report.valid)
                self.assertEqual((28, 28), (report.matched_slots, report.checked_slots))

    def test_initial_frame_preflight_rejects_wrong_or_unloaded_screen(self) -> None:
        report = MouseInventoryLayout().verify_initial_frame(
            np.zeros((1080, 1920, 3), dtype=np.uint8),
            277,
        )

        self.assertFalse(report.valid)
        self.assertEqual(0, report.matched_slots)

    def test_fixed_scale_viewport_can_freeze_native_pixel_grid(self) -> None:
        layout = MouseInventoryLayout()
        image = np.zeros((1152, 2048, 3), dtype=np.uint8)
        for row in range(4):
            for column in range(7):
                x = round(layout.first_center_x + column * layout.spacing_x)
                y = round(layout.first_center_y + row * layout.spacing_y)
                image[max(0, y - 45) : min(1152, y + 45), x - 55 : x + 55] = (20, 160, 245)

        calibrated, report = layout.calibrate_initial_frame(image, 277)

        self.assertTrue(report.valid)
        self.assertEqual((2048, 1152), (calibrated.base_width, calibrated.base_height))
        self.assertEqual((254, 322), calibrated.cell_center(0, 0, 2048, 1152))
        self.assertEqual((1576, 1101), calibrated.cell_center(3, 6, 2048, 1152))

    def test_first_page_scans_four_rows_and_later_pages_scan_three_new_rows(self) -> None:
        pages = MouseInventoryLayout().page_slots(50)

        self.assertEqual(3, len(pages))
        self.assertEqual(list(range(1, 29)), [slot.index for slot in pages[0]])
        self.assertEqual({0, 1, 2, 3}, {slot.row for slot in pages[0]})
        self.assertEqual(list(range(29, 50)), [slot.index for slot in pages[1]])
        self.assertEqual({1, 2, 3}, {slot.row for slot in pages[1]})
        self.assertEqual([50], [slot.index for slot in pages[2]])
        self.assertEqual((3, 0), (pages[2][0].row, pages[2][0].column))

    def test_final_page_uses_entered_count_to_map_bottom_aligned_tail(self) -> None:
        layout = MouseInventoryLayout()

        twelve_tail = layout.page_slots(40)[-1]
        fourteen_tail = layout.page_slots(42)[-1]

        self.assertEqual(list(range(29, 41)), [slot.index for slot in twelve_tail])
        self.assertEqual((2, 0), (twelve_tail[0].row, twelve_tail[0].column))
        self.assertEqual((3, 4), (twelve_tail[-1].row, twelve_tail[-1].column))
        self.assertEqual((2, 0), (fourteen_tail[0].row, fourteen_tail[0].column))
        self.assertEqual((3, 6), (fourteen_tail[-1].row, fourteen_tail[-1].column))

    def test_all_later_page_tail_shapes_stay_inside_bottom_aligned_viewport(self) -> None:
        layout = MouseInventoryLayout()
        for remaining in range(1, 22):
            with self.subTest(remaining=remaining):
                total = 28 + remaining
                final_page = layout.page_slots(total)[-1]
                total_rows = (total + layout.columns - 1) // layout.columns
                first_visible_row = max(0, total_rows - layout.visible_rows)
                expected_positions = [
                    (
                        (index - 1) // layout.columns - first_visible_row,
                        (index - 1) % layout.columns,
                    )
                    for index in range(29, total + 1)
                ]

                self.assertEqual(expected_positions, [(slot.row, slot.column) for slot in final_page])
                self.assertEqual(remaining, len(set(expected_positions)))

    def test_partial_final_page_keeps_contiguous_inventory_order(self) -> None:
        pages = MouseInventoryLayout().page_slots(277)
        flattened = [slot.index for page in pages for slot in page]

        self.assertEqual(13, len(pages))
        self.assertEqual(list(range(1, 278)), flattened)
        self.assertEqual(18, len(pages[-1]))
        self.assertEqual((3, 3), (pages[-1][-1].row, pages[-1][-1].column))

    def test_2000_items_have_exact_final_page_shape(self) -> None:
        pages = MouseInventoryLayout().page_slots(2000)

        self.assertEqual(95, len(pages))
        self.assertEqual(19, len(pages[-1]))
        self.assertEqual({1, 2, 3}, {slot.row for slot in pages[-1]})
        self.assertEqual((3, 4), (pages[-1][-1].row, pages[-1][-1].column))


class MouseScanRuntimeTests(unittest.TestCase):
    def test_runtime_probe_opens_capture_backend_without_sending_input(self) -> None:
        input_calls = []
        capture_closed = []

        class PyAutoGui:
            __version__ = "test"

            @staticmethod
            def moveTo(*_args, **_kwargs):
                input_calls.append("move")

            @staticmethod
            def mouseDown(*_args, **_kwargs):
                input_calls.append("down")

            @staticmethod
            def mouseUp(*_args, **_kwargs):
                input_calls.append("up")

        class Capture:
            def close(self):
                capture_closed.append(True)

        class Mss:
            __version__ = "test"
            MSS = Capture

        class Module:
            __version__ = "test"

        class User32:
            mouse_event = staticmethod(lambda *_args: None)
            GetForegroundWindow = staticmethod(lambda: 1)
            GetClientRect = staticmethod(lambda *_args: 1)
            ClientToScreen = staticmethod(lambda *_args: 1)

        modules = {"mss": Mss, "pyautogui": PyAutoGui, "cv2": Module, "numpy": Module}

        report = probe_mouse_scan_runtime(
            module_loader=lambda name: modules[name],
            user32=User32(),
        )

        self.assertTrue(report.ok)
        self.assertEqual([], input_calls)
        self.assertEqual([True], capture_closed)

    def test_runtime_requirement_names_missing_dependency(self) -> None:
        def loader(name):
            if name == "pyautogui":
                raise ModuleNotFoundError(name)
            return type("Module", (), {"__version__": "test", "MSS": lambda: None})

        report = probe_mouse_scan_runtime(module_loader=loader, user32=object())

        self.assertFalse(report.ok)
        with self.assertRaisesRegex(RuntimeError, "pyautogui"):
            require_mouse_scan_runtime(report)


class MouseScanGridTests(unittest.TestCase):

    def test_bottom_grid_counts_only_contiguous_real_slots(self) -> None:
        image = np.zeros((1440, 2560, 3), dtype=np.uint8)
        layout = MouseInventoryLayout()
        centers = []
        for row in range(1, 4):
            for column in range(7):
                centers.append(layout.cell_center(row, column, 2560, 1440))
        for center_x, center_y in centers[:18]:
            image[center_y - 55 : center_y + 55, center_x - 65 : center_x + 65] = (20, 160, 245)

        result = detect_grid_occupancy(image, tuple(centers), scale=1.0)

        self.assertEqual(18, result.contiguous_count)
        self.assertFalse(result.has_gap)
        self.assertEqual((True,) * 18 + (False,) * 3, result.occupied)

class MouseInventoryScannerTests(unittest.TestCase):
    class _Frames:
        def __init__(self, layout):
            self.layout = layout
            self.selected = None
            self.closed = False
            self.rect = WindowRect(40, 60, 2600, 1500)
            self.occupied_start_row = 0
            self.occupied_count = 28

        def capture(self):
            image = np.zeros((1440, 2560, 3), dtype=np.uint8)
            image[288:1181, 1792:2432] = 72
            remaining = self.occupied_count
            for row in range(self.occupied_start_row, 4):
                for column in range(7):
                    if remaining <= 0:
                        break
                    x, y = self.layout.cell_center(row, column, 2560, 1440)
                    image[y - 55 : y + 55, x - 65 : x + 65] = (20, 160, 245)
                    remaining -= 1
                if remaining <= 0:
                    break
            if self.selected is not None:
                x, y = self.selected
                image[max(0, y - 95) : y + 95, max(0, x - 95) : x + 95] = (180, 20, 220)
            return MouseCapturedFrame(image=image, rect=self.rect, hwnd=888)

        def close(self):
            self.closed = True

    class _Input:
        def __init__(self, frames):
            self.frames = frames
            self.clicks = []
            self.scrolls = []
            self.drags = []
            self.releases = 0

        def click(self, position, *, content_height):
            local = (position[0] - self.frames.rect.left, position[1] - self.frames.rect.top)
            self.frames.selected = local
            self.clicks.append((position, content_height))
            return position

        def scroll(self, position, amount):
            self.scrolls.append((position, amount))

        def drag(self, start, end, *, hold_seconds, duration_seconds):
            self.drags.append((start, end, hold_seconds, duration_seconds))

        def between_items(self):
            return None

        def release_left(self):
            self.releases += 1

    class _BottomAlignedInput(_Input):
        def __init__(self, frames, *, tail_count):
            super().__init__(frames)
            self.tail_count = tail_count

        def scroll(self, position, amount):
            super().scroll(position, amount)
            self.frames.occupied_start_row = 2
            self.frames.occupied_count = self.tail_count

    def test_full_mouse_capture_uses_existing_stream_callback_contract(self) -> None:
        layout = MouseInventoryLayout()
        frames = self._Frames(layout)
        input_driver = self._Input(frames)
        callbacks = []
        with tempfile.TemporaryDirectory() as tmp:
            scanner = MouseInventoryScanner(
                tmp,
                layout=layout,
                frame_provider=frames,
                input_driver=input_driver,
                sleep_fn=lambda _seconds: None,
            )

            captured = scanner.start_scan(
                7,
                on_capture=lambda path, index, total: callbacks.append((Path(path), index, total)),
                commit_on_complete=False,
            )
            scanner.close()

            self.assertEqual(7, captured)
            self.assertEqual([1, 2, 3, 4, 5, 6, 7], [row[1] for row in callbacks])
            self.assertTrue(all(row[0].is_file() for row in callbacks))
            self.assertEqual(7, len(input_driver.clicks))
            self.assertEqual([], input_driver.scrolls)
            self.assertEqual(
                [((1320, 360), (1320, 1360), 0.3, 0.6)],
                input_driver.drags,
            )
            self.assertGreaterEqual(input_driver.releases, 2)
            self.assertTrue(frames.closed)
            report = json.loads((Path(tmp) / "mouse_scan_last_report.json").read_text(encoding="utf-8"))
            self.assertEqual("complete", report["status"])
            self.assertEqual({"width": 2560, "height": 1440}, report["resolution"])
            self.assertEqual({"expected": 7, "captured": 7}, report["inventory"])
            self.assertEqual(1, len(report["pages"]))
            self.assertEqual([], report["pages"][0]["wheel_amounts"])
            self.assertEqual([1, 7], report["pages"][0]["item_range"])

    def test_bottom_aligned_twelve_item_tail_scans_in_standard_and_compatibility_profiles(self) -> None:
        layout = MouseInventoryLayout()
        for profile in (
            MouseInventoryScanner.LOW_LOAD_INPUT_SPEED_PROFILE,
            MouseInventoryScanner.COMPATIBILITY_INPUT_SPEED_PROFILE,
        ):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as tmp:
                frames = self._Frames(layout)
                input_driver = self._BottomAlignedInput(frames, tail_count=12)
                scanner = MouseInventoryScanner(
                    tmp,
                    layout=layout,
                    frame_provider=frames,
                    input_driver=input_driver,
                    input_speed_profile=profile,
                    sleep_fn=lambda _seconds: None,
                )

                captured = scanner.start_scan(40, commit_on_complete=False)

                self.assertEqual(40, captured)
                self.assertEqual(40, len(input_driver.clicks))
                self.assertTrue(input_driver.scrolls)

    def test_preflight_failure_writes_privacy_minimal_terminal_report(self) -> None:
        layout = MouseInventoryLayout()
        frames = self._Frames(layout)
        frames.capture = lambda: MouseCapturedFrame(
            image=np.zeros((1440, 2560, 3), dtype=np.uint8),
            rect=frames.rect,
            hwnd=888,
        )
        with tempfile.TemporaryDirectory() as tmp:
            scanner = MouseInventoryScanner(
                tmp,
                layout=layout,
                frame_provider=frames,
                input_driver=self._Input(frames),
                sleep_fn=lambda _seconds: None,
            )

            with self.assertRaisesRegex(RuntimeError, "首帧驱动网格预检失败"):
                scanner.start_scan(5)

            report = json.loads((Path(tmp) / "mouse_scan_last_report.json").read_text(encoding="utf-8"))
            self.assertEqual("preflight_failed", report["status"])
            self.assertEqual({"expected": 5, "captured": 0}, report["inventory"])
            self.assertNotIn("hwnd", report)
            self.assertNotIn("path", json.dumps(report).casefold())

    def test_final_page_requires_exact_match_with_entered_inventory_count(self) -> None:
        layout = MouseInventoryLayout()
        frames = self._Frames(layout)
        with tempfile.TemporaryDirectory() as tmp:
            scanner = MouseInventoryScanner(
                tmp,
                layout=layout,
                frame_provider=frames,
                input_driver=self._Input(frames),
                sleep_fn=lambda _seconds: None,
            )

            with self.assertRaisesRegex(RuntimeError, "末页定位未对齐.*检测到 7.*计划应为 5"):
                scanner.start_scan(5, commit_on_complete=False)

            report = json.loads((Path(tmp) / "mouse_scan_last_report.json").read_text(encoding="utf-8"))
            self.assertEqual("error", report["status"])
            self.assertEqual(0, report["inventory"]["captured"])

    def test_capture_error_reports_partial_count_without_committing_images(self) -> None:
        layout = MouseInventoryLayout()
        frames = self._Frames(layout)
        with tempfile.TemporaryDirectory() as tmp:
            scanner = MouseInventoryScanner(
                tmp,
                layout=layout,
                frame_provider=frames,
                input_driver=self._Input(frames),
                sleep_fn=lambda _seconds: None,
            )

            with self.assertRaisesRegex(RuntimeError, "parser stopped"):
                scanner.start_scan(
                    7,
                    on_capture=lambda _path, index, _total: (
                        (_ for _ in ()).throw(RuntimeError("parser stopped")) if index == 2 else None
                    ),
                    commit_on_complete=False,
                )

            report = json.loads((Path(tmp) / "mouse_scan_last_report.json").read_text(encoding="utf-8"))
            self.assertEqual("error", report["status"])
            self.assertEqual(2, report["inventory"]["captured"])
            self.assertEqual("RuntimeError", report["failure_type"])
            self.assertEqual([1, 2], report["pages"][0]["item_range"])
            self.assertFalse(any(Path(tmp).glob("raw_drive_*.png")))

    def test_stop_between_items_returns_zero_and_reports_partial_page(self) -> None:
        layout = MouseInventoryLayout()
        frames = self._Frames(layout)

        class StopInput(self._Input):
            scanner = None

            def between_items(inner_self):
                if len(inner_self.clicks) == 2:
                    inner_self.scanner.emergency_stop()

        input_driver = StopInput(frames)
        with tempfile.TemporaryDirectory() as tmp:
            scanner = MouseInventoryScanner(
                tmp,
                layout=layout,
                frame_provider=frames,
                input_driver=input_driver,
                sleep_fn=lambda _seconds: None,
            )
            input_driver.scanner = scanner

            self.assertEqual(0, scanner.start_scan(7, commit_on_complete=False))

            report = json.loads((Path(tmp) / "mouse_scan_last_report.json").read_text(encoding="utf-8"))
            self.assertEqual("stopped", report["status"])
            self.assertEqual(2, report["inventory"]["captured"])
            self.assertEqual([1, 2], report["pages"][0]["item_range"])
            self.assertGreaterEqual(input_driver.releases, 2)

    def test_two_page_report_preserves_raw_wheel_sequence_and_bottom_tail(self) -> None:
        layout = MouseInventoryLayout()
        frames = self._Frames(layout)
        rng = np.random.default_rng(20260814)
        selected_pattern = rng.integers(0, 55, size=(190, 190), dtype=np.uint8)
        original_capture = frames.capture

        def textured_capture():
            frame = original_capture()
            if frames.selected is not None:
                x, y = frames.selected
                top, bottom = max(0, y - 95), min(frame.image.shape[0], y + 95)
                left, right = max(0, x - 95), min(frame.image.shape[1], x + 95)
                pattern = selected_pattern[: bottom - top, : right - left]
                frame.image[top:bottom, left:right, 0] = 180 + pattern
                frame.image[top:bottom, left:right, 1] = 20 + pattern // 4
                frame.image[top:bottom, left:right, 2] = 200 + pattern
            return frame

        frames.capture = textured_capture

        class ScrollingInput(self._Input):
            def scroll(inner_self, position, amount):
                super().scroll(position, amount)
                x, y = inner_self.frames.selected
                inner_self.frames.selected = (x, y - (90 if amount == -280 else 75))
                if len(inner_self.scrolls) == 9:
                    inner_self.frames.occupied_start_row = 3
                    inner_self.frames.occupied_count = 1

        input_driver = ScrollingInput(frames)
        with tempfile.TemporaryDirectory() as tmp:
            scanner = MouseInventoryScanner(
                tmp,
                layout=layout,
                frame_provider=frames,
                input_driver=input_driver,
                sleep_fn=lambda _seconds: None,
            )

            self.assertEqual(29, scanner.start_scan(29, commit_on_complete=False))

            report = json.loads((Path(tmp) / "mouse_scan_last_report.json").read_text(encoding="utf-8"))
            self.assertEqual(2, len(report["pages"]))
            self.assertEqual([-280] * 7 + [-120] * 2, report["pages"][0]["wheel_amounts"])
            self.assertEqual(0, report["pages"][0]["overlap_row"])
            self.assertEqual([29, 29], report["pages"][1]["item_range"])

    def test_bottom_page_stops_at_contiguous_occupied_slots(self) -> None:
        layout = MouseInventoryLayout()
        frames = self._Frames(layout)

        def bottom_capture():
            frame = self._Frames.capture(frames)
            # Bottom row has four real items; its last three slots are empty.
            for column in range(4, 7):
                x, y = layout.cell_center(3, column, 2560, 1440)
                frame.image[y - 120 : y + 120, x - 100 : x + 100] = 0
            return frame

        frames.capture = bottom_capture
        input_driver = self._Input(frames)
        with tempfile.TemporaryDirectory() as tmp:
            scanner = MouseInventoryScanner(
                tmp,
                layout=layout,
                frame_provider=frames,
                input_driver=input_driver,
                sleep_fn=lambda _seconds: None,
            )

            self.assertEqual(25, scanner.start_scan(25, commit_on_complete=False))

            self.assertEqual(25, len(input_driver.clicks))
            last_position = input_driver.clicks[-1][0]
            expected_local = layout.cell_center(3, 3, 2560, 1440)
            self.assertEqual(
                (frames.rect.left + expected_local[0], frames.rect.top + expected_local[1] - 20),
                last_position,
            )
            report = json.loads((Path(tmp) / "mouse_scan_last_report.json").read_text(encoding="utf-8"))
            self.assertEqual(25, report["pages"][0]["occupied_slots"])

    def test_scroll_loop_uses_reference_coarse_then_fine_wheel_sequence(self) -> None:
        layout = MouseInventoryLayout()
        rect = WindowRect(40, 60, 2600, 1500)

        class ScrollFrames:
            def __init__(self):
                self.center_y = 1101
                rng = np.random.default_rng(20260814)
                self.pattern = rng.integers(25, 240, size=(96, 96), dtype=np.uint8)

            def capture(self):
                image = np.zeros((1440, 2560, 3), dtype=np.uint8)
                x, y = 1576, self.center_y
                image[y - 48 : y + 48, x - 48 : x + 48] = self.pattern[:, :, None]
                import cv2

                cv2.rectangle(image, (x - 95, y - 95), (x + 95, y + 95), (220, 20, 230), 8)
                return MouseCapturedFrame(
                    image=image,
                    rect=rect,
                    hwnd=888,
                )

            def close(self):
                return None

        class ScrollInput:
            def __init__(self, frames):
                self.frames = frames
                self.amounts = []

            def click(self, position, *, content_height):
                return position

            def scroll(self, _position, amount):
                self.amounts.append(amount)
                self.frames.center_y -= 90 if amount == -280 else 75

            def release_left(self):
                return None

        frames = ScrollFrames()
        input_driver = ScrollInput(frames)
        with tempfile.TemporaryDirectory() as tmp:
            scanner = MouseInventoryScanner(
                tmp,
                layout=layout,
                frame_provider=frames,
                input_driver=input_driver,
                sleep_fn=lambda _seconds: None,
            )
            initial = scanner._capture_frame(freeze=True)
            final_frame, overlap_row, row_offset, reached_bottom = scanner._scroll_to_next_page(
                initial,
                MouseScanSlot(index=28, page=0, row=3, column=6),
            )

        self.assertEqual([-280] * 7 + [-120] * 2, input_driver.amounts)
        self.assertEqual(0, overlap_row)
        self.assertEqual(0, row_offset)
        self.assertFalse(reached_bottom)
        self.assertEqual(321, frames.center_y)
        self.assertEqual(rect, final_frame.rect)

    def test_sixth_scroll_uses_the_small_cumulative_offset_compensation(self) -> None:
        layout = MouseInventoryLayout()
        rect = WindowRect(40, 60, 2600, 1500)

        class Frames:
            center_y = 1101

            def __init__(self):
                self.pattern = np.random.default_rng(42).integers(25, 240, size=(50, 50), dtype=np.uint8)

            def capture(self):
                image = np.zeros((1440, 2560, 3), dtype=np.uint8)
                image[self.center_y - 25 : self.center_y + 25, 1551:1601] = self.pattern[:, :, None]
                return MouseCapturedFrame(image=image, rect=rect, hwnd=888)

            def close(self):
                return None

        class Input:
            def __init__(self, frames):
                self.frames = frames
                self.amounts = []

            def click(self, position, *, content_height):
                return position

            def scroll(self, _position, amount):
                self.amounts.append(amount)
                self.frames.center_y -= 95 if amount == -280 else 70

            def release_left(self):
                return None

        frames = Frames()
        input_driver = Input(frames)
        with tempfile.TemporaryDirectory() as tmp:
            scanner = MouseInventoryScanner(
                tmp,
                layout=layout,
                frame_provider=frames,
                input_driver=input_driver,
                sleep_fn=lambda _seconds: None,
            )
            initial = scanner._capture_frame(freeze=True)
            scanner._scroll_to_next_page(
                initial,
                MouseScanSlot(index=133, page=5, row=3, column=6),
            )

        self.assertEqual([-280] * 6 + [-120] * 4, input_driver.amounts)

    def test_2000_item_scroll_plan_applies_small_offset_compensation_every_six_flips(self) -> None:
        profiles = [MouseInventoryScanner._scroll_amounts_for_flip(flip) for flip in range(1, 95)]

        self.assertEqual(94, len(profiles))
        for flip, profile in enumerate(profiles, start=1):
            expected = (
                MouseInventoryScanner.SCROLL_PROFILE_B
                if flip % MouseInventoryScanner.SCROLL_COMPENSATION_PERIOD == 0
                else MouseInventoryScanner.SCROLL_PROFILE_A
            )
            self.assertEqual(expected, profile)

    def test_scroll_does_not_guess_bottom_from_untrusted_visual_match(self) -> None:
        layout = MouseInventoryLayout()
        rect = WindowRect(40, 60, 2600, 1500)

        class BottomFrames:
            center_y = 1101

            def __init__(self):
                self.pattern = np.random.default_rng(42).integers(25, 240, size=(50, 50), dtype=np.uint8)

            def capture(self):
                image = np.zeros((1440, 2560, 3), dtype=np.uint8)
                image[self.center_y - 25 : self.center_y + 25, 1551:1601] = self.pattern[:, :, None]
                return MouseCapturedFrame(image=image, rect=rect, hwnd=888)

            def close(self):
                return None

        class BottomInput:
            def __init__(self):
                self.amounts = []

            def click(self, position, *, content_height):
                return position

            def scroll(self, _position, amount):
                self.amounts.append(amount)

            def release_left(self):
                return None

        frames = BottomFrames()
        input_driver = BottomInput()
        with tempfile.TemporaryDirectory() as tmp:
            scanner = MouseInventoryScanner(
                tmp,
                layout=layout,
                frame_provider=frames,
                input_driver=input_driver,
                sleep_fn=lambda _seconds: None,
            )
            initial = scanner._capture_frame(freeze=True)
            _frame, overlap_row, row_offset, reached_bottom = scanner._scroll_to_next_page(
                initial,
                MouseScanSlot(index=252, page=16, row=3, column=6),
            )

        self.assertEqual([-280] * 7 + [-120] * 2, input_driver.amounts)
        self.assertEqual(0, overlap_row)
        self.assertEqual(0, row_offset)
        self.assertFalse(reached_bottom)


if __name__ == "__main__":
    unittest.main()


def test_mouse_compatibility_profile_is_slower_than_formal_low_load() -> None:
    from src.integrations.vision.mouse_inventory_scan import MouseInventoryScanner

    scanner = MouseInventoryScanner(
        input_speed_profile=MouseInventoryScanner.COMPATIBILITY_INPUT_SPEED_PROFILE,
    )
    try:
        assert scanner.input_speed_profile == "compatibility-low-load-v1"
    finally:
        scanner.close()
