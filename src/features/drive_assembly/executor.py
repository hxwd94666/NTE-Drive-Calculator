# 执行游戏内装配计划中的屏幕坐标点击、拖拽和等待动作。
"""Execute drive assembly plans with a mouse backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
import time


DEFAULT_ACTION_PAUSE_SECONDS = 0.18
DEFAULT_DRAG_DURATION_MS = 700


class MouseBackend(Protocol):
    def click(self, position: tuple[int, int]) -> None:
        """Click a screen position."""

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        """Drag from one screen position to another."""

    def pause(self, seconds: float) -> None:
        """Pause between actions."""


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

    return lambda: bool(user32.GetAsyncKeyState(0x7B) & 0x8000)


class PyAutoGuiMouseBackend:
    """Mouse backend powered by pyautogui."""

    def __init__(self):
        import pyautogui

        self._pyautogui = pyautogui
        self._pyautogui.FAILSAFE = True

    def click(self, position: tuple[int, int]) -> None:
        self._pyautogui.click(*position)

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        duration = max(0.0, duration_ms / 1000.0)
        self._pyautogui.moveTo(*start)
        self._pyautogui.dragTo(*end, duration=duration, button="left")

    def pause(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))


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
    for action in sequence:
        if should_stop and should_stop():
            raise AssemblyExecutionStopped("assembly execution stopped")
        if _execute_one_action(action, mouse):
            report.executed_actions += 1
            if pause_seconds > 0:
                mouse.pause(pause_seconds)
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
        mouse.pause(startup_delay_seconds)
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
            if action.get("name") != "run_drive_assembly_for_role":
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


def _execute_one_action(action: dict[str, Any], backend: MouseBackend) -> bool:
    if "wait_seconds" in action:
        backend.pause(float(action.get("wait_seconds") or 0.0))
        return True
    if "position" in action:
        backend.click(_point(action["position"]))
        return True
    if "from" in action and "to" in action:
        backend.drag(
            _point(action["from"]),
            _point(action["to"]),
            int(action.get("duration_ms") or DEFAULT_DRAG_DURATION_MS),
        )
        return True
    return False


def _point(value: Any) -> tuple[int, int]:
    x, y = value
    return int(x), int(y)
