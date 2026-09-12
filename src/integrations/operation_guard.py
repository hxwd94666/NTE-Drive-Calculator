# 在真实外部操作前复核显式注入的工作模式授权。
"""Execution guards have no global state or implicit permission fallback."""

from collections.abc import Callable
from concurrent.futures import CancelledError


OperationGuard = Callable[[str], None]


def require_operation(guard: OperationGuard | None, capability: str) -> None:
    """Fail closed when a composition root has not supplied authorization."""
    if guard is None:
        raise PermissionError("未配置工作模式执行授权，请重新检查工作模式。")
    guard(capability)


def bind_stop_guard(guard: OperationGuard | None, should_stop: Callable[[], bool]) -> OperationGuard:
    """Keep input releases separate; only new operations consume this guard."""
    def require(capability: str) -> None:
        if should_stop():
            raise CancelledError("本次游戏操作已停止。")
        require_operation(guard, capability)
        if should_stop():
            raise CancelledError("本次游戏操作已停止。")
    return require


def bind_execution_guard(
    guard: OperationGuard | None, *, should_stop: Callable[[], bool],
    generation: Callable[[], object] | None,
) -> OperationGuard:
    """Freeze the explicit mode/account generation when creating an input job."""
    expected = generation() if generation is not None else None

    def require(capability: str) -> None:
        if generation is None:
            raise PermissionError("缺少本次游戏操作的运行代次，请重新检查工作模式。")
        if generation() != expected:
            raise CancelledError("工作模式或账号已变更，本次游戏操作已停止。")
        require_operation(guard, capability)
        if generation() != expected:
            raise CancelledError("工作模式或账号已变更，本次游戏操作已停止。")
    return bind_stop_guard(require, should_stop)


class GuardedGuiInput:
    """Recheck permission at the actual input call, after any random delay."""

    def __init__(self, driver, guard: OperationGuard | None):
        self.driver = driver
        self.guard = guard

    def moveTo(self, *args, **kwargs):
        require_operation(self.guard, "interface_input")
        return self.driver.moveTo(*args, **kwargs)

    def mouseDown(self, *args, **kwargs):
        require_operation(self.guard, "interface_input")
        return self.driver.mouseDown(*args, **kwargs)

    def scroll(self, *args, **kwargs):
        require_operation(self.guard, "interface_input")
        return self.driver.scroll(*args, **kwargs)

    def press(self, *args, **kwargs):
        require_operation(self.guard, "interface_input")
        return self.driver.press(*args, **kwargs)

    def mouseUp(self, *args, **kwargs):
        try:
            return self.driver.mouseUp(*args, **kwargs)
        except Exception as exc:
            failsafe_type = getattr(self.driver, "FailSafeException", None)
            if not isinstance(failsafe_type, type) or not isinstance(exc, failsafe_type):
                raise
            if args or kwargs.get("button", "left") != "left":
                raise
            # PyAutoGUI's corner fail-safe must stop new input, not its release.
            import ctypes

            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)

    def position(self):
        return self.driver.position()

    def screenshot(self):
        return self.driver.screenshot()
