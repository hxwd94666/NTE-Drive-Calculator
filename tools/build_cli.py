# 提供打包脚本共用的命令行交互和日志输出。
"""Shared command-line helpers for build and release scripts."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from tools.release.managed_paths import plain_member_stat


def select_output_root(source_root: Path, requested: Path | None) -> Path:
    """默认输出不变；显式候选仅归 build 子目录，校验联接身份后再创建。"""
    raw = source_root if requested is None else requested.expanduser()
    if ".." in raw.parts:
        raise ValueError("输出根不得包含上级路径。")
    output = raw.absolute()  # 不先 resolve：重解析点必须仍可由 lstat 识别。
    parent = source_root / "build"
    if requested is not None and (output == parent or not output.is_relative_to(parent)):
        raise ValueError("显式输出根只支持 build 下的明确子目录；默认仍为仓库根。")
    try:
        info = plain_member_stat(source_root, output)
    except FileNotFoundError:
        output.mkdir(parents=True, exist_ok=True)
        info = plain_member_stat(source_root, output)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("输出根不是普通目录。")
    return output


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
