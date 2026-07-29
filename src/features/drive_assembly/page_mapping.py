# 将装配块图纸坐标映射到实际装配页面像素坐标。
"""Map drive assembly blocks from blueprint grid coordinates to page pixels."""

from __future__ import annotations

from typing import Any

from src.features.drive_assembly.page_mapping_helpers import (
    DEFAULT_DRIVE_EQUIP_FIRST_RESULT,
    DEFAULT_EQUIPMENT_REUSE_PROMPT,
    DEFAULT_PAGE_CALIBRATION,
    FILTER_NAVIGATION_PAUSE_SECONDS,
    PageCalibration,
    _drive_sub_stat_names,
    _drive_target_position,
    _scale_controls,
)

__all__ = [
    "DEFAULT_PAGE_CALIBRATION",
    "PageCalibration",
    "map_assembly_page_prepare_controls",
    "map_blocks_to_page",
    "map_drive_block_installation",
    "map_drive_blocks_installation",
    "map_drive_filter_refinement",
    "map_drive_page_controls",
    "map_drive_set_selection",
    "map_drive_shape_selection",
    "map_filter_reset",
    "map_page_controls",
    "map_tape_equip_first_result",
    "map_tape_filter_controls",
    "map_tape_filter_refinement",
    "map_tape_main_stat_gamepad_open",
    "map_tape_main_stat_scroll",
    "map_tape_main_stat_selection",
    "map_tape_set_selection",
    "map_tape_sub_stat_filter_entry",
    "map_tape_sub_stat_selection",
]

from src.features.drive_assembly.page_navigation_mapping import (
    map_blocks_to_page,
    map_page_controls,
    map_drive_page_controls,
    map_assembly_page_prepare_controls,
    map_tape_filter_controls,
    map_drive_shape_selection,
    map_drive_set_selection,
    map_filter_reset,
    map_tape_set_selection,
)

from src.features.drive_assembly.page_filter_mapping import (
    map_tape_filter_refinement,
    map_drive_filter_refinement,
    map_tape_main_stat_scroll,
    map_tape_main_stat_gamepad_open,
    map_tape_main_stat_selection,
    map_tape_sub_stat_filter_entry,
    map_tape_sub_stat_selection,
    map_tape_equip_first_result,
)


def map_drive_block_installation(
    block: dict[str, Any],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 1200,
    open_filter: bool = False,
) -> dict[str, Any]:
    """Return the filter and drag actions for installing one drive block."""

    drive = block.get("drive") if isinstance(block.get("drive"), dict) else {}
    drive_type = str(block.get("drive_type") or drive.get("shape_id") or "")
    quality = str(drive.get("quality") or "Gold")
    sub_stats = _drive_sub_stat_names(drive.get("sub_stats"))
    reset = map_filter_reset(screen_size, content_rect)
    page_controls = map_drive_page_controls(screen_size, content_rect)
    shape_selection = map_drive_shape_selection(drive_type, screen_size, content_rect)
    is_duplicate = _is_duplicate_drive_block(block)
    refinement = map_drive_filter_refinement(
        [quality],
        sub_stats,
        screen_size,
        content_rect,
        include_status_filters=is_duplicate,
    )
    controls = _scale_controls(DEFAULT_DRIVE_EQUIP_FIRST_RESULT, screen_size, content_rect)
    prompt = _scale_controls(DEFAULT_EQUIPMENT_REUSE_PROMPT, screen_size, content_rect)
    target_position = _drive_target_position(block, screen_size, content_rect)
    result: dict[str, Any] = {
        "block_id": block.get("block_id"),
        "drive_type": shape_selection["drive_type"],
        "shape_option": shape_selection["shape_option"],
        "first_drive": controls["first_drive"],
        "target_position": target_position,
        "confirm_filter": controls["confirm_filter"],
        "reuse_prompt_confirm": prompt["reuse_prompt_confirm"],
        "reuse_prompt_probe": prompt["reuse_prompt_probe"],
        "duplicate_status_filter_enabled": is_duplicate,
        "duplicate_group_id": block.get("duplicate_group_id"),
    }
    sequence: list[dict[str, Any]] = []
    if open_filter:
        sequence.append(
            {
                "name": "filter_button",
                "position": page_controls["filter_button"],
                "post_action_pause_seconds": FILTER_NAVIGATION_PAUSE_SECONDS,
            }
        )
    sequence.extend(reset["reset_sequence"])
    sequence.extend(shape_selection["selection_sequence"])
    if is_duplicate:
        for action in refinement["refinement_sequence"]:
            if action.get("name") in {"status_locked", "status_discarded", "status_other"}:
                action["block_id"] = block.get("block_id")
                action["duplicate_group_id"] = block.get("duplicate_group_id")
                action["duplicate_status_filter"] = True
    sequence.extend(refinement["refinement_sequence"])
    sequence.append({"name": "confirm_filter", "position": result["confirm_filter"]})
    sequence.append(
        {
            "name": "capture_drive_target_baseline",
            "block_id": block.get("block_id"),
            "target_position": result["target_position"],
            "sample_radius": 12,
        }
    )
    sequence.append(
        {
            "name": "force_drag_first_drive_to_block",
            "block_id": block.get("block_id"),
            "from": result["first_drive"],
            "to": result["target_position"],
            "duration_ms": duration_ms,
        }
    )
    sequence.append({"name": "wait_for_equipment_reuse_prompt", "wait_seconds": 0.3})
    sequence.append(
        {
            "name": "confirm_equipment_reuse_prompt",
            "block_id": block.get("block_id"),
            "optional_confirm_position": result["reuse_prompt_confirm"],
            "modal_probe_position": result["reuse_prompt_probe"],
            "brightness_threshold": 150,
        }
    )
    sequence.append({"name": "wait_after_drive_block_install", "wait_seconds": 1.0})
    sequence.append(
        {
            "name": "verify_drive_block_installed",
            "block_id": block.get("block_id"),
            "target_position": result["target_position"],
            "retry_from": result["first_drive"],
            "retry_to": result["target_position"],
            "retry_duration_ms": duration_ms,
            "sample_radius": 12,
            "change_threshold": 15.0,
            "brightness_threshold": 80.0,
            "optional_confirm_position": result["reuse_prompt_confirm"],
            "modal_probe_position": result["reuse_prompt_probe"],
            "retry_prompt_wait_seconds": 0.3,
            "retry_settle_seconds": 1.0,
        }
    )
    result["install_sequence"] = sequence
    return result


def map_drive_blocks_installation(
    blocks: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 1200,
) -> dict[str, Any]:
    """Return a per-block drive assembly plan.

    Each block is filtered and dragged independently so the first filtered
    result always corresponds to the current blueprint block being installed.
    """

    page_controls = map_drive_page_controls(screen_size, content_rect)
    install_plans = [
        map_drive_block_installation(
            block,
            screen_size,
            content_rect,
            duration_ms,
            open_filter=True,
        )
        for block in blocks
    ]
    result: dict[str, Any] = {
        "page_controls": page_controls,
        "install_plans": install_plans,
    }
    sequence: list[dict[str, Any]] = [
        {
            "name": "drive_tab",
            "position": page_controls["drive_tab"],
            "post_action_pause_seconds": FILTER_NAVIGATION_PAUSE_SECONDS,
        }
    ]
    sequence.extend(
        {
            "name": "install_drive_block",
            "block_id": install.get("block_id"),
            "sequence_index": index,
        }
        for index, install in enumerate(install_plans)
    )
    result["assembly_sequence"] = sequence
    return result


def _is_duplicate_drive_block(block: dict[str, Any]) -> bool:
    """Accept both current duplicate flags and persisted group metadata."""

    drive = block.get("drive") if isinstance(block.get("drive"), dict) else {}
    return bool(
        block.get("is_duplicate_drive")
        or block.get("is_duplicate_equipment")
        or drive.get("is_duplicate_drive")
        or drive.get("is_duplicate_equipment")
        or int(block.get("duplicate_count") or 0) > 1
        or int(drive.get("duplicate_count") or 0) > 1
    )
