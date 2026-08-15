"""Mouse-driven equipment state synchronization after a full visual scan."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import cv2

from src.integrations.vision.equipment_state_detection import (
    CONFIRM_BUTTON_CENTER,
    LOCK_BUTTON_CENTER,
    TRASH_BUTTON_CENTER,
    lock_to_discard_confirmation_visible,
)
from src.scanner.window_capture import game_content_rect
from src.utils.image_io import imread_unicode
from src.utils.logger import logger


EQUIPMENT_STATES = {"normal", "locked", "discarded"}


class MouseStateScanner(Protocol):
    output_dir: str
    layout: Any
    _input: Any
    _stopped: bool

    def _capture_frame(self, *, freeze: bool = False) -> Any: ...
    def _content_height(self, frame: Any) -> int: ...
    def _wait_for_selected_panel(self, slot: Any, row_offset_px: int) -> Any | None: ...
    def _scroll_amounts_for_flip(self, flip_number: int) -> tuple[int, ...]: ...
    def _panel_signature(self, image: Any) -> Any: ...


class MouseEquipmentStateSync:
    """Reverse the proven scan pagination and apply a fixed state-change plan."""

    ACTION_SETTLE_SECONDS = 0.5
    LOCK_TO_DISCARD_POPUP_SECONDS = 1.0
    LOCK_TO_DISCARD_DISMISS_SECONDS = 0.5
    PANEL_IDENTITY_MAX_DIFF = 10.0

    def __init__(
        self,
        scanner: MouseStateScanner,
        *,
        state_detector: Callable[[Any], str],
        popup_detector: Callable[[Any], bool] = lock_to_discard_confirmation_visible,
        identity_verifier: Callable[[int, Any], bool] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.scanner = scanner
        self.state_detector = state_detector
        self.popup_detector = popup_detector
        self.identity_verifier = identity_verifier or self._reference_panel_matches
        self.sleep_fn = sleep_fn

    def sync(self, total_items: int, state_changes: list[dict[str, Any]]) -> int:
        started = time.perf_counter()
        total = int(total_items)
        changes = self._validated_changes(total, state_changes)
        if not changes:
            self._write_report(
                status="complete",
                requested=0,
                applied=0,
                started=started,
                transitions={},
            )
            return 0

        pages = self.scanner.layout.page_slots(total)
        by_index = {slot.index: slot for page in pages for slot in page}
        current_page = len(pages) - 1
        frame = self.scanner._capture_frame()
        applied = 0
        transitions: dict[str, int] = {}
        try:
            initial_last_slot = pages[-1][-1]
            if self.scanner._wait_for_selected_panel(initial_last_slot, 0) is None:
                raise RuntimeError("状态管理启动时游戏未保持在扫描结束的最后一件驱动")
            for change in changes:
                self._check_stopped()
                slot = by_index[int(change["index"])]
                while current_page > slot.page:
                    frame = self._scroll_previous_page(frame, current_page)
                    current_page -= 1
                frame = self._select_slot(frame, slot)
                if self.identity_verifier is not None and not self.identity_verifier(slot.index, frame.image):
                    raise RuntimeError(
                        f"第 {slot.index} 件详情与扫描截图不一致，"
                        "已停止以避免操作错误驱动"
                    )
                detected = self.state_detector(frame.image)
                expected_current = str(change["current_state"])
                if detected != expected_current:
                    raise RuntimeError(
                        f"第 {slot.index} 件状态与固定计划不一致："
                        f"画面为 {detected}，计划为 {expected_current}"
                    )
                target = str(change["target_state"])
                frame = self._apply_transition(frame, detected, target)
                frame, verified = self._wait_for_state(frame, target)
                if verified != target:
                    raise RuntimeError(
                        f"第 {slot.index} 件状态切换后复核失败："
                        f"期望 {target}，画面为 {verified}"
                    )
                applied += 1
                transition = f"{detected}->{target}"
                transitions[transition] = transitions.get(transition, 0) + 1
                logger.info(
                    f"[鼠标状态管理] 已复核 raw_drive_{slot.index:04d} "
                    f"{detected} -> {target}"
                )
        except BaseException as exc:
            self._write_report(
                status="stopped" if self.scanner._stopped else "error",
                requested=len(changes),
                applied=applied,
                started=started,
                transitions=transitions,
                failure_type=type(exc).__name__,
            )
            raise
        self._write_report(
            status="complete",
            requested=len(changes),
            applied=applied,
            started=started,
            transitions=transitions,
        )
        return applied

    @staticmethod
    def _validated_changes(total: int, state_changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        seen: set[int] = set()
        for change in state_changes:
            index = int(change.get("index", 0) or 0)
            current = str(change.get("current_state") or "")
            target = str(change.get("target_state") or "")
            if not 1 <= index <= total or current not in EQUIPMENT_STATES or target not in EQUIPMENT_STATES:
                continue
            if current == target or index in seen:
                continue
            seen.add(index)
            changes.append(change)
        return sorted(changes, key=lambda row: int(row["index"]), reverse=True)

    def _select_slot(self, frame: Any, slot: Any) -> Any:
        local = self.scanner.layout.cell_center(slot.row, slot.column, frame.rect.width, frame.rect.height)
        safe_y = round(
            int(getattr(self.scanner, "CLICK_SAFE_OFFSET_Y_2K", 0))
            * self.scanner._content_height(frame)
            / int(getattr(self.scanner.layout, "base_height", 1440))
        )
        self.scanner._input.click(
            (frame.rect.left + local[0], frame.rect.top + local[1] + safe_y),
            content_height=self.scanner._content_height(frame),
        )
        selected = self.scanner._wait_for_selected_panel(slot, 0)
        if selected is None:
            raise RuntimeError(f"第 {slot.index} 件状态管理定位后详情面板未稳定")
        return selected

    def _scroll_previous_page(self, frame: Any, current_page: int) -> Any:
        anchor = self.scanner.layout.cell_center(1, 0, frame.rect.width, frame.rect.height)
        position = (frame.rect.left + anchor[0], frame.rect.top + anchor[1])
        for amount in reversed(self.scanner._scroll_amounts_for_flip(current_page)):
            self._check_stopped()
            self.scanner._input.scroll(position, -int(amount))
        current = self.scanner._capture_frame()
        if current_page == 1:
            self._settle_at_top(current, position)
            current = self.scanner._capture_frame()
        return current

    def _settle_at_top(self, frame: Any, position: tuple[int, int]) -> None:
        for _ in range(4):
            self._check_stopped()
            self.scanner._input.scroll(position, 280)

    def _apply_transition(self, frame: Any, current: str, target: str) -> Any:
        if target == "discarded":
            frame = self._click_ratio(frame, TRASH_BUTTON_CENTER)
            if current == "locked":
                self.sleep_fn(self.LOCK_TO_DISCARD_POPUP_SECONDS)
                frame = self.scanner._capture_frame()
                if not self.popup_detector(frame.image):
                    raise RuntimeError("锁定切换弃置后未检测到确认弹窗")
                frame = self._click_ratio(frame, CONFIRM_BUTTON_CENTER)
                self.sleep_fn(self.LOCK_TO_DISCARD_DISMISS_SECONDS)
                frame = self.scanner._capture_frame()
                if self.popup_detector(frame.image):
                    raise RuntimeError("锁定切换弃置后确认弹窗未消失")
                return frame
            else:
                self.sleep_fn(self.ACTION_SETTLE_SECONDS)
        elif target == "locked":
            frame = self._click_ratio(frame, LOCK_BUTTON_CENTER)
            self.sleep_fn(self.ACTION_SETTLE_SECONDS)
        elif current == "discarded":
            frame = self._click_ratio(frame, TRASH_BUTTON_CENTER)
            self.sleep_fn(self.ACTION_SETTLE_SECONDS)
        elif current == "locked":
            frame = self._click_ratio(frame, LOCK_BUTTON_CENTER)
            self.sleep_fn(self.ACTION_SETTLE_SECONDS)
        return self.scanner._capture_frame()

    def _wait_for_state(self, frame: Any, target: str) -> tuple[Any, str]:
        current = frame
        detected = self.state_detector(current.image)
        for _ in range(5):
            if detected == target:
                return current, detected
            self.sleep_fn(0.1)
            current = self.scanner._capture_frame()
            detected = self.state_detector(current.image)
        return current, detected

    def _reference_panel_matches(self, index: int, image: Any) -> bool:
        reference = imread_unicode(
            str(Path(self.scanner.output_dir) / f"raw_drive_{int(index):04d}.png")
        )
        if reference is None:
            return False
        expected = self.scanner._panel_signature(reference)
        actual = self.scanner._panel_signature(image)
        return bool(float(cv2.absdiff(expected, actual).mean()) <= self.PANEL_IDENTITY_MAX_DIFF)

    def _write_report(
        self,
        *,
        status: str,
        requested: int,
        applied: int,
        started: float,
        transitions: dict[str, int],
        failure_type: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "schema": "mouse-equipment-state-sync-report-v1",
            "status": status,
            "requested": int(requested),
            "applied": int(applied),
            "transitions": dict(sorted(transitions.items())),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
        if failure_type:
            payload["failure_type"] = failure_type
        target = Path(self.scanner.output_dir) / "mouse_state_sync_last_report.json"
        temporary = target.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, target)
        except OSError as exc:
            logger.warning(f"鼠标状态同步诊断报告写入失败: {type(exc).__name__}")

    def _click_ratio(self, frame: Any, ratio: tuple[float, float]) -> Any:
        left, top, width, height = game_content_rect(frame.rect.width, frame.rect.height)
        local = (left + round(width * ratio[0]), top + round(height * ratio[1]))
        self.scanner._input.click(
            (frame.rect.left + local[0], frame.rect.top + local[1]),
            content_height=self.scanner._content_height(frame),
        )
        return self.scanner._capture_frame()

    def _check_stopped(self) -> None:
        if self.scanner._stopped:
            raise RuntimeError("鼠标状态同步已停止")
