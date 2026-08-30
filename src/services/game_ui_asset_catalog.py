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
                "characters": {}, "attributes": {}, "equipment_items": {},
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
