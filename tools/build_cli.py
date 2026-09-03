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
