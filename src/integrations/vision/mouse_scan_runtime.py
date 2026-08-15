# 在鼠标视觉扫描接管游戏前执行不发送输入的运行环境预检。
"""No-input runtime preflight for the packaged mouse scan backend."""

from __future__ import annotations

import ctypes
import importlib
from dataclasses import dataclass
from typing import Any, Callable


REQUIRED_MODULES = ("mss", "pyautogui", "cv2", "numpy")
REQUIRED_INPUT_METHODS = ("moveTo", "mouseDown", "mouseUp")
REQUIRED_USER32_APIS = ("mouse_event", "GetForegroundWindow", "GetClientRect", "ClientToScreen")


@dataclass(frozen=True)
class MouseScanRuntimeReport:
    ok: bool
    module_versions: tuple[tuple[str, str], ...]
    failures: tuple[str, ...]


class MouseScanRuntimeError(RuntimeError):
    """Raised before takeover when capture or input dependencies are incomplete."""


def probe_mouse_scan_runtime(
    *,
    module_loader: Callable[[str], Any] = importlib.import_module,
    user32: Any | None = None,
) -> MouseScanRuntimeReport:
    """Load and open capture dependencies without moving, clicking or scrolling."""

    modules: dict[str, Any] = {}
    versions: list[tuple[str, str]] = []
    failures: list[str] = []
    for name in REQUIRED_MODULES:
        try:
            module = module_loader(name)
        except (ImportError, OSError):
            failures.append(f"missing:{name}")
            continue
        modules[name] = module
        versions.append((name, str(getattr(module, "__version__", "unknown"))))

    mss_module = modules.get("mss")
    if mss_module is not None:
        capture = None
        try:
            capture = mss_module.MSS()
        except (AttributeError, OSError, RuntimeError):
            failures.append("capture:mss")
        finally:
            close = getattr(capture, "close", None)
            if callable(close):
                close()

    pyautogui = modules.get("pyautogui")
    if pyautogui is not None:
        for method in REQUIRED_INPUT_METHODS:
            if not callable(getattr(pyautogui, method, None)):
                failures.append(f"input:pyautogui.{method}")

    if user32 is None:
        windll = getattr(ctypes, "windll", None)
        user32 = getattr(windll, "user32", None)
    for api in REQUIRED_USER32_APIS:
        if not callable(getattr(user32, api, None)):
            failures.append(f"win32:user32.{api}")

    return MouseScanRuntimeReport(
        ok=not failures,
        module_versions=tuple(versions),
        failures=tuple(failures),
    )


def require_mouse_scan_runtime(
    report: MouseScanRuntimeReport | None = None,
) -> MouseScanRuntimeReport:
    checked = report or probe_mouse_scan_runtime()
    if not checked.ok:
        raise MouseScanRuntimeError("鼠标扫描运行环境预检失败：" + "、".join(checked.failures))
    return checked
