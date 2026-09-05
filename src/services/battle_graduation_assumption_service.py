# 为缺少原生背包的战报冻结毕业模板装备，不生成原生库存身份。
from __future__ import annotations

from typing import Any

from src.services.battle_build_equipment_service import freeze_equipment_context
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


def freeze_graduation_assumptions(
    static_dao: StaticGameDataDao,
    profiles: dict[int, dict[str, Any]],
    character_ids: tuple[int, ...],
) -> None:
    for character_id in character_ids:
        profile = profiles.get(character_id)
        if profile is None:
            continue
        template = static_dao.get_character_graduation_template(character_id) or {}
        items = freeze_equipment_context({"items": template.get("equipment") or []})
        suit_shapes = sorted({
            str(shape_id)
            for item in items if item.get("kind") == "core" and item.get("suit_id")
            for shape_id in (static_dao.get_suit(item["suit_id"]) or {}).get("required_shape_ids") or ()
        })
        for ordinal, item in enumerate(items, start=1):
            # Negative local identities distinguish aggregate template cards only.
            # They are never inventory rows or eligible for native equipment actions.
            item.update(uid_slot=-character_id, uid_serial=-ordinal)
            if item.get("kind") == "module":
                item["graduation_assumed_shape_ids"] = suit_shapes
        profile["equipment_assumption"] = {
            "kind": "official_graduation", "version": 1,
            "reason": "missing_complete_native_inventory", "items": items,
        }
