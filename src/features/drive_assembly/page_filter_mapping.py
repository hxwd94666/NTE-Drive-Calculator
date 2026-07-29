# 映射卡带与驱动的主副词条筛选和首项装配动作。
"""Map drive assembly blocks from blueprint grid coordinates to page pixels."""

from __future__ import annotations

from typing import Any

from src.features.drive_assembly.page_mapping_helpers import (
    _normalize_tape_main_stat,
    _normalize_tape_sub_stat,
    _quality_control_name,
    _scale_controls,
    _scale_region,
)


from src.features.drive_assembly.page_mapping_helpers import (
    DEFAULT_DRIVE_FILTER_QUALITY_CONTROLS,
    DEFAULT_DRIVE_FILTER_QUALITY_SELECTION_PROBES,
    DEFAULT_DRIVE_FILTER_STATUS_CONTROLS,
    DEFAULT_DRIVE_FILTER_SUB_STAT_CONTROLS,
    DEFAULT_DRIVE_SUB_STAT_SCROLL,
    DEFAULT_EQUIPMENT_REUSE_PROMPT,
    DEFAULT_TAPE_EQUIP_FIRST_RESULT,
    DEFAULT_TAPE_FILTER_MAIN_STAT_CONTROLS,
    DEFAULT_TAPE_FILTER_QUALITY_CONTROLS,
    DEFAULT_TAPE_FILTER_QUALITY_SELECTION_PROBES,
    DEFAULT_TAPE_FILTER_STATUS_CONTROLS,
    DEFAULT_TAPE_MAIN_STAT_OCR_REGION,
    DEFAULT_TAPE_MAIN_STAT_OPTIONS,
    DEFAULT_TAPE_MAIN_STAT_SCROLL,
    DEFAULT_TAPE_SUB_STAT_FILTER_ENTRY,
    DEFAULT_TAPE_SUB_STAT_OPTIONS,
    DEFAULT_TAPE_SUB_STAT_SELECTION,
    FILTER_OPTION_PAUSE_SECONDS,
    TAPE_FILTER_RESULT_SETTLE_SECONDS,
    TAPE_MAIN_STAT_GAMEPAD_ACTION_PAUSE_SECONDS,
    TAPE_MODAL_DISMISS_SETTLE_SECONDS,
)



def map_tape_filter_refinement(
    qualities: list[str] | tuple[str, ...],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    include_main_stat_expand: bool = True,
    include_status_filters: bool = False,
) -> dict[str, Any]:
    """Return filter positions after the set has been selected."""

    status_controls = _scale_controls(DEFAULT_TAPE_FILTER_STATUS_CONTROLS, screen_size, content_rect)
    quality_controls = _scale_controls(DEFAULT_TAPE_FILTER_QUALITY_CONTROLS, screen_size, content_rect)
    quality_probes = _scale_controls(DEFAULT_TAPE_FILTER_QUALITY_SELECTION_PROBES, screen_size, content_rect)
    result: dict[str, Any] = {}
    for name in ("status_locked", "status_discarded", "status_other"):
        result[name] = status_controls[name]
    sequence: list[dict[str, Any]] = []
    if include_status_filters:
        sequence.extend(
            [
                {
                    "name": "status_locked",
                    "position": result["status_locked"],
                    "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
                },
                {
                    "name": "status_discarded",
                    "position": result["status_discarded"],
                    "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
                },
                {
                    "name": "status_other",
                    "position": result["status_other"],
                    "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
                },
            ]
        )
    for quality in qualities:
        control_name = _quality_control_name(quality)
        result[control_name] = quality_controls[control_name]
        sequence.append(
            {
                "name": control_name,
                "quality": quality,
                "position": result[control_name],
                "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
            }
        )
        sequence.append(
            {
                "name": "verify_quality_selected",
                "quality": quality,
                "selection_probe_position": quality_probes[control_name],
                "retry_position": result[control_name],
            }
        )
    if include_main_stat_expand:
        main_stat_controls = _scale_controls(DEFAULT_TAPE_FILTER_MAIN_STAT_CONTROLS, screen_size, content_rect)
        result["main_stat_expand"] = main_stat_controls["main_stat_expand"]
        sequence.append({"name": "main_stat_expand", "position": result["main_stat_expand"]})
        sequence.append({"name": "wait_after_main_stat_expand", "wait_seconds": 0.5})
    result["refinement_sequence"] = sequence
    return result


def map_drive_filter_refinement(
    qualities: list[str] | tuple[str, ...],
    sub_stats: list[str] | tuple[str, ...],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 500,
    include_status_filters: bool = True,
    bottom_scroll_count: int = 1,
) -> dict[str, Any]:
    """Return drive filter positions after the shape has been selected."""

    status_controls = _scale_controls(DEFAULT_DRIVE_FILTER_STATUS_CONTROLS, screen_size, content_rect)
    quality_controls = _scale_controls(DEFAULT_DRIVE_FILTER_QUALITY_CONTROLS, screen_size, content_rect)
    quality_probes = _scale_controls(DEFAULT_DRIVE_FILTER_QUALITY_SELECTION_PROBES, screen_size, content_rect)
    sub_stat_controls = _scale_controls(DEFAULT_DRIVE_FILTER_SUB_STAT_CONTROLS, screen_size, content_rect)
    scroll_controls = _scale_controls(DEFAULT_DRIVE_SUB_STAT_SCROLL, screen_size, content_rect)
    normalized_stats = [_normalize_tape_sub_stat(stat) for stat in sub_stats]
    option_controls = _scale_controls(
        {stat: DEFAULT_TAPE_SUB_STAT_OPTIONS[stat] for stat in normalized_stats},
        screen_size,
        content_rect,
    )
    result: dict[str, Any] = {}
    for name in ("status_locked", "status_discarded", "status_other"):
        result[name] = status_controls[name]
    sequence = []
    if include_status_filters:
        sequence.extend(
            [
                {
                    "name": "status_locked",
                    "position": result["status_locked"],
                    "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
                },
                {
                    "name": "status_discarded",
                    "position": result["status_discarded"],
                    "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
                },
                {
                    "name": "status_other",
                    "position": result["status_other"],
                    "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
                },
            ]
        )
    for quality in qualities:
        control_name = _quality_control_name(quality)
        result[control_name] = quality_controls[control_name]
        sequence.append(
            {
                "name": control_name,
                "quality": quality,
                "position": result[control_name],
                "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
            }
        )
        sequence.append(
            {
                "name": "verify_quality_selected",
                "quality": quality,
                "selection_probe_position": quality_probes[control_name],
                "retry_position": result[control_name],
            }
        )
    result["sub_stat_expand"] = sub_stat_controls["sub_stat_expand"]
    result["sub_stat_scroll_start"] = scroll_controls["sub_stat_scroll_start"]
    result["sub_stat_scroll_end"] = scroll_controls["sub_stat_scroll_end"]
    result["sub_stat_options"] = option_controls
    result["sub_stat_count_four"] = sub_stat_controls["sub_stat_count_four"]
    sequence.extend(
        {
            "name": "drive_filter_scroll_to_bottom",
            "from": result["sub_stat_scroll_start"],
            "to": result["sub_stat_scroll_end"],
            "duration_ms": duration_ms,
        }
        for _index in range(max(1, int(bottom_scroll_count)))
    )
    sequence.append({"name": "sub_stat_expand", "position": result["sub_stat_expand"]})
    sequence.append({"name": "wait_after_drive_sub_stat_expand", "wait_seconds": 0.5})
    sequence.extend(
        {
            "name": "drive_sub_stat_scroll_to_bottom",
            "from": result["sub_stat_scroll_start"],
            "to": result["sub_stat_scroll_end"],
            "duration_ms": duration_ms,
        }
        for _index in range(max(1, int(bottom_scroll_count)))
    )
    sequence.extend(
        {
            "name": "sub_stat_option",
            "sub_stat": stat,
            "position": option_controls[stat],
            "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
        }
        for stat in normalized_stats
    )
    sequence.append(
        {
            "name": "sub_stat_count_four",
            "position": result["sub_stat_count_four"],
            "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
        }
    )
    result["refinement_sequence"] = sequence
    return result


def map_tape_main_stat_scroll(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 500,
) -> dict[str, Any]:
    """Return the drag action that scrolls main stat options to the second page."""

    controls = _scale_controls(DEFAULT_TAPE_MAIN_STAT_SCROLL, screen_size, content_rect)
    result: dict[str, Any] = {
        "main_stat_scroll_start": controls["main_stat_scroll_start"],
        "main_stat_scroll_end": controls["main_stat_scroll_end"],
    }
    result["scroll_sequence"] = [
        {
            "name": "main_stat_scroll_to_second_page",
            "from": result["main_stat_scroll_start"],
            "to": result["main_stat_scroll_end"],
            "duration_ms": duration_ms,
        }
    ]
    return result


def map_tape_main_stat_gamepad_open() -> dict[str, Any]:
    """Return gamepad actions that open the tape main-stat list."""

    sequence: list[dict[str, Any]] = []
    sequence.extend(
        {
            "name": "main_stat_gamepad_down_to_expand",
            "gamepad_stick": "left_down",
            "post_action_pause_seconds": TAPE_MAIN_STAT_GAMEPAD_ACTION_PAUSE_SECONDS,
        }
        for _index in range(7)
    )
    sequence.append(
        {
            "name": "main_stat_gamepad_confirm_expand",
            "gamepad_button": "a",
            "post_action_pause_seconds": TAPE_MAIN_STAT_GAMEPAD_ACTION_PAUSE_SECONDS,
        }
    )
    sequence.extend(
        {
            "name": "main_stat_gamepad_down_to_options",
            "gamepad_stick": "left_down",
            "post_action_pause_seconds": TAPE_MAIN_STAT_GAMEPAD_ACTION_PAUSE_SECONDS,
        }
        for _index in range(3)
    )
    return {"open_sequence": sequence}


def map_tape_main_stat_selection(
    main_stat: str,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return the click position for the tape main stat required by the blueprint."""

    normalized = _normalize_tape_main_stat(main_stat)
    controls = _scale_controls({normalized: DEFAULT_TAPE_MAIN_STAT_OPTIONS[normalized]}, screen_size, content_rect)
    region = _scale_region(DEFAULT_TAPE_MAIN_STAT_OCR_REGION["main_stat_ocr_region"], screen_size, content_rect)
    result: dict[str, Any] = {
        "main_stat": normalized,
        "main_stat_option": controls[normalized],
        "main_stat_ocr_region": region,
    }
    result["selection_sequence"] = [
        {
            "name": "main_stat_option",
            "main_stat": normalized,
            "position": result["main_stat_option"],
            "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
        }
    ]
    result["ocr_selection_sequence"] = [
        {
            "name": "main_stat_option",
            "main_stat": normalized,
            "ocr_target_text": normalized,
            "ocr_search_region": region,
            "fallback_position": result["main_stat_option"],
            "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
        }
    ]
    return result


def map_tape_sub_stat_filter_entry(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    scroll_count: int = 1,
    duration_ms: int = 500,
) -> dict[str, Any]:
    """Return drag actions for reaching and opening the tape sub-stat filter."""

    controls = _scale_controls(DEFAULT_TAPE_SUB_STAT_FILTER_ENTRY, screen_size, content_rect)
    result: dict[str, Any] = {
        "sub_stat_scroll_start": controls["sub_stat_scroll_start"],
        "sub_stat_scroll_end": controls["sub_stat_scroll_end"],
        "sub_stat_expand": controls["sub_stat_expand"],
    }
    sequence: list[dict[str, Any]] = [
        {
            "name": "sub_stat_scroll_to_expand",
            "from": result["sub_stat_scroll_start"],
            "to": result["sub_stat_scroll_end"],
            "duration_ms": duration_ms,
        }
        for _index in range(scroll_count)
    ]
    sequence.append({"name": "sub_stat_expand", "position": result["sub_stat_expand"]})
    sequence.append({"name": "wait_after_sub_stat_expand", "wait_seconds": 0.5})
    result["entry_sequence"] = sequence
    return result


def map_tape_sub_stat_selection(
    sub_stats: list[str] | tuple[str, ...],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 500,
) -> dict[str, Any]:
    """Return clicks for tape sub-stats and the fixed sub-stat count of four."""

    scroll_controls = _scale_controls(DEFAULT_TAPE_SUB_STAT_SELECTION, screen_size, content_rect)
    normalized_stats = [_normalize_tape_sub_stat(stat) for stat in sub_stats]
    option_controls = _scale_controls(
        {stat: DEFAULT_TAPE_SUB_STAT_OPTIONS[stat] for stat in normalized_stats},
        screen_size,
        content_rect,
    )
    result: dict[str, Any] = {
        "sub_stat_options": option_controls,
        "sub_stat_count_four": scroll_controls["sub_stat_count_four"],
    }
    sequence: list[dict[str, Any]] = [
        {
            "name": "sub_stat_scroll_to_bottom",
            "from": scroll_controls["sub_stat_scroll_start"],
            "to": scroll_controls["sub_stat_scroll_end"],
            "duration_ms": duration_ms,
        }
    ]
    sequence.extend(
        {
            "name": "sub_stat_option",
            "sub_stat": stat,
            "position": option_controls[stat],
            "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
        }
        for stat in normalized_stats
    )
    sequence.append(
        {
            "name": "sub_stat_count_four",
            "position": result["sub_stat_count_four"],
            "post_action_pause_seconds": FILTER_OPTION_PAUSE_SECONDS,
        }
    )
    result["selection_sequence"] = sequence
    return result


def map_tape_equip_first_result(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 1200,
) -> dict[str, Any]:
    """Return actions for confirming the filter and equipping the first visible tape."""

    controls = _scale_controls(DEFAULT_TAPE_EQUIP_FIRST_RESULT, screen_size, content_rect)
    prompt = _scale_controls(DEFAULT_EQUIPMENT_REUSE_PROMPT, screen_size, content_rect)
    result: dict[str, Any] = {
        "confirm_filter": controls["confirm_filter"],
        "first_tape": controls["first_tape"],
        "tape_socket": controls["tape_socket"],
        "reuse_prompt_confirm": prompt["reuse_prompt_confirm"],
        "reuse_prompt_probe": prompt["reuse_prompt_probe"],
    }
    result["equip_sequence"] = [
        {
            "name": "confirm_filter",
            "position": result["confirm_filter"],
            "post_action_pause_seconds": 0.0,
        },
        {
            "name": "wait_after_tape_filter_confirm",
            "wait_seconds": TAPE_FILTER_RESULT_SETTLE_SECONDS,
            "post_action_pause_seconds": 0.0,
        },
        {
            "name": "drag_first_tape_to_socket",
            "from": result["first_tape"],
            "to": result["tape_socket"],
            "duration_ms": duration_ms,
        },
        {"name": "wait_for_equipment_reuse_prompt", "wait_seconds": 0.3},
        {
            "name": "confirm_equipment_reuse_prompt",
            "optional_confirm_position": result["reuse_prompt_confirm"],
            "modal_probe_position": result["reuse_prompt_probe"],
            "brightness_threshold": 150,
            "post_action_pause_seconds": TAPE_MODAL_DISMISS_SETTLE_SECONDS,
        },
    ]
    return result
