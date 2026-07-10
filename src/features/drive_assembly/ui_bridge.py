# 汇总装配计划并提供配装页面按钮调用的后端入口。
"""UI bridge for drive assembly planning."""

from __future__ import annotations

import time
from typing import Any

import mss
import numpy as np

from src.app import runtime
from src.features.drive_assembly.executor import (
    MouseBackend,
    execute_action_sequence,
    execute_role_traversal_assembly_plan,
)
from src.features.drive_assembly.page_mapping import (
    map_drive_blocks_installation,
    map_page_controls,
    map_tape_equip_first_result,
    map_tape_filter_controls,
    map_tape_filter_refinement,
    map_tape_main_stat_scroll,
    map_tape_main_stat_selection,
    map_tape_set_selection,
    map_tape_sub_stat_filter_entry,
    map_tape_sub_stat_selection,
)
from src.features.drive_assembly.role_flow import (
    build_role_assembly_payloads,
    collect_role_observation_pages,
    map_role_page_scroll,
    plan_role_assembly_from_observations,
    recognize_role_slots_from_image,
    required_roles_from_payloads,
)
from src.scanner.window_capture import capture_foreground_window


def build_single_role_assembly_plan(
    equipped_state: dict[str, Any] | None,
    role_name: str,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return the planned tape and drive actions for one role."""

    payloads = build_role_assembly_payloads(equipped_state, screen_size, content_rect)
    payload = payloads.get(role_name)
    if not payload:
        return {
            "role_name": role_name,
            "available": False,
            "reason": "未找到该角色的已保存装配数据。",
            "actions": [],
        }
    actions: list[dict[str, Any]] = []
    tape_filter = payload.get("tape_filter")
    if tape_filter:
        actions.append(
            {
                "name": "install_tape",
                "role_name": role_name,
                "sequence": _tape_install_sequence(tape_filter, screen_size, content_rect),
            }
        )
    drive_blocks = payload.get("drive_blocks") or []
    if drive_blocks:
        drive_plan = map_drive_blocks_installation(drive_blocks, screen_size, content_rect)
        actions.append(
            {
                "name": "install_drives",
                "role_name": role_name,
                "sequence": drive_plan["assembly_sequence"],
                "install_plans": drive_plan["install_plans"],
            }
        )
    return {
        "role_name": role_name,
        "available": bool(actions),
        "reason": "" if actions else "该角色没有可装配的卡带或驱动块。",
        "tape_count": 1 if tape_filter else 0,
        "drive_count": len(drive_blocks),
        "actions": actions,
    }


def build_all_role_assembly_plan(
    equipped_state: dict[str, Any] | None,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return per-role assembly plans for all roles with saved payloads."""

    payloads = build_role_assembly_payloads(equipped_state, screen_size, content_rect)
    roles = required_roles_from_payloads(payloads)
    role_plans = [
        build_single_role_assembly_plan(equipped_state, role, screen_size, content_rect)
        for role in roles
    ]
    ready = [plan for plan in role_plans if plan["available"]]
    return {
        "role_count": len(roles),
        "ready_count": len(ready),
        "roles": roles,
        "role_plans": role_plans,
        "missing_roles": [plan["role_name"] for plan in role_plans if not plan["available"]],
    }


def build_role_traversal_assembly_plan(
    equipped_state: dict[str, Any] | None,
    observed_pages: list[list[Any]],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Build traversal and assembly plans from recognized role-list pages."""

    assembly_plan = build_all_role_assembly_plan(equipped_state, screen_size, content_rect)
    traversal_plan = plan_role_assembly_from_observations(
        assembly_plan.get("roles", []),
        observed_pages,
        screen_size=screen_size,
        content_rect=content_rect,
    )
    return {
        "assembly_plan": assembly_plan,
        "traversal_plan": traversal_plan,
        "ready_count": assembly_plan.get("ready_count", 0),
        "role_count": assembly_plan.get("role_count", 0),
        "missing_roles": traversal_plan.get("missing_roles", []),
        "duplicates": traversal_plan.get("duplicates", []),
        "unrecognized": traversal_plan.get("unrecognized", []),
    }


def execute_all_roles_from_current_game_page(
    equipped_state: dict[str, Any] | None,
    backend: MouseBackend | None = None,
    template_dir: str | None = None,
    max_pages: int | None = None,
    startup_delay_seconds: float = 3.0,
):
    """Recognize the current game role list, traverse roles, and execute assembly."""

    template_root = template_dir or str(runtime.CONFIG_DIR / "templates" / "roles")
    if startup_delay_seconds > 0:
        time.sleep(startup_delay_seconds)
    first_image, first_rect = _capture_foreground_client_image()
    screen_size = (first_rect.width, first_rect.height)
    action_rect = (first_rect.left, first_rect.top, first_rect.width, first_rect.height)
    assembly_plan = build_all_role_assembly_plan(equipped_state, screen_size=screen_size, content_rect=action_rect)
    required_roles = assembly_plan.get("roles", [])
    if not required_roles:
        return execute_role_traversal_assembly_plan({"plans": []}, assembly_plan, backend=backend)

    cached_images = {0: first_image}

    def observe_page(page_index: int):
        image = cached_images.pop(page_index, None)
        if image is None:
            image, _rect = _capture_foreground_client_image()
        return recognize_role_slots_from_image(
            image,
            required_roles,
            template_root,
            screen_size=screen_size,
        )

    def scroll_next(_page_index: int):
        scroll_sequence = map_role_page_scroll(screen_size=screen_size, content_rect=action_rect)["scroll_sequence"]
        execute_action_sequence(scroll_sequence, backend=backend)

    observed_pages = collect_role_observation_pages(
        required_roles,
        page_observer=observe_page,
        scroll_next_page=scroll_next,
        max_pages=max_pages,
    )
    traversal_plan = plan_role_assembly_from_observations(
        required_roles,
        observed_pages,
        screen_size=screen_size,
        content_rect=action_rect,
    )
    return execute_role_traversal_assembly_plan(traversal_plan, assembly_plan, backend=backend)


def summarize_assembly_plan(plan: dict[str, Any]) -> str:
    """Return a concise human-readable plan summary."""

    if "role_plans" in plan:
        lines = [f"可装配角色：{plan.get('ready_count', 0)}/{plan.get('role_count', 0)}"]
        for role_plan in plan.get("role_plans", []):
            if role_plan.get("available"):
                lines.append(
                    f"- {role_plan['role_name']}：卡带 {role_plan.get('tape_count', 0)}，驱动 {role_plan.get('drive_count', 0)}"
                )
            else:
                lines.append(f"- {role_plan['role_name']}：{role_plan.get('reason', '不可装配')}")
        return "\n".join(lines)
    if not plan.get("available"):
        return f"{plan.get('role_name', '角色')}：{plan.get('reason', '不可装配')}"
    return f"{plan['role_name']}：卡带 {plan.get('tape_count', 0)}，驱动 {plan.get('drive_count', 0)}"


def _capture_foreground_client_image():
    with mss.MSS() as sct:
        screenshot, rect = capture_foreground_window(sct)
    image = np.array(screenshot)
    if image.ndim == 3 and image.shape[2] > 3:
        image = image[:, :, :3]
    return image, rect


def _tape_install_sequence(
    tape_filter: dict[str, Any],
    screen_size: tuple[int, int] | None,
    content_rect: tuple[int, int, int, int] | None,
) -> list[dict[str, Any]]:
    sequence: list[dict[str, Any]] = []
    sequence.extend(map_page_controls(screen_size, content_rect)["click_sequence"])
    sequence.extend(map_tape_filter_controls(screen_size, content_rect)["set_filter_sequence"])
    sequence.extend(map_tape_set_selection(tape_filter["set_name"], screen_size, content_rect)["selection_sequence"])
    sequence.extend(map_tape_filter_refinement([tape_filter.get("quality", "Gold")], screen_size, content_rect)["refinement_sequence"])
    sequence.extend(map_tape_main_stat_scroll(screen_size, content_rect)["scroll_sequence"])
    sequence.extend(map_tape_main_stat_selection(tape_filter["main_stat"], screen_size, content_rect)["selection_sequence"])
    sequence.extend(map_tape_sub_stat_filter_entry(screen_size, content_rect)["entry_sequence"])
    sequence.extend(map_tape_sub_stat_selection(tape_filter.get("sub_stats", []), screen_size, content_rect)["selection_sequence"])
    sequence.extend(map_tape_equip_first_result(screen_size, content_rect)["equip_sequence"])
    return sequence
