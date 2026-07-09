# 提供打包脚本共用的命令行交互和日志输出。
"""Shared command-line helpers for build and release scripts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


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
    info("1. 普通模式")
    info("2. 开发者模式")
    try:
        choice = input("请输入 1 或 2，直接回车默认为 1: ").strip()
    except EOFError:
        choice = "1"
    if choice != "2":
        return True, False
    return False, True


def choose_missing_api_key_action() -> str:
    info("\n未在 .env 或环境变量中找到 WORKSHOP_API_KEY。")
    info("1. 手动输入")
    info("2. 进入普通模式（跳过权重同步）")
    try:
        choice = input("请输入 1 或 2，直接回车默认为 2: ").strip()
    except EOFError:
        choice = "2"
    return "manual" if choice == "1" else "normal"
