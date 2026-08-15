# 提供鼠标全量视觉扫描的布局、随机输入和滚轮反馈基础能力。
"""Mouse-driven full visual inventory capture primitives.

The pure layout and feedback types in this module are intentionally independent
from Qt, OCR, SQLite and the concrete Windows mouse backend.  The capture driver
is added on top of these contracts so tests can fix the supplied game-frame
geometry without sending real input.
"""

from __future__ import annotations

import math
import os
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from src.integrations.vision.mouse_scan_input import (
    MouseInputRandomization as MouseInputRandomization,
    MouseScanInput,
    PyAutoGuiMouseScanInput,
)
from src.integrations.vision.mouse_scan_grid import (
    MouseGridOccupancy,
    detect_grid_occupancy,
)
from src.integrations.vision.mouse_scan_capture import (
    ForegroundWindowFrameProvider,
    MouseCapturedFrame,
    MouseFrameProvider,
)
from src.integrations.vision.mouse_scan_telemetry import MouseScanPageMetric, MouseScanTelemetry
from src.scanner.window_capture import WindowRect, game_content_rect
from src.utils.logger import logger
from src.utils.perf import log_perf


def _round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


@dataclass(frozen=True)
class MouseScanSlot:
    """One inventory index and its visible-grid position."""

    index: int
    page: int
    row: int
    column: int


@dataclass(frozen=True)
class MouseLayoutPreflightReport:
    """Evidence that the frozen first frame contains the expected item grid."""

    width: int
    height: int
    checked_slots: int
    matched_slots: int
    minimum_warm_fraction: float
    valid: bool


@dataclass(frozen=True)
class MouseInventoryLayout:
    """Evidence-derived inventory grid for the supplied 2560x1440 frame."""

    base_width: int = 2560
    base_height: int = 1440
    columns: int = 7
    visible_rows: int = 4
    first_center_x: int = 254
    first_center_y: int = 322
    spacing_x: float = 220.33333333333334
    spacing_y: float = 259.6666666666667
    preflight_half_width: int = 80
    preflight_half_height: int = 70
    preflight_warm_fraction: float = 0.12

    def calibrate_initial_frame(
        self,
        image: np.ndarray,
        total_items: int,
    ) -> tuple[MouseInventoryLayout, MouseLayoutPreflightReport]:
        """Freeze either the proportional layout or a visually proven fixed-pixel grid."""

        report = self.verify_initial_frame(image, total_items)
        if report.valid:
            return self, report
        height, width = image.shape[:2]
        last_x = self.first_center_x + (self.columns - 1) * self.spacing_x
        last_y = self.first_center_y + (self.visible_rows - 1) * self.spacing_y
        if width <= self.base_width and height <= self.base_height and last_x < width and last_y < height:
            native = replace(self, base_width=int(width), base_height=int(height))
            native_report = native.verify_initial_frame(image, total_items)
            if native_report.valid:
                return native, native_report
        return self, report

    def cell_center(
        self,
        row: int,
        column: int,
        target_width: int,
        target_height: int,
    ) -> tuple[int, int]:
        """Map a visible cell to client-image physical pixels.

        Tall clients such as 2560x1600 use ``game_content_rect``'s existing
        top-aligned 16:9 canvas instead of vertically centering the UI.
        """

        if not 0 <= int(row) < self.visible_rows:
            raise ValueError(f"row must be in 0-{self.visible_rows - 1}")
        if not 0 <= int(column) < self.columns:
            raise ValueError(f"column must be in 0-{self.columns - 1}")
        left, top, content_width, content_height = game_content_rect(
            int(target_width),
            int(target_height),
            (self.base_width, self.base_height),
        )
        x = self.first_center_x + int(column) * self.spacing_x
        y = self.first_center_y + int(row) * self.spacing_y
        return (
            left + _round_half_up(x * content_width / self.base_width),
            top + _round_half_up(y * content_height / self.base_height),
        )

    def page_slots(self, total_items: int) -> tuple[tuple[MouseScanSlot, ...], ...]:
        """Build ordered pages: four rows initially, then three new rows.

        Scrolling moves the previously scanned fourth row into the first
        visible row.  Later pages therefore skip row zero and capture rows one
        through three.
        """

        total = int(total_items)
        if total <= 0:
            raise ValueError("total_items must be positive")
        pages: list[tuple[MouseScanSlot, ...]] = []
        next_index = 1
        page = 0
        while next_index <= total:
            start_row = 0 if page == 0 else 1
            page_slots: list[MouseScanSlot] = []
            for row in range(start_row, self.visible_rows):
                for column in range(self.columns):
                    if next_index > total:
                        break
                    page_slots.append(
                        MouseScanSlot(
                            index=next_index,
                            page=page,
                            row=row,
                            column=column,
                        )
                    )
                    next_index += 1
                if next_index > total:
                    break
            pages.append(tuple(page_slots))
            page += 1
        return tuple(pages)

    def nearest_visible_row(self, center_y: float, target_width: int, target_height: int) -> int:
        """Return the closest visible grid row for a tracked item center."""

        centers = [
            self.cell_center(row, 0, target_width, target_height)[1]
            for row in range(self.visible_rows)
        ]
        return min(range(self.visible_rows), key=lambda row: abs(centers[row] - float(center_y)))

    def verify_initial_frame(
        self,
        image: np.ndarray,
        total_items: int,
    ) -> MouseLayoutPreflightReport:
        """Confirm the supplied first-frame card centers before sending input.

        The warm high-saturation drive glyph is stable in the supplied 2K
        evidence and its 1080p/4K projections.  Requiring it at every occupied
        first-page center rejects another tab, a shifted layout, or an unloaded
        inventory before the first mouse click.
        """

        if image is None or image.ndim != 3 or image.shape[2] < 3:
            raise ValueError("initial frame must be a BGR color image")
        height, width = image.shape[:2]
        slots = self.page_slots(int(total_items))[0]
        fractions: list[float] = []
        matched = 0
        content_height = game_content_rect(width, height, (self.base_width, self.base_height))[3]
        scale = content_height / self.base_height
        half_width = max(24, _round_half_up(self.preflight_half_width * scale))
        half_height = max(24, _round_half_up(self.preflight_half_height * scale))
        for slot in slots:
            center_x, center_y = self.cell_center(slot.row, slot.column, width, height)
            roi = image[
                max(0, center_y - half_height) : min(height, center_y + half_height),
                max(0, center_x - half_width) : min(width, center_x + half_width),
            ]
            fraction = self._warm_glyph_fraction(roi)
            fractions.append(fraction)
            if fraction >= self.preflight_warm_fraction:
                matched += 1
        checked = len(slots)
        return MouseLayoutPreflightReport(
            width=int(width),
            height=int(height),
            checked_slots=checked,
            matched_slots=matched,
            minimum_warm_fraction=min(fractions, default=0.0),
            valid=checked > 0 and matched == checked,
        )

    @staticmethod
    def _warm_glyph_fraction(roi: np.ndarray) -> float:
        if roi.size == 0:
            return 0.0
        hsv = cv2.cvtColor(roi[:, :, :3], cv2.COLOR_BGR2HSV)
        warm = (
            (hsv[:, :, 0] >= 5)
            & (hsv[:, :, 0] <= 40)
            & (hsv[:, :, 1] >= 90)
            & (hsv[:, :, 2] >= 120)
        )
        return float(warm.mean())


class MouseInventoryScanError(RuntimeError):
    """Raised when continuing the frozen mouse scan would risk wrong capture."""


class MouseInventoryScanner:
    """Full visual inventory capture using the supplied 7x4 mouse layout."""

    MAX_INVENTORY_COUNT = 2000
    TAKEOVER_COUNTDOWN_SECONDS = 3.0
    PANEL_STABLE_DIFF = 2.4
    PANEL_STABLE_FRAMES = 1
    PANEL_POLL_SECONDS = 1.0 / 30.0
    PANEL_MAX_POLLS = 18
    SELECTED_PINK_FRACTION = 0.01
    SCROLL_PROFILE_A = (-280,) * 7 + (-120,) * 2
    SCROLL_PROFILE_B = (-280,) * 6 + (-120,) * 4
    SCROLL_COMPENSATION_PERIOD = 6
    CLICK_SAFE_OFFSET_Y_2K = -20
    INPUT_SPEED_PROFILE = "one-point-five-trial-v1"
    FAST_INPUT_SPEED_PROFILE = "fast-trial-v1"
    LOW_LOAD_INPUT_SPEED_PROFILE = "standard-low-load-v1"
    COMPATIBILITY_INPUT_SPEED_PROFILE = "compatibility-low-load-v1"

    def __init__(
        self,
        output_dir: str | os.PathLike[str] = "scanned_images",
        *,
        layout: MouseInventoryLayout | None = None,
        frame_provider: MouseFrameProvider | None = None,
        input_driver: MouseScanInput | None = None,
        input_speed_profile: str = INPUT_SPEED_PROFILE,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.output_dir = str(output_dir)
        self.capture_dir = self.output_dir
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        self.layout = layout or MouseInventoryLayout()
        self._sleep = sleep_fn
        self._frame_provider = frame_provider or ForegroundWindowFrameProvider()
        self.input_speed_profile = str(input_speed_profile)
        if self.input_speed_profile == self.INPUT_SPEED_PROFILE:
            randomization = MouseInputRandomization.one_point_five_trial()
            disable_pyautogui_pause = True
        elif self.input_speed_profile == self.FAST_INPUT_SPEED_PROFILE:
            randomization = MouseInputRandomization.fast_trial()
            disable_pyautogui_pause = True
        elif self.input_speed_profile == self.LOW_LOAD_INPUT_SPEED_PROFILE:
            randomization = MouseInputRandomization()
            disable_pyautogui_pause = False
        elif self.input_speed_profile == self.COMPATIBILITY_INPUT_SPEED_PROFILE:
            randomization = MouseInputRandomization.compatibility_low_load()
            disable_pyautogui_pause = False
        else:
            raise ValueError(f"不支持的鼠标扫描输入 profile：{input_speed_profile}")
        self._input = input_driver or PyAutoGuiMouseScanInput(
            randomization=randomization,
            sleep_fn=sleep_fn,
            disable_pyautogui_pause=disable_pyautogui_pause,
        )
        self._stopped = False
        self._closed = False
        self._target_hwnd: int | None = None
        self._target_rect: WindowRect | None = None
        self.scroll_command_count = 0
        self.scroll_amounts: list[int] = []
        self._row_offset_px = 0

    def emergency_stop(self) -> None:
        self._stopped = True
        try:
            self._input.release_left()
        finally:
            logger.warning("鼠标全量视觉扫描已收到停止指令")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._input.release_left()
        finally:
            self._frame_provider.close()

    def _clear_output_images(self) -> None:
        image_exts = {".png", ".jpg", ".jpeg", ".bmp"}
        for path in Path(self.output_dir).iterdir():
            if path.is_file() and path.suffix.lower() in image_exts:
                path.unlink()

    def _prepare_temp_output(self) -> None:
        capture_dir = Path(self.output_dir) / "temp"
        if capture_dir.exists():
            shutil.rmtree(capture_dir)
        capture_dir.mkdir(parents=True, exist_ok=True)
        self.capture_dir = str(capture_dir)

    def _commit_temp_output(self) -> None:
        capture_dir = Path(self.capture_dir)
        if capture_dir.resolve() == Path(self.output_dir).resolve():
            return
        self._clear_output_images()
        for source in sorted(capture_dir.iterdir()):
            if source.is_file():
                shutil.move(str(source), str(Path(self.output_dir) / source.name))
        shutil.rmtree(capture_dir)
        self.capture_dir = self.output_dir

    def start_scan(
        self,
        total_drives: int | None = None,
        on_capture: Callable[[str, int, int], None] | None = None,
        commit_on_complete: bool = True,
    ) -> int:
        started = time.perf_counter()
        if total_drives is None:
            raise ValueError("鼠标全量扫描需要先填写库存数量。")
        total = int(total_drives)
        if not 0 < total <= self.MAX_INVENTORY_COUNT:
            raise ValueError(f"库存数量必须在 1-{self.MAX_INVENTORY_COUNT} 之间。")
        self.scroll_command_count = 0
        self.scroll_amounts.clear()
        self._row_offset_px = 0

        self._sleep(self.TAKEOVER_COUNTDOWN_SECONDS)
        initial = self._capture_frame(freeze=True)
        self.layout, preflight = self.layout.calibrate_initial_frame(initial.image, total)
        telemetry = MouseScanTelemetry(
            expected=total,
            width=preflight.width,
            height=preflight.height,
            preflight_checked=preflight.checked_slots,
            preflight_matched=preflight.matched_slots,
            preflight_minimum_warm_fraction=preflight.minimum_warm_fraction,
        )
        if not preflight.valid:
            self._write_scan_report(
                telemetry,
                status="preflight_failed",
                captured=0,
                started=started,
            )
            raise MouseInventoryScanError(
                "首帧驱动网格预检失败："
                f"仅匹配 {preflight.matched_slots}/{preflight.checked_slots} 个应有格位；"
                "请确认已打开驱动列表、滚动到顶部且界面未被遮挡"
            )
        logger.info(
            "鼠标全量扫描首帧网格预检通过: resolution={}x{} slots={} min_warm={:.3f}",
            preflight.width,
            preflight.height,
            preflight.checked_slots,
            preflight.minimum_warm_fraction,
        )
        self._prepare_temp_output()
        captured = 0
        last_frame = initial
        last_slot: MouseScanSlot | None = None
        page_index = 0
        planned_pages = self.layout.page_slots(total)

        try:
            while captured < total:
                page_started = time.perf_counter()
                page_first_index = captured + 1
                page_scroll_start = len(self.scroll_amounts)
                current_page = page_index
                capture_elapsed_ms = 0.0
                scroll_elapsed_ms = 0.0
                overlap_row: int | None = None
                reached_bottom = False
                occupied_slots: int | None = None
                slots = list(planned_pages[page_index])
                start_row = slots[0].row
                end_row = slots[-1].row + 1
                planned_rows = end_row - start_row
                is_final_page = page_index == len(planned_pages) - 1
                try:
                    occupancy = self._page_occupancy(
                        last_frame,
                        start_row,
                        end_row,
                        self._row_offset_px,
                    )
                    occupied_slots = occupancy.contiguous_count
                    if occupancy.has_gap:
                        raise MouseInventoryScanError("当前页驱动格位不连续，停止扫描以避免漏件")
                    expected_visible = len(slots)
                    if is_final_page and occupied_slots != expected_visible:
                        raise MouseInventoryScanError(
                            "末页定位未对齐，已在点击前停止："
                            f"检测到 {occupied_slots}，计划应为 {expected_visible}"
                        )
                    if occupied_slots < expected_visible:
                        raise MouseInventoryScanError(
                            "当前页实际驱动格位与剩余数量不一致："
                            f"检测到 {occupied_slots}，至少应为 {expected_visible}"
                        )
                    reached_bottom = is_final_page and occupied_slots == expected_visible
                    for slot in slots:
                        if self._stopped:
                            break
                        last_frame = self._capture_slot(slot)
                        last_slot = slot
                        path = self._save_frame(last_frame, slot.index)
                        captured += 1
                        if on_capture is not None:
                            on_capture(path, slot.index, total)
                        between_items = getattr(self._input, "between_items", None)
                        if callable(between_items):
                            between_items()
                    capture_elapsed_ms = (time.perf_counter() - page_started) * 1000.0
                    if self._stopped:
                        break
                    if captured < total:
                        assert last_slot is not None
                        scroll_started = time.perf_counter()
                        last_frame, overlap_row, row_offset, reached_bottom = self._scroll_to_next_page(
                            last_frame,
                            last_slot,
                            row_offset_px=self._row_offset_px,
                        )
                        self._row_offset_px = row_offset
                        scroll_elapsed_ms = (time.perf_counter() - scroll_started) * 1000.0
                        page_index += 1
                finally:
                    if captured >= page_first_index:
                        if not capture_elapsed_ms:
                            capture_elapsed_ms = (time.perf_counter() - page_started) * 1000.0
                        telemetry.append_page(
                            MouseScanPageMetric(
                                page=current_page,
                                item_range=(page_first_index, captured),
                                captured=captured - page_first_index + 1,
                                capture_elapsed_ms=capture_elapsed_ms,
                                wheel_amounts=tuple(self.scroll_amounts[page_scroll_start:]),
                                scroll_elapsed_ms=scroll_elapsed_ms,
                                overlap_row=overlap_row,
                                row_offset_px=self._row_offset_px,
                                reached_bottom=reached_bottom,
                                occupied_slots=occupied_slots,
                                planned_rows=planned_rows,
                                planned_slots=len(slots),
                            )
                        )
        except BaseException as exc:
            self._write_scan_report(
                telemetry,
                status="stopped" if self._stopped else "error",
                captured=captured,
                started=started,
                failure_type=type(exc).__name__,
            )
            if self._stopped:
                return 0
            raise
        finally:
            self._input.release_left()

        if self._stopped or captured != total:
            logger.warning(f"鼠标全量扫描不完整: captured={captured} expected={total}")
            self._write_scan_report(
                telemetry,
                status="stopped" if self._stopped else "incomplete",
                captured=captured,
                started=started,
            )
            return 0
        telemetry.write_complete(
            self.output_dir,
            captured=captured,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )
        if commit_on_complete:
            self._commit_temp_output()
        log_perf(
            logger,
            "scan.mouse_full",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            total=total,
            captured=captured,
            pages=page_index + 1,
            scroll_commands=self.scroll_command_count,
        )
        return captured

    def _write_scan_report(
        self,
        telemetry: MouseScanTelemetry,
        *,
        status: str,
        captured: int,
        started: float,
        failure_type: str | None = None,
    ) -> None:
        try:
            telemetry.write(
                self.output_dir,
                status=status,
                captured=captured,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                failure_type=failure_type,
            )
        except OSError as exc:
            logger.warning(f"鼠标扫描诊断报告写入失败: {type(exc).__name__}")

    def _capture_slot(self, slot: MouseScanSlot) -> MouseCapturedFrame:
        target_frame = self._capture_frame()
        local = self.layout.cell_center(
            slot.row,
            slot.column,
            target_frame.rect.width,
            target_frame.rect.height,
        )
        safe_y_offset = _round_half_up(
            self.CLICK_SAFE_OFFSET_Y_2K * self._content_height(target_frame) / self.layout.base_height
        )
        local = (local[0], local[1] + self._row_offset_px + safe_y_offset)
        screen_position = (
            target_frame.rect.left + local[0],
            target_frame.rect.top + local[1],
        )
        for attempt in range(2):
            if self._stopped:
                raise MouseInventoryScanError("扫描已停止")
            self._input.click(screen_position, content_height=self._content_height(target_frame))
            stable = self._wait_for_selected_panel(slot, self._row_offset_px)
            if stable is not None:
                return stable
            logger.warning(f"第 {slot.index} 件详情未稳定，重试点击 ({attempt + 1}/2)")
        raise MouseInventoryScanError(f"第 {slot.index} 件点击后未检测到稳定详情面板")

    def _wait_for_selected_panel(self, slot: MouseScanSlot, row_offset_px: int) -> MouseCapturedFrame | None:
        previous: np.ndarray | None = None
        stable_frames = 0
        for _ in range(self.PANEL_MAX_POLLS):
            if self._stopped:
                return None
            frame = self._capture_frame()
            if not self._cell_is_selected(frame.image, slot.row, slot.column, row_offset_px=row_offset_px):
                previous = None
                stable_frames = 0
                self._sleep(self.PANEL_POLL_SECONDS)
                continue
            signature = self._panel_signature(frame.image)
            if previous is None:
                stable_frames = 1
            else:
                difference = float(cv2.absdiff(previous, signature).mean())
                stable_frames = stable_frames + 1 if difference <= self.PANEL_STABLE_DIFF else 1
            previous = signature
            if stable_frames >= self.PANEL_STABLE_FRAMES:
                return frame
            self._sleep(self.PANEL_POLL_SECONDS)
        return None

    def _scroll_to_next_page(
        self,
        frame: MouseCapturedFrame,
        slot: MouseScanSlot,
        *,
        row_offset_px: int = 0,
    ) -> tuple[MouseCapturedFrame, int, int, bool]:
        local = self.layout.cell_center(slot.row, slot.column, frame.rect.width, frame.rect.height)
        local = (local[0], local[1] + int(row_offset_px))
        scroll_position = (
            frame.rect.left + local[0],
            frame.rect.top + local[1],
        )
        flip_number = int(slot.page) + 1
        amounts = self._scroll_amounts_for_flip(flip_number)
        for amount in amounts:
            if self._stopped:
                raise MouseInventoryScanError("扫描已停止")
            self._input.scroll(scroll_position, amount)
            self.scroll_command_count += 1
            self.scroll_amounts.append(int(amount))
        current_frame = self._capture_frame()
        return current_frame, 0, 0, False

    @classmethod
    def _scroll_amounts_for_flip(cls, flip_number: int) -> tuple[int, ...]:
        if int(flip_number) <= 0:
            raise ValueError("flip_number must be positive")
        if int(flip_number) % cls.SCROLL_COMPENSATION_PERIOD == 0:
            return cls.SCROLL_PROFILE_B
        return cls.SCROLL_PROFILE_A

    def _page_occupancy(
        self,
        frame: MouseCapturedFrame,
        start_row: int,
        end_row: int,
        row_offset_px: int,
    ) -> MouseGridOccupancy:
        centers = tuple(
            (
                center_x,
                center_y + int(row_offset_px),
            )
            for row in range(int(start_row), int(end_row))
            for column in range(self.layout.columns)
            for center_x, center_y in (
                self.layout.cell_center(row, column, frame.rect.width, frame.rect.height),
            )
        )
        return detect_grid_occupancy(
            frame.image,
            centers,
            scale=self._content_height(frame) / self.layout.base_height,
        )

    def _capture_frame(self, *, freeze: bool = False) -> MouseCapturedFrame:
        frame = self._frame_provider.capture()
        if frame.image.shape[0] != frame.rect.height or frame.image.shape[1] != frame.rect.width:
            raise MouseInventoryScanError("截图尺寸与冻结客户区不一致")
        if freeze or self._target_rect is None:
            self._target_hwnd = int(frame.hwnd)
            self._target_rect = frame.rect
            return frame
        if int(frame.hwnd) != int(self._target_hwnd or 0):
            raise MouseInventoryScanError("扫描期间前台窗口发生变化")
        if frame.rect != self._target_rect:
            raise MouseInventoryScanError("扫描期间游戏客户区尺寸或位置发生变化")
        return frame

    def _content_height(self, frame: MouseCapturedFrame) -> int:
        return game_content_rect(frame.rect.width, frame.rect.height)[3]

    def sync_equipment_states(
        self,
        total_drives: int,
        state_changes: list[dict],
        action_mode: str = "default",
    ) -> int:
        del action_mode
        from src.integrations.vision.mouse_state_sync import MouseEquipmentStateSync
        from src.integrations.vision.equipment_state_detection import right_panel_button_state_from_image

        sync = MouseEquipmentStateSync(
            self,
            state_detector=right_panel_button_state_from_image,
            sleep_fn=self._sleep,
        )
        return sync.sync(total_drives, state_changes)

    def _cell_is_selected(
        self,
        image: np.ndarray,
        row: int,
        column: int,
        *,
        row_offset_px: int = 0,
    ) -> bool:
        height, width = image.shape[:2]
        center_x, center_y = self.layout.cell_center(row, column, width, height)
        center_y += int(row_offset_px)
        content_height = game_content_rect(width, height)[3]
        half = max(48, _round_half_up(105 * content_height / self.layout.base_height))
        roi = image[
            max(0, center_y - half) : min(height, center_y + half),
            max(0, center_x - half) : min(width, center_x + half),
        ]
        if roi.size == 0:
            return False
        b = roi[:, :, 0].astype(np.int16)
        g = roi[:, :, 1].astype(np.int16)
        r = roi[:, :, 2].astype(np.int16)
        fraction = float(((r > 150) & (b > 105) & (g < 125) & ((r - g) > 55) & ((b - g) > 25)).mean())
        return fraction >= self.SELECTED_PINK_FRACTION

    @staticmethod
    def _panel_signature(image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        left, top, content_width, content_height = game_content_rect(width, height)
        roi = image[
            top + _round_half_up(content_height * 0.20) : top + _round_half_up(content_height * 0.82),
            left + _round_half_up(content_width * 0.70) : left + _round_half_up(content_width * 0.95),
        ]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)

    def _save_frame(self, frame: MouseCapturedFrame, index: int) -> str:
        path = Path(self.capture_dir) / f"raw_drive_{int(index):04d}.png"
        ok, encoded = cv2.imencode(".png", frame.image, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if not ok:
            raise MouseInventoryScanError(f"第 {index} 张截图编码失败")
        encoded.tofile(path)
        return str(path)
