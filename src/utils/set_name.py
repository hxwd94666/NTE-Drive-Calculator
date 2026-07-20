# 规范化展示层套装名称，避免外框样式进入核心匹配逻辑。
"""Canonicalize set names at display-to-domain boundaries."""

from __future__ import annotations


_DISPLAY_WRAPPERS = (
    ("「", "」"),
    ("【", "】"),
    ("[", "]"),
)


def normalize_set_display_name(value: object) -> str:
    """Return a set name without an optional presentation wrapper.

    Game UI and imported plans may use ``「套装名」`` for display, whereas
    static data keeps the same official name without those brackets.
    """
    name = str(value or "").strip()
    for left, right in _DISPLAY_WRAPPERS:
        if name.startswith(left) and name.endswith(right) and len(name) > len(left) + len(right):
            return name[len(left):-len(right)].strip()
    return name
