# 从独立分析组件源码抽取会上屏的中文文案，登记进 Calc 发行中文目录与长译文示例补丁。
"""Sync user-visible Chinese texts emitted by nte-analysis-core into the Calc catalog.

分析组件直接返回中文说明（战报证据、计算失败原因等），Calc 在显示边界按整句或带参数模板投影翻译。
本工具只读组件源码（不复制源码、不改组件），把其中的中文字符串字面量登记为：
- `analysis_core.text.t<摘要>`：不含占位符的整句；
- `analysis_core.template.t<摘要>`：Rust `format!` 模板，`{}`/`{:.1}`/`{0}`/`{name}` 统一转成命名参数。
多行字符串按行拆分登记；正式游戏名称与已登记的同文不重复登记。组件更新后重新运行即可补齐，
`--check` 只报告缺口并以非零退出，供发布前核对。

用法：python tools/i18n/sync_analysis_core_texts.py --source <analysis-core 目录> [--check]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.domain.localization.catalog import CatalogError, parameter_names  # noqa: E402
from tools.quality.check_ui_text_coverage import game_names  # noqa: E402

ZH = PROJECT_ROOT / "assets/locales/zh-CN.json"
QPS = PROJECT_ROOT / "docs/examples/locales/qps-ploc.json"
STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
CJK = re.compile(r"[一-鿿]")
PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*|\d+)?(?::[^{}]*)?\}")
ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'", "0": "\0", "r": "\r"}
RESERVED = {"key", "catalog", "fallback", "self", "values"}


def _strip_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        in_string, out, index = False, [], 0
        while index < len(line):
            char = line[index]
            if char == '"' and (index == 0 or line[index - 1] != "\\"):
                in_string = not in_string
            if not in_string and line.startswith("//", index):
                break
            out.append(char)
            index += 1
        lines.append("".join(out))
    return "\n".join(lines)


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: ESCAPES.get(m.group(1), m.group(0)), value)


def _template(text: str) -> tuple[str, bool]:
    """Rust 占位符转成目录命名参数；返回 (模板, 是否含参数)。"""
    counter, used = [0], {}

    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in ("{{", "}}"):
            return token
        name = match.group(1)
        if name is None:
            counter[0] += 1
            name = f"v{counter[0]}"
        elif name.isdigit():
            name = f"v{int(name) + 1}"
        elif name in RESERVED:
            name = f"{name}_value"
        used[name] = True
        return "{" + name + "}"

    return PLACEHOLDER.sub(repl, text), bool(used)


def collect(source: Path) -> list[str]:
    texts: list[str] = []
    for path in sorted(source.rglob("*.rs")):
        if "target" in path.parts or "tests" in path.parts or path.name in {"tests.rs"}:
            continue
        text = _strip_comments(path.read_text(encoding="utf-8", errors="ignore"))
        cut = text.find("#[cfg(test)]")
        if cut >= 0:
            text = text[:cut]
        for match in STRING.finditer(text):
            value = _unescape(match.group(1))
            for line in value.split("\n"):
                if CJK.search(line) and line.strip():
                    texts.append(line.strip())
    return texts


def plan(texts: list[str], messages: dict[str, str]) -> dict[str, str]:
    existing = set(messages.values())
    names = game_names()
    entries: dict[str, str] = {}
    for text in texts:
        template, has_params = _template(text)
        try:
            parameter_names(template)
        except CatalogError:
            # 不是 format! 模板的字面花括号（如“∈ {1, 正式层数}”）按字面量转义。
            template, has_params = text.replace("{", "{{").replace("}", "}}"), False
        if template in existing or text in names or template in entries.values():
            continue
        digest = hashlib.sha1(template.encode("utf-8")).hexdigest()[:10]
        kind = "template" if has_params else "text"
        entries[f"analysis_core.{kind}.t{digest}"] = template
    return entries


def _write(path: Path, data: dict) -> None:
    original = path.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in original else "\n"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2).replace("\n", newline) + newline,
                    encoding="utf-8", newline="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path, help="analysis-core crate 目录（含 src/）")
    parser.add_argument("--check", action="store_true", help="只报告未登记文案，不写入目录")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    source = args.source / "src" if (args.source / "src").is_dir() else args.source
    zh = json.loads(ZH.read_text(encoding="utf-8"))
    entries = plan(collect(source), zh["messages"])
    print(f"missing={len(entries)} templates={sum(k.startswith('analysis_core.template') for k in entries)}")
    if args.check:
        return 1 if entries else 0
    qps = json.loads(QPS.read_text(encoding="utf-8"))
    for key, text in entries.items():
        zh["messages"][key] = text
        qps["messages"][key] = f"［{text} · extended］"
    _write(ZH, zh)
    _write(QPS, qps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
