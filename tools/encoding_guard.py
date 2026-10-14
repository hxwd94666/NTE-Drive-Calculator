# 检查源码 UTF-8 和乱码风险。
"""UTF-8 and mojibake checks for source files."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

MOJIBAKE_MARKERS = (
    "\ufffd",
    "鈥",
    "鈮",
    "鈫",
    "攜",
    "攆",
    "鉁",
)
QUESTION_MARK_MOJIBAKE = re.compile(r"\?{4,}")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "__pycache__"}


def iter_python_sources(paths: Iterable[str | Path]) -> list[Path]:
    """显式文件始终检查；目录以请求根枚举，仅项目根排除根构建产物。"""
    sources: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            for candidate in path.rglob("*.py"):
                if _should_skip(candidate, scan_root=path):
                    continue
                sources.append(candidate)
        elif path.suffix == ".py" and path.is_file():
            sources.append(path)
    return sorted(set(sources))


def find_text_encoding_issues(paths: Iterable[str | Path]) -> list[str]:
    issues: list[str] = []
    for path in iter_python_sources(paths):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            issues.append(f"{path}: invalid UTF-8 at byte {exc.start}")
            continue

        for marker in MOJIBAKE_MARKERS:
            if marker in text:
                issues.append(f"{path}: contains mojibake marker {marker!r}")
                break
        if QUESTION_MARK_MOJIBAKE.search(text):
            issues.append(f"{path}: contains question-mark mojibake")
    return issues


def _should_skip(path: Path, *, scan_root: Path) -> bool:
    # 只看请求根内的精确目录身份，祖先路径与名字子串不改变源码范围。
    directories = tuple(part.casefold() for part in path.relative_to(scan_root).parts[:-1])
    if SKIP_DIRS.intersection(directories):
        return True
    if scan_root.resolve() != PROJECT_ROOT:
        return False
    # 根构建产物不是源码；业务包内同名 build/dist 与 installer/output 仍检查。
    return directories[:1] in {("build",), ("dist",)} or directories[:2] == ("installer", "output")
