# 汇总装配计划并提供配装页面按钮调用的后端入口。
"""UI bridge for drive assembly planning."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

import mss
import numpy as np

from src.app import runtime
from src.features.drive_assembly.executor import (
    MouseBackend,
    PyAutoGuiMouseBackend,
    execute_action_sequence,
    execute_role_traversal_assembly_plan,
    f12_stop_checker,
)
from src.features.drive_assembly.page_mapping import (
    map_assembly_page_prepare_controls,
    map_drive_blocks_installation,
    map_page_controls,
    map_tape_equip_first_result,
    map_tape_filter_controls,
    map_tape_filter_refinement,
    map_tape_main_stat_gamepad_open,
    map_tape_main_stat_selection,
    map_tape_set_selection,
    map_tape_sub_stat_filter_entry,
    map_tape_sub_stat_selection,
)
from src.features.drive_assembly.role_flow import (
    build_role_assembly_payloads,
    collect_role_roster_with_dpad,
    collect_role_roster_until_repeat,
    map_role_page_reset,
    map_role_page_scroll,
    plan_role_assembly_from_dpad_roster,
    plan_role_assembly_from_observations,
    plan_role_assembly_from_roster,
    recognize_current_role_from_image,
    recognize_role_slots_from_image,
    required_roles_from_payloads,
)
from src.scanner.ocr_engine import OCREngine
from src.scanner.window_capture import capture_foreground_window
from src.utils.logger import logger


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
    drive_blocks = payload.get("drive_blocks") or []
    if tape_filter or drive_blocks:
        actions.append(
            {
                "name": "prepare_assembly_page",
                "role_name": role_name,
                "sequence": map_assembly_page_prepare_controls(screen_size, content_rect)["prepare_sequence"],
            }
        )
    if tape_filter:
        actions.append(
            {
                "name": "install_tape",
                "role_name": role_name,
                "sequence": _tape_install_sequence(tape_filter, screen_size, content_rect),
            }
        )
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
        "drive_blocks": drive_blocks,
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
    reset_scroll_count: int = 6,
    verification_enabled: bool = True,
    role_name_aliases: dict[str, str] | None = None,
):
    """Recognize the current game role list, traverse roles, and execute assembly."""

    return _execute_roles_from_current_game_page(
        equipped_state,
        target_roles=None,
        backend=backend,
        template_dir=template_dir,
        max_pages=max_pages,
        startup_delay_seconds=startup_delay_seconds,
        reset_scroll_count=reset_scroll_count,
        verification_enabled=verification_enabled,
        role_name_aliases=role_name_aliases,
    )


def execute_selected_role_from_current_game_page(
    equipped_state: dict[str, Any] | None,
    role_name: str,
    backend: MouseBackend | None = None,
    template_dir: str | None = None,
    max_pages: int | None = None,
    startup_delay_seconds: float = 3.0,
    reset_scroll_count: int = 6,
    verification_enabled: bool = True,
    role_name_aliases: dict[str, str] | None = None,
):
    """Find one selected role in the game sidebar and assemble only its blueprint."""

    return _execute_roles_from_current_game_page(
        equipped_state,
        target_roles=[role_name],
        backend=backend,
        template_dir=template_dir,
        max_pages=max_pages,
        startup_delay_seconds=startup_delay_seconds,
        reset_scroll_count=reset_scroll_count,
        verification_enabled=verification_enabled,
        role_name_aliases=role_name_aliases,
    )


def _execute_roles_from_current_game_page(
    equipped_state: dict[str, Any] | None,
    target_roles: list[str] | tuple[str, ...] | None,
    backend: MouseBackend | None,
    template_dir: str | None,
    max_pages: int | None,
    startup_delay_seconds: float,
    reset_scroll_count: int,
    verification_enabled: bool,
    role_name_aliases: dict[str, str] | None = None,
):
    """Shared game-page flow: find target roles first, then assemble their blueprints."""

    template_root = template_dir or str(runtime.CONFIG_DIR / "templates" / "roles")
    if startup_delay_seconds > 0:
        time.sleep(startup_delay_seconds)
    first_image, first_rect = _capture_foreground_client_image()
    screen_size = (first_rect.width, first_rect.height)
    image_content_rect = _fit_content_rect(first_rect.width, first_rect.height)
    action_rect = (
        first_rect.left + image_content_rect[0],
        first_rect.top + image_content_rect[1],
        image_content_rect[2],
        image_content_rect[3],
    )
    assembly_plan = build_all_role_assembly_plan(equipped_state, screen_size=screen_size, content_rect=action_rect)
    assembly_plan = _filter_assembly_plan_for_roles(assembly_plan, target_roles)
    required_roles = assembly_plan.get("roles", [])
    if not required_roles:
        return execute_role_traversal_assembly_plan({"plans": []}, assembly_plan, backend=backend)
    recognition_roles = _role_recognition_candidates(required_roles, template_root, equipped_state, role_name_aliases)
    action_backend = backend or PyAutoGuiMouseBackend()
    ocr_engine = OCREngine()

    def press_up():
        execute_action_sequence(
            [{"name": "role_dpad_reset_to_first", "gamepad_button": "dpad_up"}],
            backend=action_backend,
        )

    def press_down():
        execute_action_sequence(
            [{"name": "role_dpad_next", "gamepad_button": "dpad_down"}],
            backend=action_backend,
        )

    def observe_current(_index: int):
        image, _rect = _capture_foreground_client_image()
        recognition = recognize_current_role_from_image(
            image,
            recognition_roles,
            ocr_engine,
            screen_size=screen_size,
            content_rect=image_content_rect,
            role_aliases=role_name_aliases,
        )
        logger.info(
            f"驱动装配角色扫描 #{_index + 1}："
            f"匹配={recognition.role_name or '未识别'}，"
            f"方式={recognition.method}，"
            f"置信度={recognition.confidence:.3f}，"
            f"OCR={recognition.raw_text!r}"
        )
        return recognition

    role_roster = collect_role_roster_with_dpad(
        required_roles,
        current_observer=observe_current,
        press_up=press_up,
        press_down=press_down,
        max_roles=max_pages or max(20, len(recognition_roles) + 6),
    )
    logger.info(
        "驱动装配角色扫描完成："
        f"已识别={role_roster.get('roles', [])}，"
        f"未识别={role_roster.get('unrecognized', [])}，"
        f"重复={role_roster.get('duplicates', [])}"
    )
    traversal_plan = plan_role_assembly_from_dpad_roster(
        required_roles,
        role_roster,
        screen_size=screen_size,
        content_rect=action_rect,
        current_index=role_roster.get("current_index", max(0, len(role_roster.get("roles", []) or []) - 1)),
    )
    checker = f12_stop_checker()

    def verifier(role_name: str, role_plan: dict[str, Any]):
        if not verification_enabled:
            return None
        image, rect = _capture_foreground_client_image()
        return verify_blueprint_against_screenshot(image, rect, role_plan)

    return execute_role_traversal_assembly_plan(
        traversal_plan,
        assembly_plan,
        backend=action_backend,
        should_stop=checker,
        role_verifier=verifier,
    )


def _filter_assembly_plan_for_roles(
    assembly_plan: dict[str, Any],
    target_roles: list[str] | tuple[str, ...] | None,
) -> dict[str, Any]:
    if not target_roles:
        return assembly_plan
    targets = [str(role) for role in target_roles if str(role).strip()]
    target_set = set(targets)
    role_plans = [
        plan for plan in assembly_plan.get("role_plans", [])
        if str(plan.get("role_name") or "") in target_set
    ]
    ready = [plan for plan in role_plans if plan.get("available")]
    filtered = dict(assembly_plan)
    filtered["roles"] = [role for role in targets if any(plan.get("role_name") == role for plan in role_plans)]
    filtered["role_plans"] = role_plans
    filtered["role_count"] = len(filtered["roles"])
    filtered["ready_count"] = len(ready)
    filtered["missing_roles"] = [role for role in targets if role not in set(filtered["roles"])]
    return filtered


def _role_recognition_candidates(
    required_roles: list[str] | tuple[str, ...],
    template_root: str,
    equipped_state: dict[str, Any] | None,
    role_name_aliases: dict[str, str] | None = None,
) -> list[str]:
    """Return all role names that can help identify the sidebar roster."""

    names: list[str] = []
    for role in required_roles:
        _append_unique_role_name(names, role)
    if isinstance(equipped_state, dict):
        for role in equipped_state:
            _append_unique_role_name(names, role)
    if isinstance(role_name_aliases, dict):
        for canonical, alias in role_name_aliases.items():
            _append_unique_role_name(names, canonical)
            _append_unique_role_name(names, alias)
    template_dir = Path(template_root)
    if template_dir.exists():
        for path in sorted(template_dir.glob("*.png")):
            _append_unique_role_name(names, path.stem)
    return names


def _append_unique_role_name(names: list[str], role_name: Any) -> None:
    value = str(role_name).strip()
    if value and value not in names:
        names.append(value)


def verify_blueprint_against_screenshot(
    image: np.ndarray,
    rect: Any,
    role_plan: dict[str, Any],
    sample_radius: int = 4,
    brightness_threshold: float = 22.0,
) -> dict[str, Any]:
    """Check that expected drive block target positions look occupied in a screenshot."""

    if image is None or image.size == 0:
        return {"ok": False, "reason": "empty_screenshot", "missing_blocks": []}
    missing: list[dict[str, Any]] = []
    for block in role_plan.get("drive_blocks", []) or []:
        position = block.get("pixel_position")
        if not position:
            continue
        x = int(position[0]) - int(getattr(rect, "left", 0))
        y = int(position[1]) - int(getattr(rect, "top", 0))
        if not _sample_position_looks_occupied(image, x, y, sample_radius, brightness_threshold):
            missing.append({"block_id": block.get("block_id"), "position": tuple(position)})
    return {"ok": not missing, "missing_blocks": missing}


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


def _sample_position_looks_occupied(
    image: np.ndarray,
    x: int,
    y: int,
    radius: int,
    brightness_threshold: float,
) -> bool:
    height, width = image.shape[:2]
    x1 = max(0, min(width, x - radius))
    x2 = max(0, min(width, x + radius + 1))
    y1 = max(0, min(height, y - radius))
    y2 = max(0, min(height, y + radius + 1))
    if x1 >= x2 or y1 >= y2:
        return False
    patch = image[y1:y2, x1:x2]
    return float(np.mean(patch)) >= brightness_threshold


def _fit_content_rect(width: int, height: int, reference_size: tuple[int, int] = (2560, 1440)) -> tuple[int, int, int, int]:
    base_w, base_h = reference_size
    base_aspect = base_w / base_h
    target_aspect = width / height
    if target_aspect >= base_aspect:
        content_h = height
        content_w = round(content_h * base_aspect)
        left = round((width - content_w) / 2)
        top = 0
    else:
        content_w = width
        content_h = round(content_w / base_aspect)
        left = 0
        top = round((height - content_h) / 2)
    return left, top, max(1, content_w), max(1, content_h)


def _tape_install_sequence(
    tape_filter: dict[str, Any],
    screen_size: tuple[int, int] | None,
    content_rect: tuple[int, int, int, int] | None,
) -> list[dict[str, Any]]:
    sequence: list[dict[str, Any]] = []
    sequence.extend(map_page_controls(screen_size, content_rect)["click_sequence"])
    sequence.extend(map_tape_filter_controls(screen_size, content_rect)["set_filter_sequence"])
    sequence.extend(map_tape_set_selection(tape_filter["set_name"], screen_size, content_rect)["selection_sequence"])
    sequence.extend(
        map_tape_filter_refinement(
            [tape_filter.get("quality", "Gold")],
            screen_size,
            content_rect,
            include_main_stat_expand=False,
        )["refinement_sequence"]
    )
    sequence.extend(map_tape_main_stat_gamepad_open()["open_sequence"])
    main_stat_selection = map_tape_main_stat_selection(tape_filter["main_stat"], screen_size, content_rect)
    sequence.extend(main_stat_selection.get("ocr_selection_sequence") or main_stat_selection["selection_sequence"])
    sequence.extend(map_tape_sub_stat_filter_entry(screen_size, content_rect)["entry_sequence"])
    sequence.extend(map_tape_sub_stat_selection(tape_filter.get("sub_stats", []), screen_size, content_rect)["selection_sequence"])
    sequence.extend(map_tape_equip_first_result(screen_size, content_rect)["equip_sequence"])
    return sequence
