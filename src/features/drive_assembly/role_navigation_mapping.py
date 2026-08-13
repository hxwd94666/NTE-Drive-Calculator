# 映射自动装配角色列表的鼠标、手柄和滚动导航动作。
"""Role recognition and traversal planning for drive assembly."""

from __future__ import annotations

from typing import Any

from src.features.drive_assembly.page_mapping_helpers import DEFAULT_ASSEMBLY_PAGE_CONTROLS

from src.features.drive_assembly.role_flow_helpers import (
    _scale_controls,
    _scale_point,
)


from src.features.drive_assembly.role_flow_helpers import (
    DEFAULT_DPAD_RESET_UP_COUNT,
    DEFAULT_ROLE_NAME_FALLBACK_REGION,
    DEFAULT_ROLE_NAME_REGION,
    DEFAULT_ROLE_LIST_ENTRY_CONTROLS,
    DEFAULT_ROLE_LIST_ENTRY_SCROLL,
    DEFAULT_ROLE_LIST_GRID_SLOT_POSITIONS,
    DEFAULT_ROLE_LIST_WHEEL_POSITION,
    DEFAULT_ROLE_NAVIGATION_CONTROLS,
    DEFAULT_ROLE_PAGE_RESET_SCROLLS,
    DEFAULT_ROLE_PAGE_SCROLL,
    DEFAULT_ROLE_SLOT_POSITIONS,
    ROLE_ASSEMBLE_PAGE_SETTLE_SECONDS,
    ROLE_KONGMU_TAB_SETTLE_SECONDS,
    ROLE_LIST_ENTRY_INFORMATION_SETTLE_SECONDS,
    ROLE_LIST_ENTRY_OPEN_SETTLE_SECONDS,
    ROLE_LIST_ENTRY_RESET_SCROLLS,
    ROLE_LIST_ENTRY_ROLE_SETTLE_SECONDS,
    ROLE_LIST_ENTRY_SCROLL_DURATION_MS,
    ROLE_LIST_ENTRY_SCROLL_SETTLE_SECONDS,
    ROLE_LIST_SELECTION_SETTLE_SECONDS,
    ROLE_LIST_WHEEL_CLICKS_PER_ROW,
    ROLE_LIST_WHEEL_CLICK_INTERVAL_SECONDS,
    ROLE_LIST_WHEEL_SETTLE_SECONDS,
    ROLE_LIST_GRID_COLUMNS,
    ROLE_LIST_INITIAL_LEFT_RESET_COUNT,
    ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
)



def map_role_navigation_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    cloud_nte_mode: bool = False,
) -> dict[str, Any]:
    """Return controls for entering the assembly page from a role page.

    Cloud NTE mode keeps the mouse click that switches to Kongmu, then uses
    gamepad D-pad right followed by Y to activate the assembly action.
    """

    controls = _scale_controls(DEFAULT_ROLE_NAVIGATION_CONTROLS, screen_size, content_rect)
    assembly_page_controls = _scale_controls(
        DEFAULT_ASSEMBLY_PAGE_CONTROLS,
        screen_size,
        content_rect,
    )
    controls["kongmu_sequence"] = [
        {"name": "left_kongmu_tab", "position": controls["left_kongmu_tab"]},
        {"name": "wait_after_left_kongmu_tab", "wait_seconds": ROLE_KONGMU_TAB_SETTLE_SECONDS},
    ]
    cloud_assemble_sequence = [
        {
            "name": "activate_assemble_button_gamepad",
            "gamepad_button": "dpad_right",
            "post_action_pause_seconds": 0.2,
        },
        {
            "name": "assemble_button",
            "gamepad_button": "y",
            "post_action_pause_seconds": ROLE_ASSEMBLE_PAGE_SETTLE_SECONDS,
        },
        {
            "name": "assembly_page_wake_mouse_after_gamepad",
            "position": assembly_page_controls["unload_existing_drives"],
            "mouse_move_only": True,
            "post_action_pause_seconds": 0.25,
        },
    ]
    controls["assemble_sequence"] = (
        cloud_assemble_sequence
        if cloud_nte_mode
        else [
            {"name": "assemble_button", "position": controls["assemble_button"]},
            {"name": "wait_after_assemble_button", "wait_seconds": ROLE_ASSEMBLE_PAGE_SETTLE_SECONDS},
        ]
    )
    controls["entry_sequence"] = [*controls["kongmu_sequence"], *controls["assemble_sequence"]]
    controls["exit_sequence"] = [
        {
            "name": "assembly_back_to_role_page",
            "keyboard_key": "esc",
            "post_action_pause_seconds": 1.5,
        },
    ]
    return controls


def map_role_slots(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> list[tuple[int, int]]:
    """Return the five visible role avatar click positions."""

    return [
        _scale_point(point, screen_size, content_rect)
        for point in DEFAULT_ROLE_SLOT_POSITIONS
    ]


def map_role_list_mouse_entry(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    reset_scroll_count: int = ROLE_LIST_ENTRY_RESET_SCROLLS,
    duration_ms: int = ROLE_LIST_ENTRY_SCROLL_DURATION_MS,
    cloud_nte_mode: bool = False,
) -> dict[str, Any]:
    """Return the selected input-mode sequence that opens the three-column role list.

    The normal route uses mouse input for every action. Cloud NTE mode keeps the
    mouse sidebar reset but uses D-pad-up and RS only for the list control.
    """

    entry_controls = _scale_controls(DEFAULT_ROLE_LIST_ENTRY_CONTROLS, screen_size, content_rect)
    scroll_controls = _scale_controls(DEFAULT_ROLE_LIST_ENTRY_SCROLL, screen_size, content_rect)
    first_role_position = map_role_slots(screen_size, content_rect)[0]
    first_grid_slot = _scale_point(DEFAULT_ROLE_LIST_GRID_SLOT_POSITIONS[0], screen_size, content_rect)
    scroll_start = scroll_controls["role_list_entry_scroll_start"]
    scroll_end = scroll_controls["role_list_entry_scroll_end"]
    scroll_sequence = [
        {
            "name": "role_list_entry_scroll_to_first",
            "from": scroll_start,
            "to": scroll_end,
            "duration_ms": max(1, int(duration_ms)),
            "post_action_pause_seconds": ROLE_LIST_ENTRY_SCROLL_SETTLE_SECONDS,
        }
        for _index in range(max(0, int(reset_scroll_count)))
    ]
    mouse_open_action = {
        "name": "open_role_list_mouse",
        "position": entry_controls["role_list_button"],
        "post_action_pause_seconds": ROLE_LIST_ENTRY_OPEN_SETTLE_SECONDS,
    }
    cloud_open_sequence = [
        {
            "name": "activate_role_list_gamepad",
            "gamepad_button": "dpad_up",
            "post_action_pause_seconds": 0.2,
        },
        {
            "name": "open_role_list",
            "gamepad_button": "rs",
            "post_action_pause_seconds": ROLE_LIST_ENTRY_OPEN_SETTLE_SECONDS,
        },
        {
            "name": "role_list_wake_mouse_after_gamepad",
            "position": first_grid_slot,
            "mouse_move_only": True,
            "post_action_pause_seconds": 0.25,
        },
    ]
    reentry_sequence = (
        [
            {
                "name": "open_role_list_from_current_role_mouse",
                "position": entry_controls["role_list_button"],
                "post_action_pause_seconds": ROLE_LIST_ENTRY_OPEN_SETTLE_SECONDS,
            }
        ]
        if not cloud_nte_mode
        else [
            {
                "name": "activate_role_list_gamepad",
                "gamepad_button": "dpad_up",
                "post_action_pause_seconds": 0.2,
            },
            {
                "name": "open_role_list_from_current_role",
                "gamepad_button": "rs",
                "post_action_pause_seconds": ROLE_LIST_ENTRY_OPEN_SETTLE_SECONDS,
            },
            {
                "name": "role_list_wake_mouse_after_gamepad",
                "position": first_grid_slot,
                "mouse_move_only": True,
                "post_action_pause_seconds": 0.25,
            },
        ]
    )
    return {
        "role_list_entry_scroll_start": scroll_start,
        "role_list_entry_scroll_end": scroll_end,
        "first_role_position": first_role_position,
        "first_grid_slot": first_grid_slot,
        "left_information_tab": entry_controls["left_information_tab"],
        "role_list_button": entry_controls["role_list_button"],
        "close_sequence": [
            {
                "name": "close_role_list_to_role_page",
                "keyboard_key": "esc",
                "post_action_pause_seconds": ROLE_LIST_ENTRY_OPEN_SETTLE_SECONDS,
            }
        ],
        "reentry_sequence": reentry_sequence,
        "entry_sequence": [
            *scroll_sequence,
            {
                "name": "role_list_entry_first_role",
                "position": first_role_position,
                "post_action_pause_seconds": ROLE_LIST_ENTRY_ROLE_SETTLE_SECONDS,
            },
            {
                "name": "role_list_entry_information_tab",
                "position": entry_controls["left_information_tab"],
                "post_action_pause_seconds": ROLE_LIST_ENTRY_INFORMATION_SETTLE_SECONDS,
            },
            *(cloud_open_sequence if cloud_nte_mode else [mouse_open_action]),
        ],
    }


def map_role_list_mouse_row_scan(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    wheel_clicks_per_row: int = ROLE_LIST_WHEEL_CLICKS_PER_ROW,
) -> dict[str, Any]:
    """Map the first 12 cards and one incremental row-scroll operation.

    The visible list is a four-row by three-column grid.  Subsequent scans
    scroll by one row, then inspect only the newly revealed fourth row.
    """

    slot_positions = [
        _scale_point(point, screen_size, content_rect)
        for point in DEFAULT_ROLE_LIST_GRID_SLOT_POSITIONS
    ]
    wheel_position = _scale_point(DEFAULT_ROLE_LIST_WHEEL_POSITION, screen_size, content_rect)
    return {
        "slot_positions": slot_positions,
        "initial_slot_indexes": tuple(range(len(slot_positions))),
        "bottom_row_slot_indexes": tuple(
            range(len(slot_positions) - ROLE_LIST_GRID_COLUMNS, len(slot_positions))
        ),
        "row_scroll_sequence": [
            {
                "name": "role_list_wheel_next_row",
                "position": wheel_position,
                "wheel_clicks": int(wheel_clicks_per_row),
                "wheel_click_interval_seconds": ROLE_LIST_WHEEL_CLICK_INTERVAL_SECONDS,
                "post_action_pause_seconds": ROLE_LIST_WHEEL_SETTLE_SECONDS,
            }
        ],
        "slot_selection_actions": [
            {
                "name": "role_list_select_grid_slot",
                "position": position,
                "post_action_pause_seconds": ROLE_LIST_SELECTION_SETTLE_SECONDS,
            }
            for position in slot_positions
        ],
    }


def map_role_list_mouse_selection(
    roster_index: int,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    wheel_clicks_per_row: int = ROLE_LIST_WHEEL_CLICKS_PER_ROW,
) -> dict[str, Any]:
    """Return row-wise wheel-and-click navigation for a cached roster index.

    Reopening the list always starts at its first 12 entries.  Entries beyond
    that point are selected after one-row wheel increments and use the fourth
    row so a cached index matches the scan traversal exactly.
    """

    index = max(0, int(roster_index))
    scan = map_role_list_mouse_row_scan(screen_size, content_rect, wheel_clicks_per_row)
    first_page_size = len(scan["initial_slot_indexes"])
    if index < first_page_size:
        row_scroll_count = 0
        slot_index = index
    else:
        row_scroll_count = 1 + (index - first_page_size) // ROLE_LIST_GRID_COLUMNS
        slot_index = scan["bottom_row_slot_indexes"][(index - first_page_size) % ROLE_LIST_GRID_COLUMNS]
    wheel_sequence = scan["row_scroll_sequence"] * row_scroll_count
    return {
        "row_scroll_count": row_scroll_count,
        "slot_index": slot_index,
        "wheel_position": scan["row_scroll_sequence"][0]["position"],
        "slot_position": scan["slot_positions"][slot_index],
        "selection_sequence": [
            *wheel_sequence,
            scan["slot_selection_actions"][slot_index],
        ],
    }


def map_role_list_mouse_selection_from_current(
    current_roster_index: int,
    target_roster_index: int,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    wheel_clicks_per_row: int = ROLE_LIST_WHEEL_CLICKS_PER_ROW,
) -> dict[str, Any]:
    """Select a lower roster index from the role list's remembered viewport.

    The scan and descending assembly order leave every index at or after the
    first page's fourth row in the bottom row.  On reopening the list, that
    viewport is retained.  Moving upward only as many rows as required keeps
    the next target visible; once the fourth row is the first-page fourth row,
    earlier targets are clicked directly without further upward scrolling.
    """

    current_index = max(0, int(current_roster_index))
    target_index = max(0, int(target_roster_index))
    scan = map_role_list_mouse_row_scan(screen_size, content_rect, wheel_clicks_per_row)

    def viewport_row(index: int) -> int:
        return max(0, (index - ROLE_LIST_GRID_COLUMNS * 3) // ROLE_LIST_GRID_COLUMNS)

    current_viewport_row = viewport_row(current_index)
    target_viewport_row = viewport_row(target_index)
    upward_rows = max(0, current_viewport_row - target_viewport_row)
    slot_index = target_index - target_viewport_row * ROLE_LIST_GRID_COLUMNS
    slot_index = max(0, min(len(scan["slot_positions"]) - 1, slot_index))
    upward_sequence = [
        {
            "name": "role_list_wheel_previous_row",
            "position": scan["row_scroll_sequence"][0]["position"],
            "wheel_clicks": abs(int(wheel_clicks_per_row)),
            "wheel_click_interval_seconds": ROLE_LIST_WHEEL_CLICK_INTERVAL_SECONDS,
            "post_action_pause_seconds": ROLE_LIST_WHEEL_SETTLE_SECONDS,
        }
        for _row in range(upward_rows)
    ]
    return {
        "current_viewport_row": current_viewport_row,
        "target_viewport_row": target_viewport_row,
        "upward_rows": upward_rows,
        "slot_index": slot_index,
        "slot_position": scan["slot_positions"][slot_index],
        "selection_sequence": [
            *upward_sequence,
            scan["slot_selection_actions"][slot_index],
        ],
    }


def map_role_slot_template_regions(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    half_width: int = 120,
    half_height: int = 120,
) -> list[tuple[int, int, int, int]]:
    """Return per-slot template matching regions around right-side avatars."""

    regions: list[tuple[int, int, int, int]] = []
    for x, y in map_role_slots(screen_size, content_rect):
        regions.append((x - half_width, y - half_height, x + half_width, y + half_height))
    return regions


def map_role_page_scroll(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 700,
) -> dict[str, Any]:
    """Return the swipe action that advances the right-side role list by one page."""

    controls = _scale_controls(DEFAULT_ROLE_PAGE_SCROLL, screen_size, content_rect)
    result: dict[str, Any] = {
        "role_scroll_start": controls["role_scroll_start"],
        "role_scroll_end": controls["role_scroll_end"],
    }
    result["scroll_sequence"] = [
        {
            "name": "role_scroll_next_page",
            "from": result["role_scroll_start"],
            "to": result["role_scroll_end"],
            "duration_ms": duration_ms,
        }
    ]
    return result


def map_role_page_reset(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    repeat_count: int = DEFAULT_ROLE_PAGE_RESET_SCROLLS,
    duration_ms: int = 700,
) -> dict[str, Any]:
    """Return swipes that move the right-side role list back toward the first page."""

    controls = _scale_controls(DEFAULT_ROLE_PAGE_SCROLL, screen_size, content_rect)
    result: dict[str, Any] = {
        "role_scroll_start": controls["role_scroll_end"],
        "role_scroll_end": controls["role_scroll_start"],
        "repeat_count": max(0, int(repeat_count)),
    }
    result["reset_sequence"] = [
        {
            "name": "role_scroll_reset_to_first_page",
            "from": result["role_scroll_start"],
            "to": result["role_scroll_end"],
            "duration_ms": duration_ms,
        }
        for _index in range(result["repeat_count"])
    ]
    return result


def map_current_role_name_region(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    expanded: bool = False,
) -> tuple[int, int, int, int]:
    """Return the top-right current role name OCR region."""

    region = DEFAULT_ROLE_NAME_FALLBACK_REGION if expanded else DEFAULT_ROLE_NAME_REGION
    x1, y1 = _scale_point((region[0], region[1]), screen_size, content_rect)
    x2, y2 = _scale_point((region[2], region[3]), screen_size, content_rect)
    return x1, y1, x2, y2


def map_dpad_role_reset_sequence(repeat_count: int = DEFAULT_DPAD_RESET_UP_COUNT) -> list[dict[str, Any]]:
    """Return gamepad actions that move the role cursor to the first role."""

    return [
        {"name": "role_dpad_reset_to_first", "gamepad_button": "dpad_up"}
        for _index in range(max(0, int(repeat_count)))
    ]


def map_dpad_role_down_sequence(repeat_count: int) -> list[dict[str, Any]]:
    """Return gamepad actions that move down by a role count."""

    return [
        {"name": "role_dpad_next", "gamepad_button": "dpad_down"}
        for _index in range(max(0, int(repeat_count)))
    ]


def map_dpad_role_move_sequence(current_index: int, target_index: int) -> list[dict[str, Any]]:
    """Return D-pad actions that move from one recognized roster index to another."""

    delta = int(target_index) - int(current_index)
    if delta > 0:
        return map_dpad_role_down_sequence(delta)
    if delta < 0:
        return [
            {"name": "role_dpad_previous", "gamepad_button": "dpad_up"}
            for _index in range(abs(delta))
        ]
    return []


def map_role_list_grid_move_sequence(
    current_index: int,
    target_index: int,
    columns: int = ROLE_LIST_GRID_COLUMNS,
) -> list[dict[str, Any]]:
    """Move between roles in the three-column RS character-list grid.

    All directional inputs in the list use the left stick. Adjacent roster
    entries cross a row boundary through left/right exactly as the game does.
    Longer moves use vertical movement first, then stay within the target row
    for horizontal correction.
    """

    width = max(1, int(columns))
    current = max(0, int(current_index))
    target = max(0, int(target_index))
    if target == current:
        return []
    if target == current + 1:
        return [
            {
                "name": "role_list_next",
                "gamepad_stick": "left_right",
                "post_action_pause_seconds": ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
            }
        ]
    if target == current - 1:
        return [
            {
                "name": "role_list_previous",
                "gamepad_stick": "left_left",
                "post_action_pause_seconds": ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
            }
        ]

    current_row, current_col = divmod(current, width)
    target_row, target_col = divmod(target, width)
    sequence: list[dict[str, Any]] = []
    vertical_stick = "left_down" if target_row > current_row else "left_up"
    vertical_name = "role_list_down" if target_row > current_row else "role_list_up"
    sequence.extend(
        {
            "name": vertical_name,
            "gamepad_stick": vertical_stick,
            "post_action_pause_seconds": ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
        }
        for _index in range(abs(target_row - current_row))
    )
    horizontal_input = "left_right" if target_col > current_col else "left_left"
    horizontal_name = "role_list_next" if target_col > current_col else "role_list_previous"
    sequence.extend(
        {
            "name": horizontal_name,
            "gamepad_stick": horizontal_input,
            "post_action_pause_seconds": ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
        }
        for _index in range(abs(target_col - current_col))
    )
    return sequence


def map_role_list_reverse_left_move_sequence(
    current_index: int,
    target_index: int,
) -> list[dict[str, Any]]:
    """Return only left-stick moves for reverse traversal of the RS list.

    The roster scan advances to the right and leaves the cursor at its final
    scanned position.  Planning targets from high to low roster indexes can
    therefore return to every target solely through left input, avoiding the
    unreliable assumption that the sidebar and RS-list orders match.
    """

    current = max(0, int(current_index))
    target = max(0, int(target_index))
    if target > current:
        raise ValueError("reverse role-list traversal cannot move right")
    return [
        {
            "name": "role_list_previous",
            "gamepad_stick": "left_left",
            "post_action_pause_seconds": ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
        }
        for _index in range(current - target)
    ]


def map_role_list_initial_left_reset_sequence(
    repeat_count: int = ROLE_LIST_INITIAL_LEFT_RESET_COUNT,
    pause_seconds: float = 0.15,
) -> list[dict[str, Any]]:
    """Return the quick defensive left pushes used after opening the list once."""

    return [
        {
            "name": "role_list_initial_left_reset",
            "gamepad_stick": "left_left",
            "post_action_pause_seconds": max(0.0, float(pause_seconds)),
        }
        for _index in range(max(0, int(repeat_count)))
    ]


def map_role_list_reset_to_first_sequence() -> list[dict[str, Any]]:
    """Return the one left push that restores a later-page list to its first role."""

    return [
        {
            "name": "role_list_reset_to_first",
            "gamepad_stick": "left_left",
            "post_action_pause_seconds": ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
        }
    ]
