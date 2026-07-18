# 按原始游戏 ID 解析随程序提供的轻量界面图片。
"""Resolve packaged lightweight UI images by official character/stat IDs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class GameUiAssetCatalog:
    def __init__(self, asset_root: str | Path) -> None:
        self.asset_root = Path(asset_root).expanduser().resolve()
        manifest_path = self.asset_root / "manifest.json"
        self._manifest: dict[str, Any] = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {"characters": {}, "attributes": {}}
        )

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
