# 解析官方角色详情中的属性展示名称。
"""Official SQLite-only data boundary for the rebuilt character page."""

from __future__ import annotations

from typing import Any, Mapping


def _property_label(detail: Mapping[str, Any], property_id: str) -> str:
    attribute = (detail.get("attributes") or {}).get(property_id) or {}
    return str(
        attribute.get("display_name_zh")
        or attribute.get("filter_name_zh")
        or property_id
    )
