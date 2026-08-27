# 冻结并应用战报角色修改副本所选的空幕与驱动上下文。
"""Battle-edit equipment projection helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


_ITEM_FIELDS = (
    "kind",
    "item_id",
    "suit_id",
    "geometry",
    "grid_count",
    "quality",
    "level",
    "max_level",
    "locked",
    "target_row",
    "target_column",
    "names",
    "suit_names",
)


def _uid(item: Mapping[str, Any]) -> tuple[int, int]:
    raw_uid = item.get("uid")
    if isinstance(raw_uid, Mapping):
        return int(raw_uid.get("slot") or 0), int(raw_uid.get("serial") or 0)
    return int(item.get("uid_slot") or 0), int(item.get("uid_serial") or 0)


def _stat_rows(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    if "main_stats" in item or "sub_stats" in item:
        sources = (
            ("main", item.get("main_stats") or ()),
            ("sub", item.get("sub_stats") or ()),
        )
    else:
        grouped: dict[str, list[Mapping[str, Any]]] = {"main": [], "sub": []}
        for raw in item.get("stats") or ():
            if not isinstance(raw, Mapping):
                continue
            group = str(raw.get("stat_group") or "sub")
            if group in grouped:
                grouped[group].append(raw)
        sources = (("main", grouped["main"]), ("sub", grouped["sub"]))
    result: list[dict[str, Any]] = []
    for group, rows in sources:
        for ordinal, raw in enumerate(rows):
            if not isinstance(raw, Mapping):
                continue
            property_id = str(raw.get("property_id") or "").strip()
            if not property_id:
                continue
            result.append(
                {
                    "stat_group": group,
                    "ordinal": ordinal,
                    "property_id": property_id,
                    "value": float(raw.get("value") or 0.0),
                    "is_percent": bool(
                        raw.get("is_percent", raw.get("percent", False))
                    ),
                    "names": dict(raw.get("names") or {}),
                }
            )
    return result


def battle_equipment_items(character: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project battle snapshot rows into the role-page equipment-card shape."""

    items: list[dict[str, Any]] = []
    for equipment in character.get("equipment") or ():
        row = dict(equipment)
        stats = [dict(stat) for stat in row.get("stats") or ()]
        row["main_stats"] = [
            stat for stat in stats if stat.get("stat_group") == "main"
        ]
        row["sub_stats"] = [
            stat for stat in stats if stat.get("stat_group") == "sub"
        ]
        row["level_known"] = True
        items.append(row)
    return items


def freeze_equipment_context(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Copy one role-page context into pointer-free battle equipment rows."""

    raw_items = list(context.get("items") or ())
    calculation_items = list(context.get("calculation_items") or ())
    raw_uids = {_uid(row) for row in raw_items if isinstance(row, Mapping)}
    calculation_uids = {
        _uid(row) for row in calculation_items if isinstance(row, Mapping)
    }
    sources = (
        calculation_items
        if calculation_items and calculation_uids == raw_uids
        else raw_items
    )
    frozen: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, Mapping):
            raise ValueError("配装上下文包含无效装备")
        uid_slot, uid_serial = _uid(source)
        row = {
            field: source.get(field)
            for field in _ITEM_FIELDS
            if field in source
        }
        # Inventory cores legitimately have no occupied-grid count.  Battle edit
        # copies use one pointer-free shape for cores and modules, where the
        # persisted canonical value is 0 for a core instead of SQLite/JSON null.
        # Keep module values untouched so missing or malformed drive geometry is
        # still rejected by the persistence boundary.
        if row.get("kind") == "core" and row.get("grid_count") is None:
            row["grid_count"] = 0
        row.update(
            {
                "uid_slot": uid_slot,
                "uid_serial": uid_serial,
                "stats": _stat_rows(source),
            }
        )
        frozen.append(row)
    return frozen


def apply_equipment_override(
    character: dict[str, Any],
    profile: Mapping[str, Any],
) -> bool:
    """Apply an explicitly frozen override and report whether one was present."""

    if "equipment_override" not in profile:
        return False
    raw = profile.get("equipment_override")
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return False
    character["equipment"] = [dict(row) for row in raw if isinstance(row, Mapping)]
    character["equipment_context_title"] = str(
        profile.get("equipment_context_title") or "修改副本配装"
    )
    character["equipment_source_kind"] = str(
        profile.get("equipment_source_kind") or "edited_copy"
    )
    return True
