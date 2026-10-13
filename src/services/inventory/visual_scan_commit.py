# 校验完整视觉扫描结果并原子写入账号库存快照。
"""Completion gate shared by mouse and virtual-gamepad full visual scans."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import CancelledError
from pathlib import Path
from threading import Event
from typing import Any

from src.services.inventory.vision_snapshot import import_vision_inventory
from src.services.recognition.resources import check_recognition_resources
from src.integrations.vision.recognition.image_resources import RecognitionImageProfile
from src.integrations.vision.recognition.language_resources import OcrRuntimeResources
from src.integrations.vision.recognition.resources import RecognitionResources, RecognitionResourceError


class IncompleteVisionScanError(RuntimeError):
    def __init__(self, *, expected_count: int, parsed_count: int, failed_count: int) -> None:
        super().__init__(
            f"预计 {expected_count} 件，完整解析 {parsed_count} 件，失败 {failed_count} 件。"
        )
        self.expected_count = expected_count
        self.parsed_count = parsed_count
        self.failed_count = failed_count


def _unknown_tape_main_numbers(items: Sequence[Mapping[str, Any]]) -> tuple[int, ...]:
    numbers: list[int] = []
    for fallback_number, item in enumerate(items, start=1):
        if str(item.get("item_type") or "") != "tape":
            continue
        if str(item.get("main_stats") or "").strip() != "未知主词条":
            continue
        try:
            number = int(item.get("_scan_number") or fallback_number)
        except (TypeError, ValueError):
            number = fallback_number
        if number not in numbers:
            numbers.append(number)
    return tuple(sorted(numbers))


def append_tape_main_warning(summary: str, items: Sequence[Mapping[str, Any]]) -> str:
    """Append one bounded warning only when a scanned card main stat is unknown."""

    numbers = _unknown_tape_main_numbers(items)
    if not numbers:
        return summary
    listed = "、".join(f"{number}号" for number in numbers[:2])
    suffix = "……" if len(numbers) > 2 else ""
    return f"{summary}\n卡带主词条解析失败：{len(numbers)}个（{listed}{suffix}）。"


def _check_capture_cancel(stats: Mapping[str, Any]) -> None:
    token = stats.get("capture_cancel_token")
    if token is not None and not isinstance(token, Event):
        raise RecognitionResourceError("扫描结果的取消身份无效。")
    if token is not None and token.is_set():
        raise CancelledError("本次扫描已取消，原截图与背包保留。")


def check_vision_result_resources(stats: Mapping[str, Any], *, cancel_check=None) -> None:
    def authorize():
        _check_capture_cancel(stats)
        if cancel_check is not None and cancel_check():
            raise CancelledError("本次扫描已取消，原截图与背包保留。")
    authorize()
    resources = stats.get("recognition_resources")
    if isinstance(resources, RecognitionResources):
        check_recognition_resources(resources)
    authorize()


def commit_completed_vision_inventory(
    database_path: str | Path,
    stats: Mapping[str, Any],
    manual_items: Sequence[Mapping[str, Any]],
    *, result_is_current: Callable[[], bool] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> int | None:
    vision_items = list(stats.get("vision_items") or [])
    if str(stats.get("parse_scope") or "") not in {"full", "all"}:
        check_vision_result_resources(stats, cancel_check=cancel_check)
        return None
    complete_items = [*vision_items, *manual_items]
    expected_count = int(stats.get("total_count", 0) or 0)
    failed_count = int(stats.get("failed_count", 0) or 0)
    if failed_count or (expected_count and len(complete_items) != expected_count):
        raise IncompleteVisionScanError(
            expected_count=expected_count,
            parsed_count=len(complete_items),
            failed_count=failed_count,
        )
    if not complete_items:
        return None
    resources = stats.get("recognition_resources")
    if (not isinstance(resources, RecognitionResources)
            or not isinstance(resources.image_profile, RecognitionImageProfile)
            or not isinstance(resources.image_profile.ocr_resources, OcrRuntimeResources)
            or frozenset(file.shape_id for file in resources.image_profile.template_files) != resources.shape_ids):
        raise RecognitionResourceError("全量视觉结果缺少本批识别资料，请重新解析；当前背包保留。")

    def authorize() -> None:
        # import_vision_inventory 已在同一授权点核对完整资源，不重复哈希大模型。
        _check_capture_cancel(stats)
        if (result_is_current is not None and not result_is_current()
                or cancel_check is not None and cancel_check()):
            raise CancelledError("本次视觉扫描已失效，当前背包保留。")

    return import_vision_inventory(
        database_path,
        complete_items,
        capture_driver=str(stats.get("capture_driver") or "mouse"),
        resources=resources, before_commit=authorize,
    )
