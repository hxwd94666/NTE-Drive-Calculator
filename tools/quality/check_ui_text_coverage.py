# 扫描可能直接上屏、但未经文本目录渲染或登记的中文字面量，配合遗留基线阻止新增硬编码文案。
"""Ratchet check for UI-visible Chinese literals that bypass the text catalog.

规则只统计“可能上屏”的中文字符串常量；下列情况按约定不计：
- docstring、日志调用参数、`localized_text`/`_t`/`render` 等目录调用的参数，以及标注 `# ui-text: internal` 的内部文本；
- 原文已登记为发行中文目录的值（展示端以 `ui_enum_text` 投影）；
- 只用于比较、成员判断、字典键或下标的原文（业务身份，不直接展示）；
- 正式游戏数据名称（静态库中的名称列）、识别词库与协议/存储/算法内核目录。
遗留缺口记录在同目录基线文件中；任何文件的计数只能下降，新增文件必须为零。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import sys
from collections import Counter
from functools import cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).with_name("ui_text_baseline.json")
SCAN_ROOTS = ("src/app", "src/ui", "src/features", "src/services", "src/domain", "src/integrations", "src/storage",
              "src/optimizer", "src/solver", "src/scanner", "src/utils")
# 识别词库、OCR 映射与按正式名称匹配的模块不属于界面文案。
EXEMPT_PARTS = ("recognition", "vision", "ocr")
EXEMPT_FILES = {
    "src/features/identification/manual_parsing.py",
    "src/features/drive_assembly/page_mapping_helpers.py",
}
CJK = re.compile(r"[一-鿿]")
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log", "success", "trace"}
# 结构化日志与日志面板入口：日志按契约不经界面补丁改写。
LOG_FUNCTIONS = {"log_event", "_on_log", "_log", "log_operation"}
# 这些调用的参数是 key 或兜底原文；`ui_enum_text` 的原文必须已登记，按文本规则判断。
CATALOG_CALLS = {"localized_text", "_t", "render"}
# 内部文本（日志、断言、协议诊断）在所在行显式标注后不计入界面文案。
INTERNAL_MARKER = "ui-text: internal"


@cache
def catalog_texts() -> frozenset[str]:
    data = json.loads((PROJECT_ROOT / "assets/locales/zh-CN.json").read_text(encoding="utf-8"))
    return frozenset(str(value).strip() for value in data["messages"].values())


@cache
def game_names() -> frozenset[str]:
    """静态库名称列中的正式游戏名称不经界面补丁翻译。"""
    path = PROJECT_ROOT / "data/game_static.sqlite3"
    names: set[str] = set()
    if not path.is_file():
        return frozenset()
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [row[0] for row in connection.execute("select name from sqlite_master where type='table'")]
        for table in tables:
            columns = [row[1] for row in connection.execute(f'pragma table_info("{table}")')]
            for column in columns:
                if column.endswith(("name", "name_zh", "name_cn", "title", "title_zh")):
                    for (value,) in connection.execute(f'select distinct "{column}" from "{table}"'):
                        if isinstance(value, str):
                            names.add(value.strip())
    finally:
        connection.close()
    return frozenset(names)


def _call_name(node: ast.Call) -> tuple[str, str]:
    func = node.func
    if isinstance(func, ast.Attribute):
        receiver = func.value
        receiver_name = getattr(receiver, "id", None) or getattr(receiver, "attr", "") or ""
        return func.attr, str(receiver_name)
    return getattr(func, "id", ""), ""


def _exempt_context(node: ast.AST) -> bool:
    child = node
    parent = getattr(node, "_parent", None)
    while parent is not None:
        if isinstance(parent, ast.Expr) and isinstance(parent.value, ast.Constant):
            return True  # docstring 或独立字符串语句
        if isinstance(parent, ast.Compare):
            return True  # 只用于比较/成员判断的业务原文
        if isinstance(parent, ast.Subscript) and child is parent.slice:
            return True
        if isinstance(parent, ast.Dict) and child in parent.keys:
            return True
        if isinstance(parent, ast.Call):
            name, receiver = _call_name(parent)
            if name in CATALOG_CALLS:
                return True
            if name in LOG_METHODS and ("log" in receiver.lower() or receiver in {"", "logging"}):
                return True
            if name in LOG_FUNCTIONS:
                return True
            if name in {"startswith", "endswith", "replace", "split", "find", "index", "count", "get"} and child is not parent.func:
                return True
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            return False
        child, parent = parent, getattr(parent, "_parent", None)
    return False


def _enclosing_statement(node: ast.AST) -> ast.AST:
    """标注写在所属语句的任一行（通常是末行）即可覆盖语句内全部原文。"""
    current = node
    while not isinstance(current, ast.stmt) and getattr(current, "_parent", None) is not None:
        current = current._parent  # type: ignore[attr-defined]  # 扫描期父节点指针
    return current


def _is_exempt_text(value: str) -> bool:
    text = value.strip()
    return text in catalog_texts() or text in game_names()


def scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]  # 仅本次扫描使用的父节点指针
    lines = path.read_text(encoding="utf-8").splitlines()
    hits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and CJK.search(node.value)):
            continue
        if _exempt_context(node):
            continue
        # 标注可写在原文自身所在行，或所属简单语句（非 if/for/with 等复合语句）的任一行。
        span = lines[node.lineno - 1:node.end_lineno or node.lineno]
        statement = _enclosing_statement(node)
        if not hasattr(statement, "body"):
            span += lines[statement.lineno - 1:statement.end_lineno or statement.lineno]
        if any(INTERNAL_MARKER in line for line in span):
            continue
        # f-string 片段无法经原文投影翻译，即使与已登记文案同字也必须改成带参数的 key。
        if not isinstance(getattr(node, "_parent", None), ast.JoinedStr) and _is_exempt_text(node.value):
            continue
        hits.append((node.lineno, node.value))
    return hits


def scan_project(root: Path = PROJECT_ROOT) -> dict[str, list[tuple[int, str]]]:
    result: dict[str, list[tuple[int, str]]] = {}
    for base in SCAN_ROOTS:
        for path in sorted((root / base).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel in EXEMPT_FILES or any(part in EXEMPT_PARTS for part in path.parts):
                continue
            hits = scan_file(path)
            if hits:
                result[rel] = hits
    return result


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.is_file():
        return {}
    return {str(k): int(v) for k, v in json.loads(BASELINE_PATH.read_text(encoding="utf-8")).items()}


def regressions(scan: dict[str, list[tuple[int, str]]], baseline: dict[str, int]) -> list[str]:
    problems = []
    for rel, hits in sorted(scan.items()):
        allowed = baseline.get(rel, 0)
        if len(hits) > allowed:
            sample = "；".join(text[:30] for _, text in hits[:3])
            problems.append(f"{rel}: {len(hits)} > 基线 {allowed}（示例：{sample}）")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-baseline", action="store_true", help="以当前扫描结果重写遗留基线（只应下降）")
    parser.add_argument("--list", action="store_true", help="逐条列出当前缺口")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    scan = scan_project()
    total = sum(len(v) for v in scan.values())
    if args.list:
        for rel, hits in sorted(scan.items()):
            for line, text in hits:
                print(f"{rel}:{line} {text[:60]!r}")
    counts = Counter({rel: len(hits) for rel, hits in scan.items()})
    print(f"files={len(counts)} literals={total}")
    for rel, count in counts.most_common(15):
        print(f"{count:5d} {rel}")
    if args.write_baseline:
        BASELINE_PATH.write_text(json.dumps(dict(sorted(counts.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    problems = regressions(scan, load_baseline())
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
