# 管理角色识别、右侧角色列表遍历和逐角色装配计划。
"""Role recognition and traversal planning for drive assembly."""

from __future__ import annotations

from typing import Any, Callable

from src.domain.drive_layout import (
    extract_drive_blocks_from_state,
    extract_tape_filters_from_state,
)
from src.features.drive_assembly.page_mapping import map_blocks_to_page
from src.features.drive_assembly.role_contracts import RoleRecognition

__all__ = [
    "RoleRecognition",
    "build_role_assembly_payloads",
    "collect_role_observation_pages",
    "collect_role_roster_from_role_list",
    "collect_role_roster_until_repeat",
    "collect_role_roster_with_dpad",
    "map_current_role_name_region",
    "map_dpad_role_down_sequence",
    "map_dpad_role_move_sequence",
    "map_dpad_role_reset_sequence",
    "map_role_list_grid_move_sequence",
    "map_role_list_initial_left_reset_sequence",
    "map_role_list_reset_to_first_sequence",
    "map_role_list_reverse_left_move_sequence",
    "map_role_navigation_controls",
    "map_role_page_reset",
    "map_role_page_scroll",
    "map_role_slot_template_regions",
    "map_role_slots",
    "match_role_template",
    "plan_role_assembly_from_dpad_roster",
    "plan_role_assembly_from_observations",
    "plan_role_assembly_from_role_list_roster",
    "plan_role_assembly_from_roster",
    "recognize_current_role_from_image",
    "recognize_role_slots_from_image",
    "required_roles_from_payloads",
    "resolve_role_recognition",
]

from src.features.drive_assembly.role_flow_helpers import (
    DEFAULT_DPAD_BOTTOM_REPEAT_LIMIT,
    DEFAULT_DPAD_RESET_UP_COUNT,
    DEFAULT_DPAD_ROLE_LIMIT,
    DEFAULT_ROLE_PAGE_RESET_SCROLLS,
    DEFAULT_ROLE_ROSTER_MAX_PAGES,
    ROLE_LIST_FIRST_PAGE_SIZE,
    ROLE_LIST_INITIAL_LEFT_RESET_COUNT,
    _coerce_recognition,
    _recognition_stability_key,
)

from src.features.drive_assembly.role_navigation_mapping import (
    map_role_navigation_controls,
    map_role_slots,
    map_role_slot_template_regions,
    map_role_page_scroll,
    map_role_page_reset,
    map_current_role_name_region,
    map_dpad_role_reset_sequence,
    map_dpad_role_down_sequence,
    map_dpad_role_move_sequence,
    map_role_list_grid_move_sequence,
    map_role_list_reverse_left_move_sequence,
    map_role_list_initial_left_reset_sequence,
    map_role_list_reset_to_first_sequence,
)

from src.features.drive_assembly.role_recognition import (
    resolve_role_recognition,
    match_role_template,
    recognize_role_slots_from_image,
    recognize_current_role_from_image,
)


def plan_role_assembly_from_observations(
    required_roles: list[str] | tuple[str, ...],
    observed_pages: list[list[RoleRecognition | str | None]],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    reset_to_first_page: bool = False,
    reset_scroll_count: int = DEFAULT_ROLE_PAGE_RESET_SCROLLS,
) -> dict[str, Any]:
    """Build a de-duplicated per-role assembly plan from visible-page observations."""

    required = [str(role) for role in required_roles if str(role).strip()]
    required_set = set(required)
    slots = map_role_slots(screen_size, content_rect)
    entry = map_role_navigation_controls(screen_size, content_rect)
    scroll = map_role_page_scroll(screen_size, content_rect)
    reset = map_role_page_reset(screen_size, content_rect, repeat_count=reset_scroll_count)
    seen: set[str] = set()
    plans: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    unrecognized: list[dict[str, Any]] = []

    if reset_to_first_page and observed_pages:
        plans.append(
            {
                "role_name": None,
                "page_index": -1,
                "action_sequence": reset["reset_sequence"],
            }
        )

    for page_index, page in enumerate(observed_pages):
        for slot_index, observed in enumerate(page[: len(slots)]):
            recognition = _coerce_recognition(observed)
            slot_position = slots[slot_index]
            if not recognition.role_name:
                unrecognized.append({"page_index": page_index, "slot_index": slot_index, "position": slot_position})
                continue
            role_name = recognition.role_name
            if role_name in seen:
                duplicates.append({"role_name": role_name, "page_index": page_index, "slot_index": slot_index})
                continue
            seen.add(role_name)
            if role_name not in required_set:
                continue
            action_sequence = [
                {"name": "role_slot", "role_name": role_name, "position": slot_position},
                *entry["entry_sequence"],
                {"name": "assemble_current_role_from_blueprint", "role_name": role_name},
            ]
            plans.append(
                {
                    "role_name": role_name,
                    "page_index": page_index,
                    "slot_index": slot_index,
                    "flow": "find_role_then_assemble_blueprint",
                    "recognition": {
                        "method": recognition.method,
                        "confidence": recognition.confidence,
                        "raw_text": recognition.raw_text,
                    },
                    "action_sequence": action_sequence,
                }
            )
        if page_index < len(observed_pages) - 1:
            plans.append(
                {
                    "role_name": None,
                    "page_index": page_index,
                    "action_sequence": scroll["scroll_sequence"],
                }
            )

    planned_roles = [plan["role_name"] for plan in plans if plan.get("role_name")]
    missing = [role for role in required if role not in set(planned_roles)]
    return {
        "required_roles": required,
        "planned_roles": planned_roles,
        "missing_roles": missing,
        "duplicates": duplicates,
        "unrecognized": unrecognized,
        "plans": plans,
        "complete": not missing and not unrecognized,
    }


def collect_role_roster_until_repeat(
    expected_roles: list[str] | tuple[str, ...],
    page_observer: Callable[[int], list[RoleRecognition]],
    scroll_next_page: Callable[[int], None] | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Scan the role sidebar until a post-scroll page repeats a known role."""

    page_limit = max_pages if max_pages is not None else DEFAULT_ROLE_ROSTER_MAX_PAGES
    observed_pages: list[list[RoleRecognition]] = []
    role_order: list[str] = []
    seen: set[str] = set()
    duplicates: list[dict[str, Any]] = []
    unrecognized: list[dict[str, Any]] = []
    reached_bottom = False
    bottom_page_index = 0

    for page_index in range(max(1, page_limit)):
        page = page_observer(page_index)
        observed_pages.append(page)
        page_repeated = False
        for slot_index, observed in enumerate(page[:5]):
            recognition = _coerce_recognition(observed)
            if not recognition.role_name:
                unrecognized.append({"page_index": page_index, "slot_index": slot_index})
                continue
            role_name = recognition.role_name
            if role_name in seen:
                duplicates.append({"role_name": role_name, "page_index": page_index, "slot_index": slot_index})
                page_repeated = True
                continue
            seen.add(role_name)
            role_order.append(role_name)
        bottom_page_index = page_index
        if page_index > 0 and page_repeated:
            reached_bottom = True
            break
        if page_index < page_limit - 1 and scroll_next_page is not None:
            scroll_next_page(page_index)

    expected = [str(role) for role in expected_roles if str(role).strip()]
    return {
        "roles": role_order,
        "observed_pages": observed_pages,
        "duplicates": duplicates,
        "unrecognized": unrecognized,
        "missing_expected_roles": [role for role in expected if role not in seen],
        "bottom_page_index": bottom_page_index,
        "reached_bottom": reached_bottom,
    }


def collect_role_roster_with_dpad(
    expected_roles: list[str] | tuple[str, ...],
    current_observer: Callable[[int], RoleRecognition],
    press_up: Callable[[], None],
    press_down: Callable[[], None],
    reset_up_count: int = DEFAULT_DPAD_RESET_UP_COUNT,
    bottom_repeat_limit: int = DEFAULT_DPAD_BOTTOM_REPEAT_LIMIT,
    max_roles: int = DEFAULT_DPAD_ROLE_LIMIT,
) -> dict[str, Any]:
    """Scan roles by D-pad navigation until repeated down presses no longer change the role."""

    for _index in range(max(0, int(reset_up_count))):
        press_up()

    roles: list[str] = []
    observations: list[RoleRecognition] = []
    seen: set[str] = set()
    duplicates: list[dict[str, Any]] = []
    unrecognized: list[dict[str, Any]] = []
    unchanged_count = 0
    previous_key: str | None = None
    reached_bottom = False
    cursor_index = 0
    role_positions: dict[str, int] = {}

    for index in range(max(1, int(max_roles))):
        recognition = _coerce_recognition(current_observer(index))
        key = _recognition_stability_key(recognition)
        is_unchanged = bool(previous_key and key and key == previous_key)
        if index > 0 and is_unchanged:
            unchanged_count += 1
            if unchanged_count >= max(1, int(bottom_repeat_limit)):
                reached_bottom = True
                break
        else:
            if index > 0:
                cursor_index += 1
            unchanged_count = 0
            if not recognition.role_name:
                unrecognized.append({"roster_index": cursor_index, "raw_text": recognition.raw_text})
            elif recognition.role_name in seen:
                duplicates.append({"role_name": recognition.role_name, "roster_index": cursor_index})
            else:
                seen.add(recognition.role_name)
                role_positions[recognition.role_name] = cursor_index
                roles.append(recognition.role_name)
                observations.append(recognition)
        previous_key = key or previous_key
        if index < max_roles - 1:
            press_down()

    expected = [str(role) for role in expected_roles if str(role).strip()]
    return {
        "roles": roles,
        "role_positions": role_positions,
        "current_index": cursor_index,
        "observations": observations,
        "duplicates": duplicates,
        "unrecognized": unrecognized,
        "missing_expected_roles": [role for role in expected if role not in seen],
        "reached_bottom": reached_bottom,
        "navigation": "dpad_current_role",
        "reset_up_count": max(0, int(reset_up_count)),
        "bottom_repeat_limit": max(1, int(bottom_repeat_limit)),
    }


def collect_role_roster_from_role_list(
    expected_roles: list[str] | tuple[str, ...],
    current_observer: Callable[[int], RoleRecognition],
    press_up: Callable[[], None],
    open_role_list: Callable[[], None],
    confirm_selection: Callable[[], None],
    move_right: Callable[[], None],
    move_left: Callable[[], None] | None = None,
    reset_up_count: int = DEFAULT_DPAD_RESET_UP_COUNT,
    bottom_repeat_limit: int = DEFAULT_DPAD_BOTTOM_REPEAT_LIMIT,
    max_roles: int = DEFAULT_DPAD_ROLE_LIMIT,
    initial_left_reset_count: int = ROLE_LIST_INITIAL_LEFT_RESET_COUNT,
) -> dict[str, Any]:
    """Scan the RS three-column role list and stop as soon as targets are found.

    ``A`` refreshes the current character while leaving the list open, so OCR
    observes each grid position without relying on the unrelated sidebar order.
    """

    for _index in range(max(0, int(reset_up_count))):
        press_up()
    open_role_list()
    if move_left is not None:
        for _index in range(max(0, int(initial_left_reset_count))):
            move_left()

    expected_order = [str(role).strip() for role in expected_roles if str(role).strip()]
    expected = set(expected_order)
    roles: list[str] = []
    role_positions: dict[str, int] = {}
    observations: list[RoleRecognition] = []
    seen: set[str] = set()
    duplicates: list[dict[str, Any]] = []
    unrecognized: list[dict[str, Any]] = []
    previous_key = ""
    unchanged_count = 0
    current_index = 0
    stop_reason = "max_roles_reached"

    for list_index in range(max(1, int(max_roles))):
        confirm_selection()
        recognition = _coerce_recognition(current_observer(list_index))
        key = _recognition_stability_key(recognition) or "<unrecognized>"
        if list_index > 0 and key == previous_key:
            unchanged_count += 1
            if unchanged_count >= max(1, int(bottom_repeat_limit)):
                stop_reason = "role_list_end_reached"
                break
        else:
            unchanged_count = 0
            current_index = list_index
            if not recognition.role_name:
                unrecognized.append({"roster_index": list_index, "raw_text": recognition.raw_text})
            elif recognition.role_name in seen:
                duplicates.append({"role_name": recognition.role_name, "roster_index": list_index})
            else:
                seen.add(recognition.role_name)
                role_positions[recognition.role_name] = list_index
                roles.append(recognition.role_name)
                observations.append(recognition)

            if expected and expected.issubset(seen):
                stop_reason = "all_required_roles_found"
                break
        previous_key = key
        if list_index < max(1, int(max_roles)) - 1:
            move_right()

    return {
        "roles": roles,
        "role_positions": role_positions,
        "current_index": current_index,
        "observations": observations,
        "duplicates": duplicates,
        "unrecognized": unrecognized,
        "missing_expected_roles": [role for role in expected_order if role not in seen],
        "reached_bottom": stop_reason == "role_list_end_reached",
        "stop_reason": stop_reason,
        "list_open": True,
        "navigation": "rs_role_list_scan",
        "reset_up_count": max(0, int(reset_up_count)),
        "initial_left_reset_count": max(0, int(initial_left_reset_count)),
    }


def plan_role_assembly_from_roster(
    required_roles: list[str] | tuple[str, ...],
    role_roster: dict[str, Any],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    reset_scroll_count: int = DEFAULT_ROLE_PAGE_RESET_SCROLLS,
) -> dict[str, Any]:
    """Build role assembly navigation from a cached full sidebar roster."""

    required = [str(role) for role in required_roles if str(role).strip()]
    slots = map_role_slots(screen_size, content_rect)
    entry = map_role_navigation_controls(screen_size, content_rect)
    scroll = map_role_page_scroll(screen_size, content_rect)["scroll_sequence"]
    reset = map_role_page_reset(screen_size, content_rect, repeat_count=reset_scroll_count)["reset_sequence"]
    roster = [str(role) for role in role_roster.get("roles", []) if str(role).strip()]
    role_indexes = {role: index for index, role in enumerate(roster)}
    bottom_scroll_count = int(role_roster.get("bottom_page_index", max(0, (len(roster) - 1) // 5)) or 0)
    tail_count = len(roster) % len(slots) if slots else 0
    tail_start = len(roster) - tail_count if tail_count else len(roster)
    plans: list[dict[str, Any]] = []

    for role_name in required:
        index = role_indexes.get(role_name)
        if index is None:
            continue
        use_bottom_anchor = bool(tail_count and index >= tail_start)
        if use_bottom_anchor:
            from_bottom = len(roster) - index
            slot_index = max(0, min(len(slots) - 1, len(slots) - from_bottom))
            page_index = "bottom"
            scroll_count = bottom_scroll_count
        else:
            slot_index = index % len(slots)
            page_index = index // len(slots)
            scroll_count = int(page_index)
        action_sequence = [
            *reset,
            *(scroll * scroll_count),
            {"name": "role_slot", "role_name": role_name, "position": slots[slot_index]},
            *entry["entry_sequence"],
            {"name": "assemble_current_role_from_blueprint", "role_name": role_name},
        ]
        plans.append(
            {
                "role_name": role_name,
                "page_index": page_index,
                "slot_index": slot_index,
                "roster_index": index,
                "positioning": "bottom_tail" if use_bottom_anchor else "page_slot",
                "flow": "find_role_then_assemble_blueprint",
                "action_sequence": action_sequence,
            }
        )

    planned_roles = [plan["role_name"] for plan in plans]
    missing = [role for role in required if role not in set(planned_roles)]
    return {
        "required_roles": required,
        "planned_roles": planned_roles,
        "missing_roles": missing,
        "duplicates": list(role_roster.get("duplicates", []) or []),
        "unrecognized": list(role_roster.get("unrecognized", []) or []),
        "role_roster": roster,
        "plans": plans,
        "complete": not missing and not role_roster.get("unrecognized"),
    }


def plan_role_assembly_from_dpad_roster(
    required_roles: list[str] | tuple[str, ...],
    role_roster: dict[str, Any],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    reset_up_count: int = DEFAULT_DPAD_RESET_UP_COUNT,
    current_index: int | None = None,
) -> dict[str, Any]:
    """Build assembly navigation from the D-pad current-role roster."""

    required = [str(role) for role in required_roles if str(role).strip()]
    entry = map_role_navigation_controls(screen_size, content_rect)
    roster = [str(role) for role in role_roster.get("roles", []) if str(role).strip()]
    role_positions = {
        str(role): int(index)
        for role, index in (role_roster.get("role_positions", {}) or {}).items()
        if str(role).strip()
    }
    role_indexes = role_positions or {role: index for index, role in enumerate(roster)}
    ordered_required = sorted(
        (role for role in required if role in role_indexes),
        key=lambda role: role_indexes[role],
    )
    cursor_index = (
        int(current_index)
        if current_index is not None
        else int(role_roster.get("current_index", max(0, len(roster) - 1)) or 0)
    )
    plans: list[dict[str, Any]] = []

    for plan_index, role_name in enumerate(ordered_required):
        index = role_indexes[role_name]
        if plan_index == 0:
            move_sequence = map_dpad_role_move_sequence(cursor_index, index)
            action_sequence = [
                *move_sequence,
                *entry["entry_sequence"],
                {"name": "assemble_current_role_from_blueprint", "role_name": role_name},
                *entry["exit_sequence"],
            ]
            navigation = "sidebar_dpad"
        else:
            move_sequence = map_role_list_grid_move_sequence(cursor_index, index)
            action_sequence = [
                {"name": "open_role_list", "gamepad_button": "rs"},
                *move_sequence,
                {"name": "confirm_role_list_selection", "gamepad_button": "a"},
                {"name": "close_role_list_after_confirmation", "gamepad_button": "b"},
                *entry["entry_sequence"],
                {"name": "assemble_current_role_from_blueprint", "role_name": role_name},
                *entry["exit_sequence"],
            ]
            navigation = "rs_role_list_grid"
        plans.append(
            {
                "role_name": role_name,
                "roster_index": index,
                "start_roster_index": cursor_index,
                "navigation": navigation,
                "flow": "find_role_then_assemble_blueprint",
                "action_sequence": action_sequence,
            }
        )
        cursor_index = index

    planned_roles = [plan["role_name"] for plan in plans]
    missing = [role for role in required if role not in set(planned_roles)]
    return {
        "required_roles": required,
        "planned_roles": planned_roles,
        "missing_roles": missing,
        "duplicates": list(role_roster.get("duplicates", []) or []),
        "unrecognized": list(role_roster.get("unrecognized", []) or []),
        "role_roster": roster,
        "plans": plans,
        "complete": not missing and not role_roster.get("unrecognized"),
        "navigation": "sidebar_then_rs_role_list_grid",
    }


def plan_role_assembly_from_role_list_roster(
    required_roles: list[str] | tuple[str, ...],
    role_roster: dict[str, Any],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    current_index: int | None = None,
) -> dict[str, Any]:
    """Plan assembly entirely through the RS three-column character list.

    The initial roster scan leaves the list open. The first target therefore
    only needs grid movement, ``A`` confirmation and ``B`` to close the list;
    every later target reopens the list with ``RS`` before following the same
    route. Targets are visited in reverse list order, using only left-stick
    movement so selection never depends on the unrelated sidebar order.
    """

    required = [str(role) for role in required_roles if str(role).strip()]
    entry = map_role_navigation_controls(screen_size, content_rect)
    roster = [str(role) for role in role_roster.get("roles", []) if str(role).strip()]
    role_positions = {
        str(role): int(index)
        for role, index in (role_roster.get("role_positions", {}) or {}).items()
        if str(role).strip()
    }
    role_indexes = role_positions or {role: index for index, role in enumerate(roster)}
    ordered_required = sorted(
        (role for role in required if role in role_indexes),
        key=lambda role: role_indexes[role],
        reverse=True,
    )
    cursor_index = (
        int(current_index)
        if current_index is not None
        else int(role_roster.get("current_index", max(0, len(roster) - 1)) or 0)
    )
    list_is_open = bool(role_roster.get("list_open", True))
    first_target_page = (
        role_indexes[ordered_required[0]] // ROLE_LIST_FIRST_PAGE_SIZE
        if ordered_required else 0
    )
    # 从第二页起打开角色列表没有稳定光标。先通过“左推复位 + 网格”处理，
    # 直到实际装配过一个第一页角色；之后第一页光标可用，继续倒序左移即可。
    first_page_cursor_established = first_target_page == 0
    plans: list[dict[str, Any]] = []

    for plan_index, role_name in enumerate(ordered_required):
        index = role_indexes[role_name]
        starts_in_open_list = plan_index == 0 and list_is_open
        reset_to_first = not starts_in_open_list and not first_page_cursor_established
        if reset_to_first:
            move_sequence = [
                *map_role_list_reset_to_first_sequence(),
                *map_role_list_grid_move_sequence(0, index),
            ]
        else:
            move_sequence = map_role_list_reverse_left_move_sequence(cursor_index, index)
        action_sequence: list[dict[str, Any]] = []
        if not starts_in_open_list:
            action_sequence.append({"name": "open_role_list", "gamepad_button": "rs"})
        action_sequence.extend(
            [
                *move_sequence,
                {"name": "confirm_role_list_selection", "gamepad_button": "a"},
                {"name": "close_role_list_after_confirmation", "gamepad_button": "b"},
                *entry["entry_sequence"],
                {"name": "assemble_current_role_from_blueprint", "role_name": role_name},
                *entry["exit_sequence"],
            ]
        )
        plans.append(
            {
                "role_name": role_name,
                "roster_index": index,
                "target_page": index // ROLE_LIST_FIRST_PAGE_SIZE,
                "start_roster_index": cursor_index,
                "navigation": (
                    "role_list_reverse_left_from_open"
                    if starts_in_open_list
                    else "rs_role_list_reset_then_grid"
                    if reset_to_first
                    else "rs_role_list_reverse_left"
                ),
                "flow": "find_role_then_assemble_blueprint",
                "action_sequence": action_sequence,
            }
        )
        if index < ROLE_LIST_FIRST_PAGE_SIZE:
            first_page_cursor_established = True
        cursor_index = index

    planned_roles = [plan["role_name"] for plan in plans]
    missing = [role for role in required if role not in set(planned_roles)]
    return {
        "required_roles": required,
        "planned_roles": planned_roles,
        "missing_roles": missing,
        "duplicates": list(role_roster.get("duplicates", []) or []),
        "unrecognized": list(role_roster.get("unrecognized", []) or []),
        "role_roster": roster,
        "plans": plans,
        "complete": not missing and not role_roster.get("unrecognized"),
        "navigation": "rs_role_list_scan_then_reverse_left",
        "scan_stop_reason": role_roster.get("stop_reason", ""),
        "first_target_page": first_target_page,
        "reset_until_first_page_target": first_target_page > 0,
    }


def collect_role_observation_pages(
    required_roles: list[str] | tuple[str, ...],
    page_observer: Callable[[int], list[RoleRecognition]],
    scroll_next_page: Callable[[int], None] | None = None,
    max_pages: int | None = None,
    stop_when_all_seen: bool = True,
) -> list[list[RoleRecognition]]:
    """Observe visible role pages until all required roles are seen or the page limit is reached."""

    required = {str(role) for role in required_roles if str(role).strip()}
    page_limit = max_pages if max_pages is not None else max(1, (len(required) + 4) // 5 + 1)
    observed_pages: list[list[RoleRecognition]] = []
    seen: set[str] = set()
    for page_index in range(max(1, page_limit)):
        page = page_observer(page_index)
        observed_pages.append(page)
        for recognition in page:
            if recognition.role_name:
                seen.add(recognition.role_name)
        if stop_when_all_seen and required and required.issubset(seen):
            break
        if page_index < page_limit - 1 and scroll_next_page is not None:
            scroll_next_page(page_index)
    return observed_pages


def build_role_assembly_payloads(
    equipped_state: dict[str, Any] | None,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return role-keyed assembly data for recognized roles."""

    drive_blocks = map_blocks_to_page(
        extract_drive_blocks_from_state(equipped_state),
        screen_size=screen_size,
        content_rect=content_rect,
    )
    tape_filters = extract_tape_filters_from_state(equipped_state)
    payloads: dict[str, dict[str, Any]] = {}
    for block in drive_blocks:
        role_name = str(block.get("blueprint_role_name") or block.get("role_name") or "")
        if not role_name:
            continue
        payloads.setdefault(role_name, {"drive_blocks": [], "tape_filter": None})["drive_blocks"].append(block)
    for tape_filter in tape_filters:
        role_name = str(tape_filter.get("blueprint_role_name") or tape_filter.get("role_name") or "")
        if not role_name:
            continue
        payloads.setdefault(role_name, {"drive_blocks": [], "tape_filter": None})["tape_filter"] = tape_filter
    return payloads


def required_roles_from_payloads(payloads: dict[str, dict[str, Any]]) -> list[str]:
    """Return roles that have at least one assembly item."""

    return [
        role_name
        for role_name, payload in payloads.items()
        if payload.get("drive_blocks") or payload.get("tape_filter")
    ]
