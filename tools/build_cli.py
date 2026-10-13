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


def artifact_size_bytes(path: Path) -> int:
    """只统计文件逻辑字节；EXE 直接读取大小，目录汇总，不代表分配磁盘空间。"""
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(member.stat().st_size for member in path.rglob("*") if member.is_file())
    raise FileNotFoundError(path)
