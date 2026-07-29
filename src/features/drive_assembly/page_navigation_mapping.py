# 映射自动装配页面的进入、筛选重置和套装选择动作。
"""Map drive assembly blocks from blueprint grid coordinates to page pixels."""

from __future__ import annotations

from typing import Any

from src.features.drive_assembly.page_mapping_helpers import (
    _map_block_to_page,
    _normalize_drive_shape,
    _normalize_tape_set_name,
    _scale_controls,
)


from src.features.drive_assembly.page_mapping_helpers import (
    DEFAULT_ASSEMBLY_PAGE_CONTROLS,
    DEFAULT_DRIVE_FILTER_CONTROLS,
    DEFAULT_DRIVE_PAGE_CONTROLS,
    DEFAULT_DRIVE_SHAPE_DIALOG_CONTROLS,
    DEFAULT_DRIVE_SHAPE_OPTIONS,
    DEFAULT_EQUIPMENT_REUSE_PROMPT,
    DEFAULT_FILTER_ACTION_CONTROLS,
    DEFAULT_PAGE_CALIBRATION,
    DEFAULT_PAGE_CONTROLS,
    DEFAULT_TAPE_FILTER_CONTROLS,
    DEFAULT_TAPE_SET_DIALOG_CONTROLS,
    DEFAULT_TAPE_SET_OPTIONS,
    FILTER_DIALOG_CLOSE_SETTLE_SECONDS,
    FILTER_NAVIGATION_PAUSE_SECONDS,
    PageCalibration,
    TAPE_MODAL_DISMISS_SETTLE_SECONDS,
)



def map_blocks_to_page(
    blocks: list[dict[str, Any]],
    screen_size: tuple[int, int] | None = None,
    calibration: PageCalibration = DEFAULT_PAGE_CALIBRATION,
    content_rect: tuple[int, int, int, int] | None = None,
) -> list[dict[str, Any]]:
    """Return copies of assembly blocks with centroid and pixel coordinates."""

    page = calibration.scaled(screen_size, content_rect)
    return [_map_block_to_page(block, page) for block in blocks]


def map_page_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return page control pixel positions for opening tape filters."""

    controls = _scale_controls(DEFAULT_PAGE_CONTROLS, screen_size, content_rect)
    controls["click_sequence"] = [
        {
            "name": "tape_tab",
            "position": controls["tape_tab"],
            "post_action_pause_seconds": FILTER_NAVIGATION_PAUSE_SECONDS,
        },
        {
            "name": "filter_button",
            "position": controls["filter_button"],
            "post_action_pause_seconds": FILTER_NAVIGATION_PAUSE_SECONDS,
        },
    ]
    return controls


def map_drive_page_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return page control pixel positions for opening drive filters."""

    controls = _scale_controls(DEFAULT_DRIVE_PAGE_CONTROLS, screen_size, content_rect)
    controls["click_sequence"] = [
        {
            "name": "drive_tab",
            "position": controls["drive_tab"],
            "post_action_pause_seconds": FILTER_NAVIGATION_PAUSE_SECONDS,
        },
        {
            "name": "filter_button",
            "position": controls["filter_button"],
            "post_action_pause_seconds": FILTER_NAVIGATION_PAUSE_SECONDS,
        },
    ]
    return controls


def map_assembly_page_prepare_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return controls used immediately after entering the assembly page."""

    controls = _scale_controls(DEFAULT_ASSEMBLY_PAGE_CONTROLS, screen_size, content_rect)
    prompt = _scale_controls(DEFAULT_EQUIPMENT_REUSE_PROMPT, screen_size, content_rect)
    controls["unload_prompt_confirm"] = prompt["reuse_prompt_confirm"]
    controls["unload_prompt_probe"] = prompt["reuse_prompt_probe"]
    controls["prepare_sequence"] = [
        {"name": "unload_existing_drives", "position": controls["unload_existing_drives"]},
        {"name": "wait_for_unload_existing_drives_prompt", "wait_seconds": 1.0},
        {
            "name": "confirm_unload_existing_drives_prompt",
            "optional_confirm_position": controls["unload_prompt_confirm"],
            "modal_probe_position": controls["unload_prompt_probe"],
            "brightness_threshold": 150,
            "post_action_pause_seconds": TAPE_MODAL_DISMISS_SETTLE_SECONDS,
        },
    ]
    return controls


def map_tape_filter_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return tape filter panel positions for choosing the required set."""

    controls = _scale_controls(DEFAULT_TAPE_FILTER_CONTROLS, screen_size, content_rect)
    controls["set_filter_sequence"] = [
        {"name": "set_select", "position": controls["set_select"]},
        {"name": "wait_after_tape_set_dialog_open", "wait_seconds": 0.5},
    ]
    return controls


def map_drive_shape_selection(
    drive_type: str,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return click positions for selecting a drive block shape."""

    normalized = _normalize_drive_shape(drive_type)
    filter_controls = _scale_controls(DEFAULT_DRIVE_FILTER_CONTROLS, screen_size, content_rect)
    shape_option = _scale_controls({normalized: DEFAULT_DRIVE_SHAPE_OPTIONS[normalized]}, screen_size, content_rect)[
        normalized
    ]
    dialog_controls = _scale_controls(DEFAULT_DRIVE_SHAPE_DIALOG_CONTROLS, screen_size, content_rect)
    result: dict[str, Any] = {
        "drive_type": normalized,
        "shape_select": filter_controls["shape_select"],
        "shape_option": shape_option,
        "confirm_filter": dialog_controls["confirm_filter"],
    }
    result["selection_sequence"] = [
        {"name": "shape_select", "position": result["shape_select"]},
        {"name": "wait_after_drive_shape_dialog_open", "wait_seconds": 0.5},
        {"name": "shape_option", "drive_type": normalized, "position": result["shape_option"]},
        {"name": "confirm_shape_filter", "position": result["confirm_filter"]},
        {"name": "wait_after_drive_shape_dialog_close", "wait_seconds": FILTER_DIALOG_CLOSE_SETTLE_SECONDS},
    ]
    return result


def map_drive_set_selection(
    set_name: str,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return click positions for selecting a drive set from the filter panel."""
    normalized_name = _normalize_tape_set_name(set_name)
    if normalized_name not in DEFAULT_TAPE_SET_OPTIONS:
        available = ", ".join(DEFAULT_TAPE_SET_OPTIONS)
        raise ValueError(f"unknown drive set: {set_name}. available sets: {available}")
    filter_controls = _scale_controls(DEFAULT_DRIVE_FILTER_CONTROLS, screen_size, content_rect)
    set_option = _scale_controls({normalized_name: DEFAULT_TAPE_SET_OPTIONS[normalized_name]}, screen_size, content_rect)[
        normalized_name
    ]
    dialog_controls = _scale_controls(DEFAULT_TAPE_SET_DIALOG_CONTROLS, screen_size, content_rect)
    result: dict[str, Any] = {
        "set_name": normalized_name,
        "set_select": filter_controls["set_select"],
        "set_option": set_option,
        "confirm_filter": dialog_controls["confirm_filter"],
    }
    result["selection_sequence"] = [
        {"name": "drive_set_select", "set_name": normalized_name, "position": result["set_select"]},
        {"name": "drive_set_option", "set_name": normalized_name, "position": result["set_option"]},
        {"name": "confirm_drive_set_filter", "position": result["confirm_filter"]},
        {"name": "wait_after_drive_set_dialog_close", "wait_seconds": FILTER_DIALOG_CLOSE_SETTLE_SECONDS},
    ]
    return result


def map_filter_reset(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return the filter reset button used before every new drive search."""

    controls = _scale_controls(DEFAULT_FILTER_ACTION_CONTROLS, screen_size, content_rect)
    controls["reset_sequence"] = [
        {
            "name": "reset_filter",
            "position": controls["reset_filter"],
            "post_action_pause_seconds": FILTER_NAVIGATION_PAUSE_SECONDS,
        }
    ]
    return controls


def map_tape_set_selection(
    set_name: str,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return click positions for selecting a tape set in the set filter dialog."""

    normalized_name = _normalize_tape_set_name(set_name)
    if normalized_name not in DEFAULT_TAPE_SET_OPTIONS:
        available = "、".join(DEFAULT_TAPE_SET_OPTIONS)
        raise ValueError(f"未知套装: {set_name}。可用套装: {available}")
    set_option = _scale_controls({normalized_name: DEFAULT_TAPE_SET_OPTIONS[normalized_name]}, screen_size, content_rect)[
        normalized_name
    ]
    controls = _scale_controls(DEFAULT_TAPE_SET_DIALOG_CONTROLS, screen_size, content_rect)
    result = {
        "set_name": normalized_name,
        "set_option": set_option,
        "confirm_filter": controls["confirm_filter"],
    }
    result["selection_sequence"] = [
        {"name": "set_option", "set_name": normalized_name, "position": result["set_option"]},
        {"name": "confirm_filter", "position": result["confirm_filter"]},
        {"name": "wait_after_tape_set_dialog_close", "wait_seconds": FILTER_DIALOG_CLOSE_SETTLE_SECONDS},
    ]
    return result
