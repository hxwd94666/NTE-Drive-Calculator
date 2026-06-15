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

SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "installer/output",
}


def iter_python_sources(paths: Iterable[str | Path]) -> list[Path]:
    sources: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            for candidate in path.rglob("*.py"):
                if _should_skip(candidate):
                    continue
                sources.append(candidate)
        elif path.suffix == ".py" and path.exists() and not _should_skip(path):
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


def _should_skip(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if parts.intersection({".git", ".venv", "__pycache__", "build", "dist"}):
        return True
    normalized = path.as_posix().lower()
    return any(skip in normalized for skip in SKIP_DIRS)
