# 映射自动装配角色列表的鼠标、手柄和滚动导航动作。
"""Role recognition and traversal planning for drive assembly."""

from __future__ import annotations

from typing import Any


from src.features.drive_assembly.role_flow_helpers import (
    _scale_controls,
    _scale_point,
)


from src.features.drive_assembly.role_flow_helpers import (
    DEFAULT_DPAD_RESET_UP_COUNT,
    DEFAULT_ROLE_NAME_FALLBACK_REGION,
    DEFAULT_ROLE_NAME_REGION,
    DEFAULT_ROLE_NAVIGATION_CONTROLS,
    DEFAULT_ROLE_PAGE_RESET_SCROLLS,
    DEFAULT_ROLE_PAGE_SCROLL,
    DEFAULT_ROLE_SLOT_POSITIONS,
    ROLE_ASSEMBLE_PAGE_SETTLE_SECONDS,
    ROLE_KONGMU_TAB_SETTLE_SECONDS,
    ROLE_LIST_GRID_COLUMNS,
    ROLE_LIST_INITIAL_LEFT_RESET_COUNT,
    ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
)



def map_role_navigation_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return controls for entering the assembly page from a role page."""

    controls = _scale_controls(DEFAULT_ROLE_NAVIGATION_CONTROLS, screen_size, content_rect)
    controls["assemble_sequence"] = [
        {"name": "assemble_button", "position": controls["assemble_button"]},
        {"name": "wait_after_assemble_button", "wait_seconds": ROLE_ASSEMBLE_PAGE_SETTLE_SECONDS},
    ]
    controls["entry_sequence"] = [
        {"name": "left_kongmu_tab", "position": controls["left_kongmu_tab"]},
        {"name": "wait_after_left_kongmu_tab", "wait_seconds": ROLE_KONGMU_TAB_SETTLE_SECONDS},
        *controls["assemble_sequence"],
    ]
    controls["exit_sequence"] = [
        {
            "name": "assembly_back_to_role_page",
            "gamepad_button": "b",
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
