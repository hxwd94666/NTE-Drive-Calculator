# 提供角色名称按首个有效字拼音首字母排序的纯展示键。
"""Stable A–Z ordering for role pickers without changing character identity."""

from __future__ import annotations
from functools import lru_cache


@lru_cache(maxsize=2048)
def role_name_sort_key(name: str) -> tuple[str, str, str]:
    """仅生成名称排序键；缓存有界，缺拼音依赖回退原文，不改变正式角色身份。"""

    label = str(name).strip()
    meaningful = "".join(char for char in label if char.isalnum())
    first = meaningful[:1]
    try:
        from pypinyin import Style, lazy_pinyin

        initial = lazy_pinyin(first, style=Style.NORMAL)[0][:1].lower() if first else ""
        full = "".join(lazy_pinyin(meaningful, style=Style.NORMAL)).lower()
    except ImportError:
        initial = first.lower()
        full = meaningful.casefold()
    group = initial if "a" <= initial <= "z" else "{"
    return group, full, label.casefold()


__all__ = ["role_name_sort_key"]
