# 验证输入法兼容模式在桌面窗口启动前调用 Windows 进程级入口。
"""Bootstrap contract for the opt-in IME compatibility mode."""

from __future__ import annotations

import ctypes

import main


class _Disable:
    argtypes = None
    restype = None

    def __init__(self) -> None:
        self.values: list[int] = []

    def __call__(self, value: int) -> int:
        self.values.append(value)
        return 1


def test_ime_compat_uses_process_scope_before_gui_import(monkeypatch) -> None:
    disable = _Disable()

    class Imm32:
        ImmDisableIME = disable

    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: Imm32, raising=False)
    main._enable_ime_compatibility()
    assert disable.values == [0xFFFFFFFF]
