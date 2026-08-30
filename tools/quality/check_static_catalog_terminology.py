# 检查游戏资料库玩家层是否重新引入已禁止的自创术语。
"""Fail when player-facing static-catalog strings use forbidden terminology."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGETS = (
    PROJECT_ROOT / "src" / "features" / "static_catalog",
    PROJECT_ROOT / "src" / "services",
)
FORBIDDEN_TERMS = {
    "金币": "按 canonical item/context 显示方斯或甲硬币",
    "精炼": "弧盘正式术语为混频",
    "金色品质": "使用正式 S 级或橙色品质",
}
PRIVATE_TERM_PAIRS = {
    ("gold", "方斯"): "成本 token 必须经 progression_cost alias 查询",
    ("Fons", "方斯"): "canonical item 名称必须来自正式 StringTable",
    ("Gold", "甲硬币"): "canonical item 名称必须来自正式 StringTable",
    ("ORANGE", "S"): "品质等级必须来自正式 ItemQuality 目录",
    ("PURPLE", "A"): "品质等级必须来自正式 ItemQuality 目录",
    ("BLUE", "B"): "品质等级必须来自正式 ItemQuality 目录",
}


@dataclass(frozen=True, slots=True)
class TerminologyViolation:
    path: Path
    line: int
    term: str
    guidance: str


def _source_files(targets: Iterable[Path]) -> tuple[Path, ...]:
    files: set[Path] = set()
    for target in targets:
        if target.is_file() and target.suffix == ".py":
            files.add(target.resolve())
            continue
        if target.name == "services":
            files.update(path.resolve() for path in target.glob("static_catalog_*.py"))
            continue
        files.update(path.resolve() for path in target.rglob("*.py"))
    return tuple(sorted(files))


def scan_files(targets: Iterable[Path]) -> tuple[TerminologyViolation, ...]:
    violations: list[TerminologyViolation] = []
    for path in _source_files(targets):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                pairs = {
                    (key.value, value.value)
                    for key, value in zip(node.keys, node.values, strict=True)
                    if isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                }
                for pair in sorted(pairs & PRIVATE_TERM_PAIRS.keys()):
                    violations.append(
                        TerminologyViolation(
                            path,
                            node.lineno,
                            f"{pair[0]} -> {pair[1]}",
                            PRIVATE_TERM_PAIRS[pair],
                        )
                    )
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            for term, guidance in FORBIDDEN_TERMS.items():
                if term in node.value:
                    violations.append(
                        TerminologyViolation(path, node.lineno, term, guidance)
                    )
    return tuple(
        sorted(violations, key=lambda row: (str(row.path), row.line, row.term))
    )


def _relative(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _configure_utf8_output() -> None:
    """Emit deterministic UTF-8 even when Windows inherited a GBK console."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_output()
    parser = argparse.ArgumentParser(
        description="检查游戏资料库玩家层的正式术语门禁。"
    )
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    targets = tuple(path.resolve() for path in args.paths) or DEFAULT_TARGETS
    violations = scan_files(targets)
    if not violations:
        print("static-catalog terminology gate: OK")
        return 0
    for row in violations:
        print(f"{_relative(row.path)}:{row.line}: {row.term} -> {row.guidance}")
    print(f"static-catalog terminology gate: {len(violations)} violation(s)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
