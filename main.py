# 图形程序入口，负责运行时 DLL 准备和桌面界面启动。
"""Packaged-runtime bootstrap for the NTE Drive Calc desktop application."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# 打包环境下优先注册 OpenVINO DLL 路径，必须在 import openvino 之前。
if getattr(sys, "frozen", False) and sys.platform == "win32":
    _meipass = Path(sys._MEIPASS)
    _openvino_libraries = _meipass / "openvino" / "libs"
    if _openvino_libraries.is_dir():
        os.add_dll_directory(str(_openvino_libraries))
        os.environ["OPENVINO_LIB_PATHS"] = str(_openvino_libraries)


ROOT_DIR = Path(__file__).parent.resolve()
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))


def main() -> None:
    """Start the supported desktop GUI; the legacy JSON CLI is retired."""
    parser = argparse.ArgumentParser(description="NTE 卡带驱动配装工具")
    parser.add_argument("--cli", action="store_true", help="旧命令行模式（已移除）")
    parser.add_argument("--gui", action="store_true", help="桌面 GUI 模式（默认）")
    args = parser.parse_args()
    if args.cli:
        parser.error("旧 JSON 命令行分配器已移除，请直接启动桌面 GUI。")

    from src.ui.app import run_gui

    run_gui()


if __name__ == "__main__":
    main()
