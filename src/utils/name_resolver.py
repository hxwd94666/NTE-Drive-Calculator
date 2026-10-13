# 规范化名称并进行近似匹配。
"""Fuzzy name matching helpers for sets, stats, and user input."""

import difflib
import re
import unicodedata
from collections.abc import Iterable


def normalize_name(value: str | None) -> str:
    """仅为查找规范化输入与 OCR 名称，不改持久化显示名或正式 ID。"""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def resolve_name(value: str | None, choices: Iterable[str], cutoff: float = 0.82) -> str | None:
    """优先规范名称精确匹配，再从给定候选中近似匹配；无候选返回 None。"""
    names = [name for name in choices if name]
    if not value or not names:
        return None

    index: dict[str, str] = {}
    for name in names:
        key = normalize_name(name)
        if key and key not in index:
            index[key] = name

    target = normalize_name(value)
    if target in index:
        return index[target]

    matches = difflib.get_close_matches(target, list(index.keys()), n=1, cutoff=cutoff)
    if matches:
        return index[matches[0]]
    return None
