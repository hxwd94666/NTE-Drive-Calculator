# 按原始游戏 ID 解析随程序提供的轻量界面图片。
"""Resolve packaged lightweight UI images by official character/stat IDs."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


class GameUiAssetCatalog:
    def __init__(self, asset_root: str | Path) -> None:
        self.asset_root = Path(asset_root).expanduser().resolve()
        manifest_path = self.asset_root / "manifest.json"
        self._manifest: dict[str, Any] = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {
                "characters": {}, "character_arts": {}, "attributes": {},
                "equipment_items": {},
                "equipment_modules": {}, "fork_items": {}, "monster_icons": {},
                "encounter_icons": {}, "monster_family_icons": {},
            }
        )

    @lru_cache(maxsize=2048)
    def _resolve(self, group: str, key: str) -> Path | None:
        relative = self._manifest.get(group, {}).get(key)
        if not isinstance(relative, str):
            return None
        path = (self.asset_root / relative).resolve()
        if self.asset_root != path and self.asset_root not in path.parents:
            return None
        return path if path.is_file() else None

    def character_icon(self, character_id: int) -> Path | None:
        return self._resolve("characters", str(character_id))

    def character_art(self, character_id: int) -> Path | None:
        """Return the packaged default appearance art for a playable identity."""
        return self._resolve("character_arts", str(character_id))

    def attribute_icon(self, attribute_key: str) -> Path | None:
        return self._resolve("attributes", str(attribute_key))

    def equipment_icon(self, item_id: str) -> Path | None:
        return self._resolve("equipment_items", str(item_id))

    def module_icon(self, item_id: str) -> Path | None:
        return self._resolve("equipment_modules", str(item_id))

    def inventory_item_icon(self, kind: str, item_id: str) -> Path | None:
        if str(kind) == "core":
            return self.equipment_icon(item_id)
        if str(kind) == "module":
            return self.module_icon(item_id)
        return None

    def fork_icon(self, fork_id: str) -> Path | None:
        return self._resolve("fork_items", str(fork_id))

    def monster_icon(self, static_table: str, monster_id: str) -> Path | None:
        return self._resolve("monster_icons", f"{static_table}:{monster_id}")

    def monster_variant_icon(self, formal_monster_id: str) -> Path | None:
        """Resolve a unique packaged variant from the same formal ID family."""

        wanted = _monster_identity_parts(formal_monster_id)
        if wanted is None:
            return None
        matches = []
        icon_map = self._manifest.get("monster_icons", {})
        if not isinstance(icon_map, dict):
            return None
        for key, relative in icon_map.items():
            if not isinstance(key, str) or not isinstance(relative, str):
                continue
            candidate_id = key.split(":", 1)[-1]
            candidate = _monster_identity_parts(candidate_id)
            if candidate is None or candidate[:2] != wanted[:2]:
                continue
            shared = 2
            for left, right in zip(wanted[2], candidate[2]):
                if left != right:
                    break
                shared += 1
            matches.append((shared, key, relative))
        if not matches:
            return None
        best_score = max(score for score, _key, _relative in matches)
        preferred = [row for row in matches if row[0] == best_score]
        if len({relative for _score, _key, relative in preferred}) != 1:
            return None
        return self._resolve("monster_icons", preferred[0][1])

    def encounter_icon(self, resource_path: str) -> Path | None:
        return self._resolve("encounter_icons", str(resource_path))

    def monster_family_icon(self, formal_monster_id: str) -> Path | None:
        normalized = str(formal_monster_id).strip().casefold()
        family_map = self._manifest.get("monster_family_icons", {})
        if not isinstance(family_map, dict):
            return None
        matches = [
            key for key in family_map
            if isinstance(key, str)
            and (normalized == key or normalized.startswith(f"{key}_"))
        ]
        if not matches:
            return None
        return self._resolve("monster_family_icons", max(matches, key=len))


def _monster_identity_parts(
    formal_monster_id: str,
) -> tuple[str, int, tuple[str, ...]] | None:
    parts = tuple(
        part for part in str(formal_monster_id).strip().casefold().split("_")
        if part
    )
    if len(parts) < 2 or parts[0] not in {"mon", "boss"}:
        return None
    try:
        ordinal = int(parts[1])
    except ValueError:
        return None
    return parts[0], ordinal, parts
