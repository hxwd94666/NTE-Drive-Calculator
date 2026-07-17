# 汇总装配计划并提供配装页面按钮调用的后端入口。
"""UI bridge for drive assembly planning."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import time
from typing import Any

import cv2
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
    collect_role_roster_from_role_list,
    plan_role_assembly_from_role_list_roster,
    plan_role_assembly_from_observations,
    recognize_current_role_from_image,
    recognize_role_slots_from_image,
    required_roles_from_payloads,
)
from src.scanner.ocr_engine import OCREngine
from src.scanner.window_capture import capture_foreground_window, game_content_rect
from src.utils.logger import logger


_STARTUP_ROLE_RECOGNITION_METHODS = {
    "ocr",
    "ocr_fallback",
    "ocr_correction",
    "ocr_yi_fallback",
}
_RECORDED_ASSEMBLY_ACTIONS = {
    "open_role_list",
    "confirm_role_list_selection",
    "close_role_list_after_confirmation",
    "left_kongmu_tab",
    "wait_after_left_kongmu_tab",
    "assemble_button",
    "wait_after_assemble_button",
    "assembly_back_to_role_page",
}


class _AssemblyRunRecorder:
    """Persist screenshots for the navigation steps of one assembly run."""

    def __init__(self, root: Path | None = None):
        record_root = root or (runtime.SCREENSHOT_DIR / "record")
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.directory = record_root / f"assembly_{run_id}"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            logger.warning(f"无法创建装配过程截图目录 | 路径={self.directory} | 原因={exc}")
            self.directory = None
        self._sequence = 0

    def save_image(self, image: np.ndarray, label: str) -> Path | None:
        if self.directory is None or image is None or image.size == 0:
            return None
        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", str(label)).strip("_") or "screen"
        self._sequence += 1
        path = self.directory / f"{self._sequence:03d}_{safe_label}.png"
        try:
            ok, encoded = cv2.imencode(".png", image)
            if not ok:
                raise ValueError("PNG encoding failed")
            encoded.tofile(str(path))
            logger.info(f"装配过程截图已保存 | {path}")
            return path
        except Exception as exc:
            logger.warning(f"保存装配过程截图失败 | 标签={safe_label} | 原因={exc}")
            return None

    def capture_foreground(self, label: str) -> Path | None:
        try:
            image, _rect = _capture_foreground_client_image()
        except Exception as exc:
            logger.warning(f"截取装配过程页面失败 | 标签={label} | 原因={exc}")
            return None
        return self.save_image(image, label)

    def record_action(self, action: dict[str, Any], role_name: str | None) -> None:
        action_name = str(action.get("name") or "")
        if action_name not in _RECORDED_ASSEMBLY_ACTIONS:
            return
        role_suffix = f"_{role_name}" if role_name else ""
        self.capture_foreground(f"{action_name}{role_suffix}")


def _is_role_detail_startup_recognition(recognition: Any) -> bool:
    """Accept only high-confidence role-detail OCR before sending navigation input."""

    return bool(
        getattr(recognition, "role_name", None)
        and getattr(recognition, "method", "") in _STARTUP_ROLE_RECOGNITION_METHODS
    )


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
    recorder = _AssemblyRunRecorder()
    recorder.save_image(first_image, "startup")
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
    _log_assembly_plan_diagnostics(assembly_plan, screen_size, action_rect)
    if not required_roles:
        logger.warning("驱动装配未启动 | 原因=没有可装配的目标角色")
        return execute_role_traversal_assembly_plan({"plans": []}, assembly_plan, backend=backend)
    recognition_roles = _role_recognition_candidates(required_roles, template_root, equipped_state, role_name_aliases)
    ocr_engine = OCREngine()
    startup_recognition = recognize_current_role_from_image(
        first_image,
        recognition_roles,
        ocr_engine,
        screen_size=screen_size,
        content_rect=image_content_rect,
        role_aliases=role_name_aliases,
    )
    logger.info(
        "Assembly startup page recognition | "
        f"role={startup_recognition.role_name or 'unrecognized'} | "
        f"method={startup_recognition.method} | OCR={startup_recognition.raw_text!r} | "
        f"record_dir={recorder.directory}"
    )
    if not _is_role_detail_startup_recognition(startup_recognition):
        logger.warning(
            "Assembly was not started because the current page is not a recognized role detail page | "
            f"OCR={startup_recognition.raw_text!r} | record_dir={recorder.directory}"
        )
        return execute_role_traversal_assembly_plan(
            {
                "plans": [],
                "missing_roles": required_roles,
                "unrecognized": [{"roster_index": 0, "raw_text": startup_recognition.raw_text}],
            },
            assembly_plan,
            backend=backend,
        )
    logger.info(
        "驱动装配角色扫描开始 | "
        f"目标角色={required_roles} | 识别候选数={len(recognition_roles)} | "
        f"窗口尺寸={screen_size} | 操作区域={action_rect} | 向上复位次数=5"
    )
    logger.info(
        "角色列表扫描路径 | 复位=dpad_up×5 | 打开列表=RS | "
        "逐格移动=左摇杆 | 确认=A | 达成全部目标后保留列表并规划返回首个目标"
    )
    action_backend = backend or PyAutoGuiMouseBackend()
    randomization_enabled = _enable_assembly_randomization(action_backend)
    logger.info(
        "驱动装配随机化 | "
        f"鼠标随机化={'已启用' if randomization_enabled else '后端不支持'} | "
        "点击偏移、拖拽路径与拖拽节奏随机；手柄路径保持固定"
    )
    def press_up():
        execute_action_sequence(
            [{"name": "role_list_reset_dpad_up", "gamepad_button": "dpad_up"}],
            backend=action_backend,
        )

    def open_role_list():
        execute_action_sequence(
            [{"name": "open_role_list", "gamepad_button": "rs"}],
            backend=action_backend,
        )
        logger.info("角色列表打开指令已发送 | button=RS")
        recorder.capture_foreground("role_list_opened")

    def confirm_role_list_selection():
        execute_action_sequence(
            [{"name": "confirm_role_list_selection", "gamepad_button": "a"}],
            backend=action_backend,
        )

    def move_role_list_right():
        execute_action_sequence(
            [
                {
                    "name": "role_list_next",
                    "gamepad_stick": "left_right",
                    "post_action_pause_seconds": 0.25,
                }
            ],
            backend=action_backend,
        )

    def observe_current(_index: int):
        image, _rect = _capture_foreground_client_image()
        recorder.save_image(image, f"role_list_scan_{_index + 1:02d}")
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

    try:
        role_roster = collect_role_roster_from_role_list(
            required_roles,
            current_observer=observe_current,
            press_up=press_up,
            open_role_list=open_role_list,
            confirm_selection=confirm_role_list_selection,
            move_right=move_role_list_right,
            max_roles=max_pages or max(20, len(recognition_roles) + 6),
        )
    except BaseException:
        _close_assembly_backend(action_backend)
        raise
    logger.info(
        "驱动装配角色扫描完成："
        f"已识别={role_roster.get('roles', [])}，"
        f"未识别={role_roster.get('unrecognized', [])}，"
        f"重复={role_roster.get('duplicates', [])}"
    )
    logger.info(
        "角色列表扫描结果 | "
        f"停止原因={role_roster.get('stop_reason', '')} | "
        f"缺少={role_roster.get('missing_expected_roles', [])} | "
        f"当前列表索引={role_roster.get('current_index', 0)} | 列表保持打开={role_roster.get('list_open', False)}"
    )
    recorder.capture_foreground("role_list_scan_complete")
    traversal_plan = plan_role_assembly_from_role_list_roster(
        required_roles,
        role_roster,
        screen_size=screen_size,
        content_rect=action_rect,
        current_index=role_roster.get("current_index", max(0, len(role_roster.get("roles", []) or []) - 1)),
    )
    _log_traversal_plan_diagnostics(traversal_plan)
    checker = f12_stop_checker()

    def verifier(role_name: str, role_plan: dict[str, Any]):
        if not verification_enabled:
            return None
        image, rect = _capture_foreground_client_image()
        return verify_blueprint_against_screenshot(image, rect, role_plan)

    try:
        report = execute_role_traversal_assembly_plan(
            traversal_plan,
            assembly_plan,
            backend=action_backend,
            should_stop=checker,
            role_verifier=verifier,
            on_action_executed=recorder.record_action,
        )
    finally:
        _close_assembly_backend(action_backend)
    logger.info(
        "驱动装配总结果 | "
        f"已执行={report.executed_actions} | 导航={report.navigation_actions} | "
        f"角色={[(item.role_name, item.executed_actions, len(item.skipped_actions)) for item in report.role_reports]} | "
        f"跳过角色={report.skipped_roles} | 校验失败={report.verification_failures}"
    )
    return report


def _enable_assembly_randomization(backend: MouseBackend) -> bool:
    """Enable bounded mouse randomization when the selected backend supports it."""

    enable_randomization = getattr(backend, "enable_randomization", None)
    if not callable(enable_randomization):
        return False
    enable_randomization()
    return True


def _close_assembly_backend(backend: MouseBackend) -> bool:
    """Close the virtual controller when the backend owns one."""

    close = getattr(backend, "close", None)
    if not callable(close):
        return False
    try:
        close()
    except Exception as exc:
        logger.warning(f"虚拟手柄关闭失败 | {exc}")
        return False
    logger.info("虚拟手柄已重置并关闭")
    return True


def _log_assembly_plan_diagnostics(
    assembly_plan: dict[str, Any],
    screen_size: tuple[int, int],
    content_rect: tuple[int, int, int, int],
) -> None:
    """Log the planned equipment and target geometry before any UI input."""

    logger.info(
        "驱动装配计划生成 | "
        f"角色={assembly_plan.get('roles', [])} | 可执行={assembly_plan.get('ready_count', 0)}/{assembly_plan.get('role_count', 0)} | "
        f"窗口尺寸={screen_size} | 操作区域={content_rect}"
    )
    for role_plan in assembly_plan.get("role_plans", []):
        role_name = role_plan.get("role_name", "未命名")
        if not role_plan.get("available"):
            logger.warning(f"角色装配计划不可用 | 角色={role_name} | 原因={role_plan.get('reason', '未知')}")
            continue
        logger.info(
            "角色装配计划 | "
            f"角色={role_name} | 卡带={role_plan.get('tape_count', 0)} | 驱动={role_plan.get('drive_count', 0)} | "
            f"顶层动作={[action.get('name') for action in role_plan.get('actions', [])]}"
        )
        for block in role_plan.get("drive_blocks", []) or []:
            drive = block.get("drive") if isinstance(block.get("drive"), dict) else {}
            logger.info(
                "驱动装配目标 | "
                f"角色={role_name} | 块={block.get('block_id')} | uid={drive.get('uid', '未知')} | "
                f"形状={block.get('drive_type') or drive.get('shape_id', '未知')} | 品质={drive.get('quality', '未知')} | "
                f"套装={block.get('set_name') or drive.get('set_name', '未筛选')} | 副词条={drive.get('sub_stats', {})} | "
                f"格子={block.get('cells', [])} | 质心={block.get('grid_centroid') or block.get('shape_centroid')} | "
                f"目标={block.get('pixel_position')} | 重复={bool(block.get('is_duplicate_drive') or block.get('is_duplicate_equipment'))}"
            )


def _log_traversal_plan_diagnostics(traversal_plan: dict[str, Any]) -> None:
    """Log the role-recognition result and the exact D-pad route per role."""

    logger.info(
        "角色路径规划完成 | "
        f"导航={traversal_plan.get('navigation', '未知')} | 计划={traversal_plan.get('planned_roles', [])} | "
        f"缺失={traversal_plan.get('missing_roles', [])} | 未识别={traversal_plan.get('unrecognized', [])} | "
        f"重复={traversal_plan.get('duplicates', [])}"
    )
    for step in traversal_plan.get("plans", []):
        moves = [
            action.get("gamepad_button") or action.get("gamepad_stick")
            for action in step.get("action_sequence", [])
            if action.get("gamepad_button") or action.get("gamepad_stick")
        ]
        logger.info(
            "角色路径 | "
            f"角色={step.get('role_name')} | 起始索引={step.get('start_roster_index')} | "
            f"目标索引={step.get('roster_index')} | 手柄路径={moves} | "
            f"进入动作={[action.get('name') for action in step.get('action_sequence', [])]}"
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
    return game_content_rect(width, height, reference_size)


def _tape_install_sequence(
    tape_filter: dict[str, Any],
    screen_size: tuple[int, int] | None,
    content_rect: tuple[int, int, int, int] | None,
) -> list[dict[str, Any]]:
    sequence: list[dict[str, Any]] = []
    sequence.extend(map_page_controls(screen_size, content_rect)["click_sequence"])
    sequence.extend(map_tape_filter_controls(screen_size, content_rect)["set_filter_sequence"])
    sequence.extend(map_tape_set_selection(tape_filter["set_name"], screen_size, content_rect)["selection_sequence"])
    quality = str(tape_filter.get("quality") or "").strip()
    is_duplicate_tape = bool(
        tape_filter.get("is_duplicate_tape") or tape_filter.get("is_duplicate_equipment")
    )
    sequence.extend(
        map_tape_filter_refinement(
            [quality] if quality else [],
            screen_size,
            content_rect,
            include_main_stat_expand=False,
            include_status_filters=is_duplicate_tape,
        )["refinement_sequence"]
    )
    sequence.extend(map_tape_main_stat_gamepad_open()["open_sequence"])
    main_stat_selection = map_tape_main_stat_selection(tape_filter["main_stat"], screen_size, content_rect)
    sequence.extend(main_stat_selection.get("ocr_selection_sequence") or main_stat_selection["selection_sequence"])
    sequence.extend(map_tape_sub_stat_filter_entry(screen_size, content_rect)["entry_sequence"])
    sequence.extend(map_tape_sub_stat_selection(tape_filter.get("sub_stats", []), screen_size, content_rect)["selection_sequence"])
    sequence.extend(map_tape_equip_first_result(screen_size, content_rect)["equip_sequence"])
    return sequence
