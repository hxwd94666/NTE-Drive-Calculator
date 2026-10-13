# 共用发行路径的 lstat、重解析点和普通目录树校验，不跟随链接。
from __future__ import annotations

import os
from pathlib import Path
import stat
from collections.abc import Iterator


def plain_path_stat(path: Path) -> os.stat_result:
    """保留词法路径身份；链接及 Windows 重解析点均不作为发行成员。"""
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("受管理发行路径包含符号链接或重解析点。")
    return info


def plain_directory_root(path: Path) -> Path:
    """先检查根及父段，再规范化；不让 resolve 抹去目录联接身份。"""
    absolute = path.absolute()
    for parent in (*reversed(absolute.parents), absolute):
        if not stat.S_ISDIR(plain_path_stat(parent).st_mode):
            raise ValueError("受管理发行路径不是普通目录。")
    return absolute.resolve(strict=True)


def plain_member_stat(root: Path, path: Path) -> os.stat_result:
    """按确切根检查全部父段和叶子；包内重定向也不获得正式成员身份。"""
    current = plain_directory_root(root)
    relative = path.relative_to(current)
    info = plain_path_stat(current)
    for index, part in enumerate(relative.parts):
        current /= part
        info = plain_path_stat(current)
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ValueError("受管理发行成员的父路径不是普通目录。")
    return info


def iter_plain_tree(root: Path) -> Iterator[tuple[Path, os.stat_result]]:
    """有界栈枚举普通文件/目录；不追随链接，调用者决定计量或删除计划。"""
    pending = [plain_directory_root(root)]
    while pending:
        path = pending.pop()
        info = plain_member_stat(root, path)
        if stat.S_ISDIR(info.st_mode):
            pending.extend(sorted(path.iterdir(), reverse=True))
        elif not stat.S_ISREG(info.st_mode):
            raise ValueError("受管理发行目录包含非普通成员。")
        yield path, info
