# 为中文名称提供大小写不敏感的原文、全拼和拼音首字母匹配，不依赖界面。
from __future__ import annotations


def match_pinyin(name: str, filt: str) -> bool:
    """共享名称检索规则，缺少拼音依赖时仍保留原文匹配。"""
    if not filt:
        return True
    keyword = filt.lower()
    text = str(name or "").lower()
    if keyword in text:
        return True
    try:
        from pypinyin import Style, lazy_pinyin

        parts = lazy_pinyin(str(name), style=Style.NORMAL)
        if keyword in "".join(parts).lower():
            return True
        return keyword in "".join(part[0] for part in parts if part).lower()
    except ImportError:
        return False
