# 执行游戏内装配计划中的屏幕坐标点击、拖拽和等待动作。
"""Execute drive assembly plans with a mouse backend."""

from __future__ import annotations

from typing import Any, Callable

from src.utils.logger import logger

DEFAULT_ACTION_PAUSE_SECONDS = 0.5
FILTER_ACTION_PAUSE_SECONDS = 0.25
DEFAULT_DRAG_DURATION_MS = 1200
STOP_POLL_INTERVAL_SECONDS = 0.05

FILTER_INTERACTION_ACTION_NAMES = {
    "tape_tab",
    "drive_tab",
    "filter_button",
    "reset_filter",
    "set_select",
    "set_option",
    "drive_set_select",
    "drive_set_option",
    "confirm_filter",
    "confirm_drive_set_filter",
    "shape_select",
    "shape_option",
    "confirm_shape_filter",
    "status_locked",
    "status_discarded",
    "status_other",
    "quality_blue",
    "quality_purple",
    "quality_orange",
    "verify_quality_selected",
    "main_stat_expand",
    "main_stat_option",
    "sub_stat_expand",
    "sub_stat_option",
    "sub_stat_count_four",
}

__all__ = [
    "ActionExecutionReport",
    "AssemblyExecutionReport",
    "AssemblyExecutionStopped",
    "MOUSEEVENTF_ABSOLUTE",
    "MOUSEEVENTF_LEFTDOWN",
    "MOUSEEVENTF_LEFTUP",
    "MOUSEEVENTF_MOVE",
    "MouseBackend",
    "PyAutoGuiMouseBackend",
    "_VirtualGamepadDriver",
    "_WindowsSendInputMouseDriver",
    "execute_action_sequence",
    "execute_all_role_assembly_plan",
    "execute_role_assembly_plan",
    "execute_role_traversal_assembly_plan",
    "f12_stop_checker",
]


from src.features.drive_assembly.input_backends import (
    MouseBackend,
    ActionExecutionReport,
    AssemblyExecutionReport,
    AssemblyExecutionStopped,
    f12_stop_checker,
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MOVE,
    PyAutoGuiMouseBackend,
    _WindowsSendInputMouseDriver,
    _VirtualGamepadDriver,
)


def execute_action_sequence(
    sequence: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    backend: MouseBackend | None = None,
    pause_seconds: float = DEFAULT_ACTION_PAUSE_SECONDS,
    should_stop: Callable[[], bool] | None = None,
    role_name: str | None = None,
    on_action_executed: Callable[[dict[str, Any], str | None], None] | None = None,
) -> ActionExecutionReport:
    """Execute a flat click/drag sequence."""

    mouse = backend or PyAutoGuiMouseBackend()
    report = ActionExecutionReport(role_name=role_name)
    runtime_state: dict[str, Any] = {}
    for action in sequence:
        if should_stop and should_stop():
            raise AssemblyExecutionStopped("assembly execution stopped")
        if _execute_one_action(action, mouse, should_stop=should_stop, runtime_state=runtime_state):
            report.executed_actions += 1
            if action.get("duplicate_status_filter"):
                logger.info(
                    "重复装备状态筛选已执行 | "
                    f"角色={role_name or '未指定'} | 块={action.get('block_id', '未指定')} | "
                    f"分组={action.get('duplicate_group_id') or '未分组'} | 状态={action.get('name')}"
                )
            action_pause_seconds = float(
                action.get("post_action_pause_seconds", _default_action_pause_seconds(action, pause_seconds))
            )
            if action_pause_seconds > 0:
                _pause_with_stop(mouse, action_pause_seconds, should_stop)
            if on_action_executed is not None:
                try:
                    on_action_executed(dict(action), role_name)
                except Exception as exc:
                    logger.warning(f"装配动作记录回调失败 | {_action_diagnostic(action)} | 原因={exc}")
        else:
            report.skipped_actions.append(dict(action))
            logger.warning(
                "装配动作跳过 | "
                f"角色={role_name or '未指定'} | {_action_diagnostic(action)} | "
                "原因=动作不受支持或前置检测失败"
            )
    return report


def _default_action_pause_seconds(action: dict[str, Any], pause_seconds: float) -> float:
    """Use a shorter pause for ordinary filtering clicks and confirmations."""

    if str(action.get("name") or "") in FILTER_INTERACTION_ACTION_NAMES:
        return min(float(pause_seconds), FILTER_ACTION_PAUSE_SECONDS)
    return float(pause_seconds)


def execute_role_assembly_plan(
    plan: dict[str, Any],
    backend: MouseBackend | None = None,
    pause_seconds: float = DEFAULT_ACTION_PAUSE_SECONDS,
    should_stop: Callable[[], bool] | None = None,
    startup_delay_seconds: float = 0.0,
    role_verifier: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None,
    on_action_executed: Callable[[dict[str, Any], str | None], None] | None = None,
) -> ActionExecutionReport:
    """Execute all install actions for one role plan."""

    role_name = str(plan.get("role_name") or "")
    if not plan.get("available"):
        return ActionExecutionReport(role_name=role_name)
    mouse = backend or PyAutoGuiMouseBackend()
    logger.info(
        "角色装配开始 | "
        f"角色={role_name or '未指定'} | 卡带={plan.get('tape_count', 0)} | "
        f"驱动={plan.get('drive_count', 0)} | 顶层动作={[action.get('name') for action in plan.get('actions', [])]}"
    )
    if startup_delay_seconds > 0:
        _pause_with_stop(mouse, startup_delay_seconds, should_stop)
    combined = _flatten_role_actions(plan.get("actions", []))
    report = execute_action_sequence(
        combined,
        backend=mouse,
        pause_seconds=pause_seconds,
        should_stop=should_stop,
        role_name=role_name,
        on_action_executed=on_action_executed,
    )
    if role_verifier is not None:
        role_verifier(role_name, plan)
    logger.info(
        "角色装配结束 | "
        f"角色={role_name or '未指定'} | 已执行={report.executed_actions} | "
        f"跳过={[action.get('name', '未命名') for action in report.skipped_actions]}"
    )
    return report


def execute_all_role_assembly_plan(
    plan: dict[str, Any],
    backend: MouseBackend | None = None,
    pause_seconds: float = DEFAULT_ACTION_PAUSE_SECONDS,
    should_stop: Callable[[], bool] | None = None,
) -> AssemblyExecutionReport:
    """Execute every available role plan in an all-role assembly plan."""

    mouse = backend or PyAutoGuiMouseBackend()
    report = AssemblyExecutionReport()
    for role_plan in plan.get("role_plans", []):
        role_name = str(role_plan.get("role_name") or "")
        if not role_plan.get("available"):
            if role_name:
                report.skipped_roles.append(role_name)
            continue
        role_report = execute_role_assembly_plan(
            role_plan,
            backend=mouse,
            pause_seconds=pause_seconds,
            should_stop=should_stop,
        )
        report.role_reports.append(role_report)
    return report


def execute_role_traversal_assembly_plan(
    traversal_plan: dict[str, Any],
    assembly_plan: dict[str, Any],
    backend: MouseBackend | None = None,
    pause_seconds: float = DEFAULT_ACTION_PAUSE_SECONDS,
    should_stop: Callable[[], bool] | None = None,
    role_verifier: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None,
    on_action_executed: Callable[[dict[str, Any], str | None], None] | None = None,
) -> AssemblyExecutionReport:
    """Execute role-list traversal and run the matching assembly plan for each role."""

    mouse = backend or PyAutoGuiMouseBackend()
    role_plans = _role_plan_lookup(assembly_plan)
    report = AssemblyExecutionReport()
    report.missing_roles = list(traversal_plan.get("missing_roles", []) or [])
    report.duplicate_roles = list(traversal_plan.get("duplicates", []) or [])
    report.unrecognized_roles = list(traversal_plan.get("unrecognized", []) or [])
    logger.info(
        "装配遍历执行开始 | "
        f"计划角色={[step.get('role_name') for step in traversal_plan.get('plans', [])]} | "
        f"缺失={report.missing_roles} | 未识别={report.unrecognized_roles} | 重复角色={report.duplicate_roles}"
    )
    for step in traversal_plan.get("plans", []):
        logger.info(
            "角色路径执行 | "
            f"角色={step.get('role_name')} | 起始索引={step.get('start_roster_index')} | "
            f"目标索引={step.get('roster_index')} | "
            f"导航={[action.get('gamepad_button') or action.get('gamepad_stick') or action.get('name') for action in step.get('action_sequence', []) if not _is_role_blueprint_assembly_action(action)]}"
        )
        pending_actions: list[dict[str, Any]] = []
        for action in step.get("action_sequence", []):
            if not _is_role_blueprint_assembly_action(action):
                pending_actions.append(action)
                continue
            if pending_actions:
                action_report = execute_action_sequence(
                    pending_actions,
                    backend=mouse,
                    pause_seconds=pause_seconds,
                    should_stop=should_stop,
                    role_name=step.get("role_name"),
                    on_action_executed=on_action_executed,
                )
                report.navigation_actions += action_report.executed_actions
                pending_actions = []
            role_name = str(action.get("role_name") or step.get("role_name") or "")
            role_plan = role_plans.get(role_name)
            if role_plan is None:
                if role_name:
                    report.skipped_roles.append(role_name)
                logger.warning(f"角色装配跳过 | 角色={role_name or '未指定'} | 原因=未找到对应装配计划")
                continue
            role_report = execute_role_assembly_plan(
                role_plan,
                backend=mouse,
                pause_seconds=pause_seconds,
                should_stop=should_stop,
                on_action_executed=on_action_executed,
            )
            if role_verifier is not None:
                verification = role_verifier(role_name, role_plan)
                if verification and not verification.get("ok", True):
                    report.verification_failures.append({"role_name": role_name, **verification})
                    logger.warning(f"角色装配校验失败 | 角色={role_name} | 详情={verification}")
            report.role_reports.append(role_report)
        if pending_actions:
            action_report = execute_action_sequence(
                pending_actions,
                backend=mouse,
                pause_seconds=pause_seconds,
                should_stop=should_stop,
                role_name=step.get("role_name"),
                on_action_executed=on_action_executed,
            )
            report.navigation_actions += action_report.executed_actions
    logger.info(
        "装配遍历执行结束 | "
        f"导航动作={report.navigation_actions} | 角色报告={[(item.role_name, item.executed_actions) for item in report.role_reports]} | "
        f"跳过角色={report.skipped_roles} | 校验失败={report.verification_failures}"
    )
    return report


def _flatten_role_actions(actions: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    sequence: list[dict[str, Any]] = []
    for action in actions:
        name = action.get("name")
        if name == "install_drives":
            sequence.extend(_expand_drive_install_sequence(action))
        else:
            sequence.extend(action.get("sequence", []))
    return sequence


def _role_plan_lookup(assembly_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(role_plan.get("role_name")): role_plan
        for role_plan in assembly_plan.get("role_plans", [])
        if role_plan.get("role_name")
    }


def _is_role_blueprint_assembly_action(action: dict[str, Any]) -> bool:
    return action.get("name") in {"assemble_current_role_from_blueprint", "run_drive_assembly_for_role"}


def _action_diagnostic(action: dict[str, Any]) -> str:
    """Return concise, useful context for an action that could not run."""

    fields = [f"动作={action.get('name', '未命名')}"]
    for key in ("block_id", "position", "from", "to", "target_position", "duration_ms", "gamepad_button", "gamepad_stick"):
        if key in action:
            fields.append(f"{key}={action[key]}")
    return " | ".join(fields)


def _expand_drive_install_sequence(action: dict[str, Any]) -> list[dict[str, Any]]:
    install_plans = action.get("install_plans", []) or []
    result: list[dict[str, Any]] = []
    for item in action.get("sequence", []) or []:
        if item.get("name") != "install_drive_block":
            result.append(item)
            continue
        index = int(item.get("sequence_index", -1))
        if index < 0 or index >= len(install_plans):
            result.append(item)
            continue
        result.extend(install_plans[index].get("install_sequence", []))
    return result


def _execute_one_action(
    action: dict[str, Any],
    backend: MouseBackend,
    should_stop: Callable[[], bool] | None = None,
    runtime_state: dict[str, Any] | None = None,
) -> bool:
    state = runtime_state if runtime_state is not None else {}
    if "wait_seconds" in action:
        _pause_with_stop(backend, float(action.get("wait_seconds") or 0.0), should_stop)
        return True
    if "selection_probe_position" in action and "retry_position" in action:
        return _retry_unselected_quality(action, backend, should_stop)
    if action.get("name") == "capture_drive_target_baseline":
        return _capture_drive_target_baseline(action, backend, state)
    if action.get("name") == "verify_drive_block_installed":
        return _retry_missing_drive_block(action, backend, should_stop, state)
    if "ocr_target_text" in action:
        return _click_ocr_target(action, backend)
    if "optional_confirm_position" in action:
        return _maybe_click_optional_confirm(action, backend)
    if "wheel_clicks" in action:
        scroll = getattr(backend, "scroll", None)
        if scroll is None:
            raise TypeError("backend does not support wheel actions")
        position = _point(action["position"])
        clicks = int(action["wheel_clicks"])
        interval = max(0.0, float(action.get("wheel_click_interval_seconds") or 0.0))
        if interval <= 0.0 or abs(clicks) <= 1:
            scroll(position, clicks)
            return True
        direction = 1 if clicks > 0 else -1
        for click_index in range(abs(clicks)):
            if should_stop and should_stop():
                raise AssemblyExecutionStopped("assembly execution stopped")
            scroll(position, direction)
            if click_index + 1 < abs(clicks):
                _pause_with_stop(backend, interval, should_stop)
        return True
    if action.get("mouse_move_only"):
        position = _point(action["position"])
        move_to = getattr(backend, "move_to", None)
        if not callable(move_to):
            raise TypeError("backend does not support pointer-only move actions")
        move_to(position)
        return True
    if "position" in action:
        position = _point(action["position"])
        if action.get("ensure_mouse_release"):
            _force_mouse_release(backend)
        cloud_click = getattr(backend, "cloud_click", None)
        if action.get("cloud_click") and callable(cloud_click):
            cloud_click(position, hold_seconds=float(action.get("click_hold_seconds") or 0.12))
        else:
            backend.click(position)
        if action.get("ensure_mouse_release"):
            _force_mouse_release(backend)
        return True
    if "keyboard_key" in action:
        _press_keyboard_key(backend, str(action["keyboard_key"]))
        return True
    if "gamepad_button" in action:
        _press_gamepad_button(backend, str(action["gamepad_button"]))
        return True
    if "gamepad_stick" in action:
        _push_gamepad_stick(backend, str(action["gamepad_stick"]))
        return True
    if action.get("name") == "force_drag_first_drive_to_block":
        start = _point(action["from"])
        end = _point(action["to"])
        duration_ms = int(action.get("duration_ms") or DEFAULT_DRAG_DURATION_MS)
        logger.info(
            f"Drive block {action.get('block_id')} forced drag started: "
            f"{start} -> {end} ({duration_ms}ms)"
        )
        # This first placement attempt intentionally runs even when the list is
        # empty or a screenshot probe cannot determine the filter result.
        backend.drag(start, end, duration_ms)
        logger.info(f"Drive block {action.get('block_id')} forced drag completed")
        return True
    if "from" in action and "to" in action:
        start = _point(action["from"])
        end = _point(action["to"])
        duration_ms = int(action.get("duration_ms") or DEFAULT_DRAG_DURATION_MS)
        if _is_scroll_action(action) and action.get("drag_mode") != "standard":
            _drag_scroll(backend, start, end, duration_ms)
        else:
            if action.get("name") == "drag_first_drive_to_block":
                logger.info(
                    f"Drive block {action.get('block_id')} drag started: "
                    f"{start} -> {end} ({duration_ms}ms)"
                )
            backend.drag(start, end, duration_ms)
            if action.get("name") == "drag_first_drive_to_block":
                logger.info(f"Drive block {action.get('block_id')} drag completed")
        return True
    return False



def _force_mouse_release(backend: MouseBackend) -> None:
    release = getattr(backend, "force_mouse_release", None)
    if callable(release):
        release()


def _retry_unselected_quality(
    action: dict[str, Any],
    backend: MouseBackend,
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """Retry one quality button click only when its pink selected state is absent."""

    capture = getattr(backend, "screenshot", None)
    if capture is None:
        logger.warning(f"驱动块 {action.get('block_id')} 无法校验 | 原因=后端不支持截图")
        return False
    try:
        image = capture()
    except Exception as exc:
        logger.warning(f"驱动块 {action.get('block_id')} 无法校验 | 原因=截图失败 | 异常={exc!r}")
        return False
    if _quality_button_looks_selected(image, _point(action["selection_probe_position"])):
        return True
    backend.click(_point(action["retry_position"]))
    _pause_with_stop(backend, DEFAULT_ACTION_PAUSE_SECONDS, should_stop)
    return True


def _retry_missing_drive_block(
    action: dict[str, Any],
    backend: MouseBackend,
    should_stop: Callable[[], bool] | None = None,
    runtime_state: dict[str, Any] | None = None,
) -> bool:
    """Retry a drive drag when the target area did not change after the first drag."""

    capture = getattr(backend, "screenshot", None)
    if capture is None:
        return False
    try:
        image = capture()
    except Exception:
        return False

    target = action.get("target_position")
    retry_from = action.get("retry_from")
    retry_to = action.get("retry_to") or target
    if not target or not retry_from or not retry_to:
        logger.warning(f"驱动块 {action.get('block_id')} 无法校验 | 原因=缺少重试坐标 | target={target} from={retry_from} to={retry_to}")
        return False
    target_point = _point(target)
    # New plans provide a wider comparison sample. Keep the old narrow sample
    # for baseline-less fallback actions that only use brightness detection.
    sample_radius = int(action.get("sample_radius") or 4)
    state = runtime_state if runtime_state is not None else {}
    baseline = state.get(_drive_target_state_key(action))
    current = _drive_target_sample(image, target_point, sample_radius)

    if baseline is not None and current is not None:
        changed = _drive_target_changed(
            baseline,
            current,
            minimum_difference=float(action.get("change_threshold") or 15.0),
        )
        if changed:
            logger.info(
                f"Drive block {action.get('block_id')} install verified by target-image change | "
                f"target={target_point} | radius={sample_radius} | threshold={action.get('change_threshold') or 15.0}"
            )
            return True
        logger.warning(
            f"Drive block {action.get('block_id')} target image unchanged; retrying drag | "
            f"target={target_point} | radius={sample_radius} | threshold={action.get('change_threshold') or 15.0}"
        )
    elif _drive_target_looks_occupied(
        image,
        target_point,
        radius=sample_radius,
        brightness_threshold=float(action.get("brightness_threshold") or 80.0),
    ):
        logger.info(
            f"Drive block {action.get('block_id')} install verified by fallback brightness | "
            f"target={target_point} | radius={sample_radius} | threshold={action.get('brightness_threshold') or 80.0}"
        )
        return True
    else:
        logger.warning(
            f"Drive block {action.get('block_id')} has no baseline; retrying drag | "
            f"target={target_point} | radius={sample_radius} | threshold={action.get('brightness_threshold') or 80.0}"
        )

    retry_start = _point(retry_from)
    retry_end = _point(retry_to)
    retry_duration = int(action.get("retry_duration_ms") or DEFAULT_DRAG_DURATION_MS)
    logger.info(
        f"Drive block {action.get('block_id')} retry drag started: "
        f"{retry_start} -> {retry_end} ({retry_duration}ms)"
    )
    backend.drag(retry_start, retry_end, retry_duration)
    logger.info(f"Drive block {action.get('block_id')} retry drag completed")
    _pause_with_stop(backend, float(action.get("retry_prompt_wait_seconds") or 0.3), should_stop)
    _maybe_click_optional_confirm(action, backend)
    _pause_with_stop(backend, float(action.get("retry_settle_seconds") or 1.0), should_stop)
    return True


def _capture_drive_target_baseline(
    action: dict[str, Any],
    backend: MouseBackend,
    runtime_state: dict[str, Any],
) -> bool:
    """Save a small pre-drag target image for reliable post-drag verification."""

    capture = getattr(backend, "screenshot", None)
    target = action.get("target_position")
    if capture is None or not target:
        logger.warning(
            f"驱动块 {action.get('block_id')} 未采集基线 | "
            f"原因={'后端不支持截图' if capture is None else '缺少目标坐标'} | target={target}"
        )
        return False
    try:
        image = capture()
    except Exception as exc:
        logger.warning(f"驱动块 {action.get('block_id')} 未采集基线 | 原因=截图失败 | 异常={exc!r}")
        return False
    sample = _drive_target_sample(image, _point(target), int(action.get("sample_radius") or 12))
    if sample is None:
        logger.warning(f"驱动块 {action.get('block_id')} 未采集基线 | 原因=目标采样无效 | target={target}")
        return False
    runtime_state[_drive_target_state_key(action)] = sample
    logger.info(
        f"Drive block {action.get('block_id')} target baseline captured | "
        f"target={_point(target)} | radius={action.get('sample_radius') or 12}"
    )
    return True


def _drive_target_state_key(action: dict[str, Any]) -> str:
    block_id = action.get("block_id")
    target = action.get("target_position") or action.get("retry_to") or ()
    return f"drive-target:{block_id}:{target}"


def _drive_target_sample(image: Any, target: tuple[int, int], radius: int) -> Any | None:
    try:
        import numpy as np

        pixels = np.asarray(image)
        if pixels.ndim < 3 or pixels.shape[2] < 3:
            return None
        x, y = target
        height, width = pixels.shape[:2]
        x1 = max(0, min(width, x - max(1, radius)))
        x2 = max(0, min(width, x + max(1, radius) + 1))
        y1 = max(0, min(height, y - max(1, radius)))
        y2 = max(0, min(height, y + max(1, radius) + 1))
        if x1 >= x2 or y1 >= y2:
            return None
        return np.asarray(pixels[y1:y2, x1:x2, :3], dtype=np.float32).copy()
    except Exception:
        return None


def _drive_target_changed(before: Any, after: Any, minimum_difference: float) -> bool:
    try:
        import numpy as np

        if before.shape != after.shape:
            return True
        difference = float(np.mean(np.abs(after - before)))
        return difference >= max(0.0, minimum_difference)
    except Exception:
        return False


def _drive_target_looks_occupied(
    image: Any,
    target: tuple[int, int],
    radius: int,
    brightness_threshold: float,
) -> bool:
    return _region_brightness(image, target, radius=max(1, radius)) >= brightness_threshold


def _quality_button_looks_selected(image: Any, position: tuple[int, int], radius: int = 5) -> bool:
    try:
        import numpy as np

        pixels = np.asarray(image)
        if pixels.ndim < 3 or pixels.shape[2] < 3:
            return False
        x, y = position
        height, width = pixels.shape[:2]
        x1 = max(0, min(width, x - radius))
        x2 = max(0, min(width, x + radius + 1))
        y1 = max(0, min(height, y - radius))
        y2 = max(0, min(height, y + radius + 1))
        if x1 >= x2 or y1 >= y2:
            return False
        red, green, blue = np.mean(pixels[y1:y2, x1:x2, :3], axis=(0, 1))
        return bool(red >= green + 35 and blue >= green + 15)
    except Exception:
        return False


def _is_scroll_action(action: dict[str, Any]) -> bool:
    return "scroll" in str(action.get("name") or "").lower()


def _drag_scroll(
    backend: MouseBackend,
    start: tuple[int, int],
    end: tuple[int, int],
    duration_ms: int,
) -> None:
    scroll = getattr(backend, "drag_scroll", None)
    if scroll is not None:
        scroll(start, end, duration_ms)
        return
    backend.drag(start, end, duration_ms)


def _pause_with_stop(
    backend: MouseBackend,
    seconds: float,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    remaining = max(0.0, float(seconds))
    while remaining > 0:
        if should_stop and should_stop():
            raise AssemblyExecutionStopped("assembly execution stopped")
        step = min(STOP_POLL_INTERVAL_SECONDS, remaining)
        backend.pause(step)
        remaining -= step


def _press_gamepad_button(backend: MouseBackend, button_name: str) -> None:
    press = getattr(backend, "press_gamepad_button", None)
    if press is None:
        raise TypeError("backend does not support gamepad button actions")
    press(button_name)


def _press_keyboard_key(backend: MouseBackend, key_name: str) -> None:
    press = getattr(backend, "press_key", None)
    if press is None:
        raise TypeError("backend does not support keyboard key actions")
    press(key_name)


def _push_gamepad_stick(backend: MouseBackend, direction: str) -> None:
    push = getattr(backend, "push_left_joystick", None)
    if push is None:
        raise TypeError("backend does not support gamepad stick actions")
    vectors = {
        "left_down": (0.0, -1.0),
        "left_up": (0.0, 1.0),
        "left_left": (-1.0, 0.0),
        "left_right": (1.0, 0.0),
    }
    key = str(direction).strip().lower()
    if key not in vectors:
        raise ValueError(f"unknown gamepad stick direction: {direction}")
    push(*vectors[key])


from src.features.drive_assembly.assembly_ocr import (
    _click_ocr_target,
    _maybe_click_optional_confirm,
    _region_brightness,
    _point,
)
