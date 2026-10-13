# 图形程序入口，负责运行时 DLL 准备和桌面界面启动。
"""桌面程序显式启动入口；普通导入不注册 DLL 或创建账号。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# 保留 DLL 目录句柄直至进程结束，避免函数返回后路径注册被回收。
_runtime_dll_handles: list[object] = []


def _prepare_runtime_dlls() -> None:
    """只在正式启动时注册打包 DLL，且先于 OpenVINO/桌面模块导入。"""
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return
    libraries = Path(sys._MEIPASS) / "openvino" / "libs"
    if libraries.is_dir():
        _runtime_dll_handles.append(os.add_dll_directory(str(libraries)))
        os.environ["OPENVINO_LIB_PATHS"] = str(libraries)


ROOT_DIR = Path(__file__).parent.resolve()
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))


def main() -> None:
    """解析启动选择后再准备运行环境；退役 CLI 不执行桌面初始化。"""
    parser = argparse.ArgumentParser(description="NTE 卡带驱动配装工具")
    parser.add_argument("--cli", action="store_true", help="旧命令行模式（已移除）")
    parser.add_argument("--gui", action="store_true", help="桌面 GUI 模式（默认）")
    parser.add_argument(
        "--ime-compat", action="store_true",
        help="仅本进程禁用输入法以规避第三方 IME 原生崩溃；应用内中文输入将不可用",
    )
    args = parser.parse_args()
    if args.cli:
        parser.error("旧 JSON 命令行分配器已移除，请直接启动桌面 GUI。")

    _prepare_runtime_dlls()
    if args.ime_compat and sys.platform == "win32":
        _enable_ime_compatibility()
    from src.ui.app import run_gui

    run_gui()


def _enable_ime_compatibility() -> None:
    """在 Qt 创建窗口前按进程禁用 TSF/IMM，兼容有问题的输入法。"""

    import ctypes

    imm32 = ctypes.WinDLL("imm32", use_last_error=True)
    disable = imm32.ImmDisableIME
    disable.argtypes = [ctypes.c_ulong]
    disable.restype = ctypes.c_int
    if disable(0xFFFFFFFF):
        return
    code = ctypes.get_last_error()
    ctypes.WinDLL("user32").MessageBoxW(
        None,
        f"输入法兼容模式启动失败（Windows 错误 {code}）。应用尚未启动。",
        "NTE Drive Calc",
        0x10,
    )
    raise SystemExit(1)


if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()
    main()
