# 提供打包脚本共用的命令行交互和日志输出。
"""Shared command-line helpers for build and release scripts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def configure_utf8_console() -> None:
    """Use UTF-8 for Chinese build output on Windows legacy code pages."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass


configure_utf8_console()


def running_in_automation() -> bool:
    if os.environ.get("CI") or os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return os.environ.get("NTE_BUILD_NONINTERACTIVE") == "1"


def info(message: str) -> None:
    print(message)


def ok(message: str) -> None:
    print(f"[OK] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def fail(message: str) -> None:
    print(f"[FAIL] {message}")


def skip(message: str) -> None:
    print(f"[SKIP] {message}")


def run(cmd: list[str], cwd: Path) -> None:
    print("[RUN]", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd), check=True)


def choose_build_mode(
    *,
    skip_workshop_sync: bool = False,
    require_workshop_sync: bool = False,
    has_explicit_choice: bool = False,
) -> tuple[bool, bool]:
    if has_explicit_choice or skip_workshop_sync or require_workshop_sync:
        return skip_workshop_sync, require_workshop_sync
    if running_in_automation():
        return True, False

    info("\n请选择打包模式：")
    info("1. 普通模式（有 Key 同步，无 Key 继承发行备份）")
    info("2. 开发者模式（缺 Key 时允许手动输入，留空则继承备份）")
    try:
        choice = input("请输入 1 或 2，直接回车默认为 1: ").strip()
    except EOFError:
        choice = "1"
    if choice != "2":
        return False, False
    return False, True
