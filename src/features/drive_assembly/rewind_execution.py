# 执行倒带页面的余额识别、驱动定制和单抽/十连鼠标流程。
"""Mouse execution for a saved custom rewind plan."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from src.features.drive_assembly.input_backends import (
    MouseBackend,
    PyAutoGuiMouseBackend,
    f12_stop_checker,
)
from src.features.drive_assembly.page_navigation_mapping import map_rewind_controls
from src.features.drive_assembly.rewind_shape_detection import detect_rewind_shape_positions
from src.scanner.window_capture import WindowRect, capture_foreground_window
from src.services.blueprint_service import OFFICIAL_SHAPE_LABELS


REWIND_COST_PER_DRAW = 60
REWIND_DRAWS_PER_TEN = 10
REWIND_COST_PER_TEN = REWIND_COST_PER_DRAW * REWIND_DRAWS_PER_TEN
REWIND_SETUP_CLICK_PAUSE_SECONDS = 1.0
REWIND_RESULT_DISMISS_PAUSE_SECONDS = 1.0
REWIND_NEXT_DRAW_PAUSE_SECONDS = 0.5
REWIND_SLOT_CLICK_PAUSE_SECONDS = 0.2
QUALITY_TO_DIFFICULTY = {
    "blue": "difficulty_low",
    "purple": "difficulty_medium",
    "gold": "difficulty_advanced",
}
QUALITY_EXECUTION_ORDER = ("gold", "purple", "blue")


@dataclass(frozen=True, slots=True)
class RewindExecutionRequest:
    qualities: tuple[str, ...] = ("gold",)
    drive_customization: str = "none"
    shape_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RewindExecutionReport:
    currency: int
    planned_draws: int
    ten_draws: int
    single_draws: int
    executed_actions: int
    difficulty: str
    quality_draws: tuple[tuple[str, int], ...] = ()
    remaining_currency: int = 0
    quality_currencies: tuple[tuple[str, int], ...] = ()
    quality_remaining_currency: tuple[tuple[str, int], ...] = ()
    quality_ten_costs: tuple[tuple[str, int], ...] = ()


def parse_rewind_currency(texts: list[str] | tuple[str, ...]) -> int | None:
    """Extract the top-right currency integer from OCR text fragments."""

    values: list[int] = []
    fragments: list[str] = []
    for text in texts:
        for value in re.findall(r"\d{1,3}(?:[,，.\s]\d{3})+|\d+", str(text)):
            digits = "".join(re.findall(r"\d", value))
            if digits:
                values.append(int(digits))
                fragments.append(digits)
    for left, right in zip(fragments, fragments[1:]):
        if 1 <= len(left) <= 3 and len(right) == 3:
            values.append(int(left + right))
    return max(values) if values else None


def execute_rewind_request(
    request: RewindExecutionRequest,
    *,
    backend: MouseBackend | None = None,
    capture: Callable[[], tuple[Any, WindowRect]] = capture_foreground_window,
    ocr_engine: Any | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> RewindExecutionReport:
    """Select each difficulty, read its balance, and spend its complete tens."""

    _validate_rewind_request(request)
    _image, rect = capture()
    relative = map_rewind_controls(screen_size=(rect.width, rect.height))
    controls = _absolute_controls(relative, rect)
    selected_qualities = tuple(
        quality for quality in QUALITY_EXECUTION_ORDER if quality in request.qualities
    ) or ("gold",)
    primary_quality = selected_qualities[0]
    difficulty = QUALITY_TO_DIFFICULTY[primary_quality]

    action_backend = backend or PyAutoGuiMouseBackend()
    enable_randomization = getattr(action_backend, "enable_randomization", None)
    if callable(enable_randomization):
        enable_randomization()
    f12_checker = f12_stop_checker()
    stop_checker = lambda: bool(
        (should_stop is not None and should_stop()) or f12_checker()
    )
    executed = 0
    total_currency = 0
    total_ten_draws = 0
    quality_currencies: list[tuple[str, int]] = []
    quality_draws: list[tuple[str, int]] = []
    quality_remaining: list[tuple[str, int]] = []
    quality_ten_costs: list[tuple[str, int]] = []
    for quality in selected_qualities:
        quality_difficulty = QUALITY_TO_DIFFICULTY[quality]
        _click_and_pause(action_backend, controls[quality_difficulty], stop_checker)
        executed += 1

        quality_mode = "none" if quality == "blue" else request.drive_customization
        quality_request = RewindExecutionRequest((quality,), quality_mode, request.shape_ids)
        uses_custom_price = quality_mode != "none"
        configured = False
        if uses_custom_price:
            executed += _configure_drive_customization(
                action_backend, controls, quality_request, capture, stop_checker
            )
            configured = True

        image, selected_rect = capture()
        relative = map_rewind_controls(screen_size=(selected_rect.width, selected_rect.height))
        controls = _absolute_controls(relative, selected_rect)
        currency = _read_currency(image, relative["currency_counter_region"], ocr_engine)
        if currency is None:
            raise RuntimeError(f"未识别到 {quality_difficulty} 的倒带货币数量")
        ten_cost = REWIND_COST_PER_TEN
        if uses_custom_price:
            custom_cost = _read_currency(image, relative["custom_ten_cost_region"], ocr_engine)
            if custom_cost is None or custom_cost <= 0:
                raise RuntimeError(f"未识别到 {quality_difficulty} 的定制十连价格")
            ten_cost = custom_cost
        ten_count = max(0, currency // ten_cost)
        draws = ten_count * REWIND_DRAWS_PER_TEN
        remaining = currency - ten_count * ten_cost
        total_currency += currency
        total_ten_draws += ten_count
        quality_currencies.append((quality, currency))
        quality_draws.append((quality, draws))
        quality_remaining.append((quality, remaining))
        quality_ten_costs.append((quality, ten_cost))
        if ten_count == 0:
            continue
        if not configured:
            executed += _configure_drive_customization(
                action_backend, controls, quality_request, capture, stop_checker
            )
            configured = True
        for _ in range(ten_count):
            _click(action_backend, controls["rewind_ten"], stop_checker)
            _dismiss_rewind_result(action_backend, stop_checker)
            executed += 1
    planned_draws = total_ten_draws * REWIND_DRAWS_PER_TEN
    return RewindExecutionReport(
        total_currency,
        planned_draws,
        total_ten_draws,
        0,
        executed,
        difficulty,
        tuple(quality_draws),
        sum(value for _quality, value in quality_remaining),
        tuple(quality_currencies),
        tuple(quality_remaining),
        tuple(quality_ten_costs),
    )


def _dismiss_rewind_result(
    backend: MouseBackend,
    should_stop: Callable[[], bool] | None,
) -> None:
    backend.pause(REWIND_RESULT_DISMISS_PAUSE_SECONDS)
    _press_key(backend, "esc", should_stop)
    backend.pause(REWIND_RESULT_DISMISS_PAUSE_SECONDS)
    _press_key(backend, "esc", should_stop)
    backend.pause(REWIND_NEXT_DRAW_PAUSE_SECONDS)

def _configure_drive_customization(
    backend: MouseBackend,
    controls: dict[str, Any],
    request: RewindExecutionRequest,
    capture: Callable[[], tuple[Any, WindowRect]],
    should_stop: Callable[[], bool] | None,
) -> int:
    mode = request.drive_customization
    if mode == "none":
        _click_and_pause(backend, controls["random_selection"], should_stop)
        return 1
    if mode == "enabled":
        _click_and_pause(backend, controls["drive_customization"], should_stop)
        return 1

    _click_and_pause(backend, controls["drive_customization"], should_stop)
    _click_and_pause(backend, controls["select_drives"], should_stop)
    _click_and_pause(backend, controls["customization_menu"], should_stop)
    _click_and_pause(backend, controls["customization_free_match"], should_stop)
    _click_with_pause(
        backend,
        controls["customization_menu_dismiss"],
        should_stop,
        REWIND_SLOT_CLICK_PAUSE_SECONDS,
    )

    selection_image, selection_rect = capture()
    selection_relative = map_rewind_controls(
        screen_size=(selection_rect.width, selection_rect.height)
    )
    selection_controls = _absolute_controls(selection_relative, selection_rect)
    detected_shapes = detect_rewind_shape_positions(
        selection_image,
        tuple(selection_relative["available_drive_shapes"].values()),
    )
    for position in selection_controls["selected_drive_remove"]:
        _click_with_pause(
            backend, position, should_stop, REWIND_SLOT_CLICK_PAUSE_SECONDS
        )
    for shape_id in request.shape_ids:
        control = _shape_control_name(shape_id)
        relative_position = detected_shapes[control]
        _click_with_pause(
            backend,
            (selection_rect.left + relative_position[0], selection_rect.top + relative_position[1]),
            should_stop,
            REWIND_SLOT_CLICK_PAUSE_SECONDS,
        )
    _click_and_pause(
        backend, selection_controls["confirm_drive_selection"], should_stop
    )
    # Confirming the eighth-slot selection returns to the draw page itself.
    # Do not send Escape here: it would immediately dismiss that draw page.
    return 22


def _validate_rewind_request(request: RewindExecutionRequest) -> None:
    if request.drive_customization not in {"none", "enabled", "apply_plan"}:
        raise ValueError(f"unknown rewind drive customization mode: {request.drive_customization}")
    if request.drive_customization == "apply_plan" and len(request.shape_ids) != 8:
        raise ValueError("应用倒带方案需要恰好 8 个驱动候选")


def _shape_control_name(shape_id: str) -> str:
    normalized = OFFICIAL_SHAPE_LABELS.get(str(shape_id), str(shape_id))
    if normalized not in {
        "H_2", "V_2", "H_3", "V_3", "H_4", "V_4",
        "Trap_4_H", "Trap_4_V", "L_3_BL", "L_3_TL", "L_3_TR", "L_3_BR",
    }:
        raise ValueError(f"未映射的倒带驱动形状: {shape_id}")
    return normalized


def _read_currency(image: Any, region: tuple[int, int, int, int], ocr_engine: Any | None) -> int | None:
    array = np.asarray(image)
    x1, y1, x2, y2 = region
    cropped = array[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
    if cropped.size == 0:
        return None
    if ocr_engine is None:
        from src.scanner.ocr_engine import OCREngine

        ocr_engine = OCREngine()
    return parse_rewind_currency(ocr_engine.extract_text(cropped))


def _absolute_controls(relative: dict[str, Any], rect: WindowRect) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in relative.items():
        if name.endswith("_region"):
            result[name] = value
        elif name == "selected_drive_remove":
            result[name] = [(rect.left + x, rect.top + y) for x, y in value]
        elif name == "available_drive_shapes":
            result[name] = {key: (rect.left + x, rect.top + y) for key, (x, y) in value.items()}
        else:
            x, y = value
            result[name] = (rect.left + x, rect.top + y)
    return result


def _click(backend: MouseBackend, position: tuple[int, int], should_stop: Callable[[], bool] | None) -> None:
    if should_stop is not None and should_stop():
        raise RuntimeError("倒带执行已停止")
    backend.click(position)


def _click_and_pause(
    backend: MouseBackend,
    position: tuple[int, int],
    should_stop: Callable[[], bool] | None,
) -> None:
    _click(backend, position, should_stop)
    backend.pause(REWIND_SETUP_CLICK_PAUSE_SECONDS)


def _click_with_pause(
    backend: MouseBackend,
    position: tuple[int, int],
    should_stop: Callable[[], bool] | None,
    seconds: float,
) -> None:
    _click(backend, position, should_stop)
    backend.pause(seconds)


def _press_key(backend: MouseBackend, key: str, should_stop: Callable[[], bool] | None) -> None:
    if should_stop is not None and should_stop():
        raise RuntimeError("倒带执行已停止")
    backend.press_key(key)
