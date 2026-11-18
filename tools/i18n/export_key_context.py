# 导出文本目录的翻译上下文对照表：键、中文原文、命名参数、出处文件与用法，供译者查找每条文案在哪里显示。
"""Export translator context for every catalog key as UTF-8 CSV.

出处判断顺序：
1. 源码直接引用该 key（render / localized_text 等）→ 列出引用文件；
2. 源码中存在与原文完全相同的字符串字面量（显示边界整句投影）→ 列出这些文件；
3. `analysis_core.*` 键来自独立分析组件的返回文案；
4. 都找不到时标为“未定位”，可能是兼容保留的旧 key 或运行时拼装的文字。
默认输出到忽略目录 build/i18n/key_context.csv，不进入仓库。

用法：python tools/i18n/export_key_context.py [--output 路径]
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.domain.localization.catalog import parameter_names  # noqa: E402

ZH = PROJECT_ROOT / "assets/locales/zh-CN.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "build/i18n/key_context.csv"


def _index_sources() -> dict[str, set[str]]:
    """源码中每个字符串字面量（含 key 与整句原文）→ 出现的文件。"""
    by_literal: dict[str, set[str]] = defaultdict(set)
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                by_literal[node.value.strip()].add(rel)
    return by_literal


def export(output: Path) -> int:
    messages = json.loads(ZH.read_text(encoding="utf-8"))["messages"]
    literals = _index_sources()
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["key", "zh_CN", "parameters", "usage", "files"])
        for key, text in sorted(messages.items()):
            params = ",".join(sorted(parameter_names(text)))
            if key in literals:
                usage, files = "key", literals[key]
            elif text.strip() in literals and not params:
                usage, files = "whole_text_projection", literals[text.strip()]
            elif key.startswith("analysis_core."):
                usage, files = "analysis_core_output", set()
            else:
                usage, files = "unlocated", set()
            writer.writerow([key, text, params, usage, ";".join(sorted(files)[:5])])
            rows += 1
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    rows = export(args.output)
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"rows={rows} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
