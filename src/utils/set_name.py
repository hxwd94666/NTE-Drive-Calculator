# 规范化展示层套装名称，避免外框样式进入核心匹配逻辑。
"""Canonicalize set names at display-to-domain boundaries."""

from __future__ import annotations


_DISPLAY_WRAPPERS = (
    ("「", "」"),
    ("【", "】"),
    ("[", "]"),
)


def normalize_set_display_name(value: object) -> str:
    """只去除一层已知展示外框与两端空白，不推断别名或正式套装 ID。

    游戏界面或旧导入可能用「套装名」，核心匹配仍消费同一无外框名称。
    """
    name = str(value or "").strip()
    for left, right in _DISPLAY_WRAPPERS:
        if name.startswith(left) and name.endswith(right) and len(name) > len(left) + len(right):
            return name[len(left):-len(right)].strip()
    return name
