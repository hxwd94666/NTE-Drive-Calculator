# 执行游戏内装配计划中的屏幕坐标点击、拖拽和等待动作。
"""Execute drive assembly plans with a mouse backend."""

from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import re
from typing import Any, Callable, Protocol
import time

from src.utils.logger import logger


DEFAULT_ACTION_PAUSE_SECONDS = 0.5
DEFAULT_DRAG_DURATION_MS = 700
DEFAULT_CLICK_HOLD_SECONDS = 0.035
STOP_POLL_INTERVAL_SECONDS = 0.05
SENDINPUT_DRAG_HOLD_SECONDS = 0.30
SENDINPUT_DRAG_RELEASE_SECONDS = 0.20
SENDINPUT_DRAG_STEP_SECONDS = 0.012
EQUIPMENT_DRAG_HOLD_SECONDS = 0.35
EQUIPMENT_DRAG_RELEASE_SECONDS = 0.20
MAX_OCR_INPUT_WIDTH = 1200
MAX_OCR_INPUT_HEIGHT = 900

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

_OCR_ENGINE_INSTANCE: Any | None = None
_OCR_ENGINE_FACTORY: Callable[[], Any] | None = None


class MouseBackend(Protocol):
    def click(self, position: tuple[int, int]) -> None:
        """Click a screen position."""

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        """Drag from one screen position to another."""

    def drag_scroll(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        """Perform a filter-panel scroll gesture."""

    def press_gamepad_button(self, button_name: str) -> None:
        """Press a virtual gamepad button."""

    def push_left_joystick(self, x: float, y: float) -> None:
        """Push the virtual left stick once."""

    def pause(self, seconds: float) -> None:
        """Pause between actions."""

    def screenshot(self) -> Any:
        """Capture the current screen for optional UI detection."""


@dataclass
class ActionExecutionReport:
    """Result for one executed action sequence."""

    role_name: str | None = None
    executed_actions: int = 0
    skipped_actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AssemblyExecutionReport:
    """Result for executing a multi-role assembly plan."""

    role_reports: list[ActionExecutionReport] = field(default_factory=list)
    skipped_roles: list[str] = field(default_factory=list)
    navigation_actions: int = 0
    missing_roles: list[str] = field(default_factory=list)
    duplicate_roles: list[dict[str, Any]] = field(default_factory=list)
    unrecognized_roles: list[dict[str, Any]] = field(default_factory=list)
    verification_failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def executed_actions(self) -> int:
        return self.navigation_actions + sum(report.executed_actions for report in self.role_reports)


class AssemblyExecutionStopped(RuntimeError):
    """Raised when the user stops automatic assembly."""


def f12_stop_checker() -> Callable[[], bool]:
    """Return a Windows F12 stop checker suitable for long-running mouse automation."""

    try:
        import ctypes

        user32 = ctypes.windll.user32
    except Exception:
        return lambda: False

    return lambda: bool(user32.GetAsyncKeyState(0x7B) & 0x8001)


class PyAutoGuiMouseBackend:
    """Mouse backend powered by pyautogui."""

    def __init__(self):
        import pyautogui

        self._pyautogui = pyautogui
        self._pyautogui.FAILSAFE = True
        self._send_input = _WindowsSendInputMouseDriver()
        self._gamepad = _VirtualGamepadDriver()
        self._sleeper = time.sleep

    def click(self, position: tuple[int, int]) -> None:
        if self._send_input.available:
            self._send_input.click(position)
            return
        self._pyautogui.mouseUp()
        self._pyautogui.moveTo(*position)
        self._pyautogui.mouseDown()
        time.sleep(DEFAULT_CLICK_HOLD_SECONDS)
        self._pyautogui.mouseUp()

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        # Equipment placement needs the game's original mouse drag behavior.
        # Filter-panel scrolling uses drag_scroll() and remains on SendInput.
        duration = max(0.0, duration_ms / 1000.0)
        sleeper = getattr(self, "_sleeper", time.sleep)

        # Give the game time to recognize that the filtered equipment card was
        # grabbed. Keep the established dragTo movement unchanged afterwards.
        self._pyautogui.mouseUp(button="left")
        self._pyautogui.moveTo(*start)
        sleeper(0.15)
        self._pyautogui.mouseDown(button="left")
        try:
            sleeper(EQUIPMENT_DRAG_HOLD_SECONDS)
            self._pyautogui.dragTo(*end, duration=duration, button="left")
        finally:
            self._pyautogui.mouseUp(button="left")
        sleeper(EQUIPMENT_DRAG_RELEASE_SECONDS)

    def drag_scroll(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        if self._send_input.available:
            self._send_input.drag(start, end, duration_ms)
            return
        self.drag(start, end, duration_ms)

    def press_gamepad_button(self, button_name: str) -> None:
        self._gamepad.press(button_name)

    def push_left_joystick(self, x: float, y: float) -> None:
        self._gamepad.push_left_joystick(x, y)

    def pause(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))

    def screenshot(self) -> Any:
        return self._pyautogui.screenshot()


class _WindowsSendInputMouseDriver:
    """SendInput drag driver that mimics the scanner's long-press swipe pattern."""

    def __init__(
        self,
        user32: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self._sleeper = sleeper
        self._ctypes = None
        self._wintypes = None
        self._input_cls = None
        self._mouse_input_cls = None
        self._user32 = None
        try:
            import ctypes
            import ctypes.wintypes

            self._ctypes = ctypes
            self._wintypes = ctypes.wintypes
            self._user32 = user32 or ctypes.windll.user32
            self._mouse_input_cls, self._input_cls = self._build_structs(ctypes, ctypes.wintypes)
        except Exception:
            self._user32 = None

    @property
    def available(self) -> bool:
        return self._user32 is not None and self._input_cls is not None and self._mouse_input_cls is not None

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        if not self.available:
            raise RuntimeError("SendInput is not available")
        self._move_to(start)
        self._sleeper(0.15)
        self._send(MOUSEEVENTF_LEFTDOWN)
        self._sleeper(SENDINPUT_DRAG_HOLD_SECONDS)
        steps = self._drag_steps(start, end, duration_ms)
        self._move_relative_in_steps(start, end, steps)
        self._sleeper(SENDINPUT_DRAG_RELEASE_SECONDS)
        self._send(MOUSEEVENTF_LEFTUP)
        self._sleeper(SENDINPUT_DRAG_RELEASE_SECONDS)

    def click(self, position: tuple[int, int]) -> None:
        """Click without invoking PyAutoGUI's corner fail-safe check."""

        if not self.available:
            raise RuntimeError("SendInput is not available")
        self._move_to(position)
        self._sleeper(0.05)
        self._send(MOUSEEVENTF_LEFTDOWN)
        self._sleeper(DEFAULT_CLICK_HOLD_SECONDS)
        self._send(MOUSEEVENTF_LEFTUP)

    def _move_relative_in_steps(self, start: tuple[int, int], end: tuple[int, int], steps: int) -> None:
        previous_x, previous_y = start
        total_dx = end[0] - start[0]
        total_dy = end[1] - start[1]
        for index in range(1, steps + 1):
            target_x = start[0] + round(total_dx * index / steps)
            target_y = start[1] + round(total_dy * index / steps)
            dx = target_x - previous_x
            dy = target_y - previous_y
            if dx or dy:
                self._send(MOUSEEVENTF_MOVE, dx, dy)
            previous_x, previous_y = target_x, target_y
            self._sleeper(SENDINPUT_DRAG_STEP_SECONDS)

    def _move_to(self, position: tuple[int, int]) -> None:
        ax, ay = self._abs_coord(position)
        self._send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay)

    def _abs_coord(self, position: tuple[int, int]) -> tuple[int, int]:
        width = max(1, int(self._user32.GetSystemMetrics(0)))
        height = max(1, int(self._user32.GetSystemMetrics(1)))
        x, y = position
        return int(x * 65535 / width), int(y * 65535 / height)

    def _drag_steps(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> int:
        distance = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
        duration_steps = max(1, int(max(1, duration_ms) / 12))
        distance_steps = max(1, int(distance / 18))
        return max(50, min(90, max(duration_steps, distance_steps)))

    def _send(self, flags: int, dx: int = 0, dy: int = 0) -> None:
        mouse_input = self._mouse_input_cls(dx, dy, 0, flags, 0, None)
        input_value = self._input_cls(INPUT_MOUSE, mouse_input)
        self._user32.SendInput(1, self._ctypes.byref(input_value), self._ctypes.sizeof(input_value))

    @staticmethod
    def _build_structs(ctypes_module: Any, wintypes_module: Any) -> tuple[Any, Any]:
        class MouseInput(ctypes_module.Structure):
            _fields_ = [
                ("dx", ctypes_module.c_long),
                ("dy", ctypes_module.c_long),
                ("mouseData", wintypes_module.DWORD),
                ("dwFlags", wintypes_module.DWORD),
                ("time", wintypes_module.DWORD),
                ("dwExtraInfo", ctypes_module.POINTER(ctypes_module.c_ulong)),
            ]

        class Input(ctypes_module.Structure):
            _fields_ = [
                ("type", wintypes_module.DWORD),
                ("mi", MouseInput),
            ]

        return MouseInput, Input


class _VirtualGamepadDriver:
    """Small virtual Xbox gamepad wrapper for role navigation."""

    BUTTON_NAMES = {
        "dpad_up": "XUSB_GAMEPAD_DPAD_UP",
        "dpad_down": "XUSB_GAMEPAD_DPAD_DOWN",
        "dpad_left": "XUSB_GAMEPAD_DPAD_LEFT",
        "dpad_right": "XUSB_GAMEPAD_DPAD_RIGHT",
        "a": "XUSB_GAMEPAD_A",
        "b": "XUSB_GAMEPAD_B",
        "x": "XUSB_GAMEPAD_X",
        "y": "XUSB_GAMEPAD_Y",
    }

    def __init__(
        self,
        hold_seconds: float = 0.08,
        settle_seconds: float = 0.30,
        connect_settle_seconds: float = 0.40,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self._hold_seconds = hold_seconds
        self._settle_seconds = settle_seconds
        self._connect_settle_seconds = connect_settle_seconds
        self._sleeper = sleeper
        self._gamepad = None
        self._buttons = None

    def press(self, button_name: str) -> None:
        self._ensure_connected()
        key = str(button_name).strip().lower()
        attr_name = self.BUTTON_NAMES.get(key)
        if not attr_name:
            raise ValueError(f"unknown gamepad button: {button_name}")
        button = getattr(self._buttons, attr_name)
        self._gamepad.press_button(button=button)
        self._gamepad.update()
        self._sleeper(self._hold_seconds)
        self._gamepad.release_button(button=button)
        self._gamepad.update()
        self._sleeper(self._settle_seconds)

    def push_left_joystick(self, x: float, y: float) -> None:
        self._ensure_connected()
        self._gamepad.left_joystick_float(x_value_float=float(x), y_value_float=float(y))
        self._gamepad.update()
        self._sleeper(self._hold_seconds)
        self._gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        self._gamepad.update()
        self._sleeper(self._settle_seconds)

    def _ensure_connected(self) -> None:
        if self._gamepad is not None:
            return
        import vgamepad as vg

        self._gamepad = vg.VX360Gamepad()
        self._buttons = vg.XUSB_BUTTON
        self._sleeper(self._connect_settle_seconds)


def execute_action_sequence(
    sequence: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    backend: MouseBackend | None = None,
    pause_seconds: float = DEFAULT_ACTION_PAUSE_SECONDS,
    should_stop: Callable[[], bool] | None = None,
    role_name: str | None = None,
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
            if pause_seconds > 0:
                _pause_with_stop(mouse, pause_seconds, should_stop)
        else:
            report.skipped_actions.append(dict(action))
    return report


def execute_role_assembly_plan(
    plan: dict[str, Any],
    backend: MouseBackend | None = None,
    pause_seconds: float = DEFAULT_ACTION_PAUSE_SECONDS,
    should_stop: Callable[[], bool] | None = None,
    startup_delay_seconds: float = 0.0,
    role_verifier: Callable[[str, dict[str, Any]], dict[str, Any] | None] | None = None,
) -> ActionExecutionReport:
    """Execute all install actions for one role plan."""

    role_name = str(plan.get("role_name") or "")
    if not plan.get("available"):
        return ActionExecutionReport(role_name=role_name)
    mouse = backend or PyAutoGuiMouseBackend()
    if startup_delay_seconds > 0:
        _pause_with_stop(mouse, startup_delay_seconds, should_stop)
    combined = _flatten_role_actions(plan.get("actions", []))
    report = execute_action_sequence(
        combined,
        backend=mouse,
        pause_seconds=pause_seconds,
        should_stop=should_stop,
        role_name=role_name,
    )
    if role_verifier is not None:
        role_verifier(role_name, plan)
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
) -> AssemblyExecutionReport:
    """Execute role-list traversal and run the matching assembly plan for each role."""

    mouse = backend or PyAutoGuiMouseBackend()
    role_plans = _role_plan_lookup(assembly_plan)
    report = AssemblyExecutionReport()
    report.missing_roles = list(traversal_plan.get("missing_roles", []) or [])
    report.duplicate_roles = list(traversal_plan.get("duplicates", []) or [])
    report.unrecognized_roles = list(traversal_plan.get("unrecognized", []) or [])
    for step in traversal_plan.get("plans", []):
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
                )
                report.navigation_actions += action_report.executed_actions
                pending_actions = []
            role_name = str(action.get("role_name") or step.get("role_name") or "")
            role_plan = role_plans.get(role_name)
            if role_plan is None:
                if role_name:
                    report.skipped_roles.append(role_name)
                continue
            role_report = execute_role_assembly_plan(
                role_plan,
                backend=mouse,
                pause_seconds=pause_seconds,
                should_stop=should_stop,
            )
            if role_verifier is not None:
                verification = role_verifier(role_name, role_plan)
                if verification and not verification.get("ok", True):
                    report.verification_failures.append({"role_name": role_name, **verification})
            report.role_reports.append(role_report)
        if pending_actions:
            action_report = execute_action_sequence(
                pending_actions,
                backend=mouse,
                pause_seconds=pause_seconds,
                should_stop=should_stop,
                role_name=step.get("role_name"),
            )
            report.navigation_actions += action_report.executed_actions
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
    if "position" in action:
        backend.click(_point(action["position"]))
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
        if _is_scroll_action(action):
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


def _retry_unselected_quality(
    action: dict[str, Any],
    backend: MouseBackend,
    should_stop: Callable[[], bool] | None = None,
) -> bool:
    """Retry one quality button click only when its pink selected state is absent."""

    capture = getattr(backend, "screenshot", None)
    if capture is None:
        return False
    try:
        image = capture()
    except Exception:
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
            logger.info(f"Drive block {action.get('block_id')} install verified by target-image change")
            return True
        logger.warning(f"Drive block {action.get('block_id')} target image unchanged; retrying drag")
    elif _drive_target_looks_occupied(
        image,
        target_point,
        radius=sample_radius,
        brightness_threshold=float(action.get("brightness_threshold") or 80.0),
    ):
        logger.info(f"Drive block {action.get('block_id')} install verified by fallback brightness")
        return True
    else:
        logger.warning(f"Drive block {action.get('block_id')} has no baseline; retrying drag")

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
        return False
    try:
        image = capture()
    except Exception:
        return False
    sample = _drive_target_sample(image, _point(target), int(action.get("sample_radius") or 12))
    if sample is None:
        return False
    runtime_state[_drive_target_state_key(action)] = sample
    logger.info(f"Drive block {action.get('block_id')} target baseline captured")
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


def _click_ocr_target(action: dict[str, Any], backend: MouseBackend) -> bool:
    position = _find_ocr_target_position(action, backend)
    if position is None and action.get("fallback_position"):
        position = _point(action["fallback_position"])
    if position is None:
        return False
    backend.click(position)
    return True


def _find_ocr_target_position(action: dict[str, Any], backend: MouseBackend) -> tuple[int, int] | None:
    capture = getattr(backend, "screenshot", None)
    if capture is None:
        return None
    try:
        image = capture()
    except Exception:
        return None
    region = action.get("ocr_search_region")
    if not region:
        return None
    cropped = _crop_image_region(image, _region(region))
    if cropped is None:
        return None
    try:
        ocr_image, scale_x, scale_y = _prepare_ocr_image(cropped)
        lines = _get_ocr_engine().extract_lines(ocr_image)
    except Exception:
        return None
    target_text = str(action.get("ocr_target_text") or "")
    match = _best_ocr_line_match(lines, target_text)
    if not match:
        return None
    x1, y1, x2, y2 = _region(region)
    bx1, by1, bx2, by2 = match["box"]
    crop_x = int((bx1 + bx2) / 2 / max(scale_x, 0.0001))
    crop_y = int((by1 + by2) / 2 / max(scale_y, 0.0001))
    return (x1 + crop_x, y1 + crop_y)


def _prepare_ocr_image(image: Any) -> tuple[Any, float, float]:
    import numpy as np

    array = np.asarray(image)
    if array.ndim < 2 or array.size == 0:
        return array, 1.0, 1.0
    if array.ndim == 3:
        array = array[..., :3]
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    height, width = array.shape[:2]
    if width <= 0 or height <= 0:
        return array, 1.0, 1.0
    scale = min(1.0, MAX_OCR_INPUT_WIDTH / width, MAX_OCR_INPUT_HEIGHT / height)
    if scale >= 1.0:
        return array, 1.0, 1.0
    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))
    try:
        import cv2

        resized = cv2.resize(array, (new_width, new_height), interpolation=cv2.INTER_AREA)
    except Exception:
        from PIL import Image

        resized = np.asarray(Image.fromarray(array).resize((new_width, new_height)))
    return resized, new_width / width, new_height / height


def _get_ocr_engine() -> Any:
    global _OCR_ENGINE_INSTANCE
    if _OCR_ENGINE_INSTANCE is None:
        if _OCR_ENGINE_FACTORY is not None:
            _OCR_ENGINE_INSTANCE = _OCR_ENGINE_FACTORY()
        else:
            from src.scanner.ocr_engine import OCREngine

            _OCR_ENGINE_INSTANCE = OCREngine()
    return _OCR_ENGINE_INSTANCE


def _best_ocr_line_match(lines: list[dict[str, Any]], target_text: str) -> dict[str, Any] | None:
    normalized_target = _normalize_ocr_match_text(target_text)
    if not normalized_target:
        return None
    best_line: dict[str, Any] | None = None
    best_score = 0.0
    for line in lines:
        text = _normalize_ocr_match_text(line.get("text"))
        if not text:
            continue
        if normalized_target in text or text in normalized_target:
            score = 1.0
        else:
            score = difflib.SequenceMatcher(None, normalized_target, text).ratio()
        if score > best_score:
            best_score = score
            best_line = line
    return best_line if best_score >= 0.55 else None


def _normalize_ocr_match_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("%", "百分比")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _crop_image_region(image: Any, region: tuple[int, int, int, int]) -> Any | None:
    x1, y1, x2, y2 = region
    if x1 >= x2 or y1 >= y2:
        return None
    try:
        if hasattr(image, "crop"):
            return image.crop((x1, y1, x2, y2))
    except Exception:
        return None
    try:
        import numpy as np

        array = np.asarray(image)
        if array.ndim < 2 or array.size == 0:
            return None
        height, width = array.shape[:2]
        x1 = max(0, min(width, x1))
        x2 = max(0, min(width, x2))
        y1 = max(0, min(height, y1))
        y2 = max(0, min(height, y2))
        if x1 >= x2 or y1 >= y2:
            return None
        return array[y1:y2, x1:x2]
    except Exception:
        return None


def _maybe_click_optional_confirm(action: dict[str, Any], backend: MouseBackend) -> bool:
    if not _optional_prompt_visible(action, backend):
        return False
    backend.click(_point(action["optional_confirm_position"]))
    return True


def _optional_prompt_visible(action: dict[str, Any], backend: MouseBackend) -> bool:
    capture = getattr(backend, "screenshot", None)
    if capture is None:
        return False
    try:
        image = capture()
    except Exception:
        return False
    probe = action.get("modal_probe_position")
    if not probe:
        return False
    threshold = float(action.get("brightness_threshold") or 150)
    return _region_brightness(image, _point(probe), radius=28) >= threshold


def _region_brightness(image: Any, center: tuple[int, int], radius: int = 20) -> float:
    x, y = center
    try:
        import numpy as np

        if hasattr(image, "__array__"):
            array = np.asarray(image)
            if array.ndim < 2 or array.size == 0:
                return 0.0
            height, width = array.shape[:2]
            x1, x2 = max(0, x - radius), min(width, x + radius + 1)
            y1, y2 = max(0, y - radius), min(height, y + radius + 1)
            patch = array[y1:y2, x1:x2]
            if patch.size == 0:
                return 0.0
            if patch.ndim == 3:
                patch = patch[..., :3]
            return float(np.mean(patch))
    except Exception:
        pass
    try:
        width, height = image.size
        x1, x2 = max(0, x - radius), min(width, x + radius + 1)
        y1, y2 = max(0, y - radius), min(height, y + radius + 1)
        values: list[float] = []
        for py in range(y1, y2):
            for px in range(x1, x2):
                pixel = image.getpixel((px, py))
                if isinstance(pixel, int):
                    values.append(float(pixel))
                else:
                    channels = pixel[:3]
                    values.append(sum(float(channel) for channel in channels) / len(channels))
        return sum(values) / len(values) if values else 0.0
    except Exception:
        return 0.0


def _point(value: Any) -> tuple[int, int]:
    x, y = value
    return int(x), int(y)


def _region(value: Any) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = value
    return int(x1), int(y1), int(x2), int(y2)
