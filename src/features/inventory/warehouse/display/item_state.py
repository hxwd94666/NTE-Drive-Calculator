# 投影仓库卡片的待保存状态，保留原始状态供状态筛选与管理净差异使用。
from collections.abc import Mapping
from typing import Any

from src.ui.common.enum_text import ui_enum_text


def warehouse_item_tags(
    row: Mapping[str, Any],
    *,
    text_catalog=None,
) -> list[tuple[str, str]]:
    """Return the card badges for an item's current, possibly unsaved state."""
    tags: list[tuple[str, str]] = []
    if not row.get("state_known", True):
        return [(ui_enum_text(text_catalog, "状态未知"), "#8b949e")]
    if row.get("discarded"):
        tags.append((ui_enum_text(text_catalog, "已弃置"), "#f85149"))
    if row.get("locked"):
        tags.append((ui_enum_text(text_catalog, "已锁定"), "#d29922"))
    if row.get("equipped"):
        tags.append((ui_enum_text(text_catalog, "已装备"), "#58a6ff"))
    return tags


def warehouse_item_with_state(
    item: Mapping[str, Any],
    target_state: str,
    *,
    text_catalog=None,
) -> dict[str, Any]:
    """Apply a local state edit to one projected card without touching SQLite."""
    if target_state not in {"normal", "locked", "discarded"}:
        raise ValueError(f"未知仓库状态：{target_state}")  # ui-text: internal
    if not item.get("state_known", True):
        raise ValueError("当前库存来源无法读取或修改锁定、弃置状态")
    updated = dict(item)
    for field in ("locked", "discarded", "equipped"):
        updated.setdefault("base_" + field, item.get(field))
    before = "locked" if updated["base_locked"] else "discarded" if updated["base_discarded"] else "normal"
    updated["management_state"] = target_state if target_state != before else "unchanged"
    updated["discarded"] = target_state == "discarded"
    updated["locked"] = target_state == "locked"
    updated["tags"] = warehouse_item_tags(updated, text_catalog=text_catalog)
    updated["search_text"] = " ".join(
        (
            str(updated.get("item_name") or ""),
            str(updated.get("suit_name") or ""),
            str(updated.get("title") or ""),
            str(updated.get("equipped_character_name") or ""),
            *[str(value) for value in updated.get("stats") or []],
            *[tag for tag, _color in updated["tags"]],
        )
    ).casefold()
    return updated
