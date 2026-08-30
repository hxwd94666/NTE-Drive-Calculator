# 怪物页面正式图片的唯一解析器。
"""Resolve encounter, profile-variant and formal-family monster images."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from src.features.static_catalog.domain_pages.monster_browse_models import (
    profile_parts,
)
from src.features.static_catalog.domain_pages.monster_page_controller import (
    MonsterCatalogPageController,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_monster_models import CatalogDetail, CatalogEntry


class MonsterIconResolver:
    """Follow only formal resource, profile, family and relation identities."""

    def __init__(
        self,
        controller: MonsterCatalogPageController,
        asset_catalog: GameUiAssetCatalog,
    ) -> None:
        self._controller = controller
        self._assets = asset_catalog

    @staticmethod
    def candidates(detail: CatalogDetail) -> tuple[str, ...]:
        candidates = []
        if profile_parts(detail.entry.key):
            candidates.append(detail.entry.key)
        candidates.extend(
            relation.target_key for relation in detail.relations
            if profile_parts(relation.target_key)
        )
        return tuple(dict.fromkeys(candidates))

    def resolve(self, detail: CatalogDetail | None) -> Path | None:
        return self._resolve(detail, set())

    def first(self, entries: Iterable[CatalogEntry]) -> Path | None:
        for entry in entries:
            icon = self.resolve(self._controller.detail(entry.key))
            if icon:
                return icon
        return None

    def _resolve(
        self,
        detail: CatalogDetail | None,
        visited: set[str],
    ) -> Path | None:
        if detail is None or detail.entry.key in visited:
            return None
        visited.add(detail.entry.key)
        if detail.entry.resource_path:
            icon = self._assets.encounter_icon(detail.entry.resource_path)
            if icon:
                return icon
        for candidate in self.candidates(detail):
            parts = profile_parts(candidate)
            if parts:
                icon = self._assets.monster_icon(*parts)
                if icon:
                    return icon
                icon = self._assets.monster_variant_icon(parts[1])
                if icon:
                    return icon
                icon = self._assets.monster_family_icon(parts[1])
                if icon:
                    return icon
        family = self._assets.monster_family_icon(detail.entry.primary_id)
        if family:
            return family
        for relation in detail.relations:
            if not relation.target_key.startswith((
                "manual_monster|", "world_boss|", "profile_monster|", "feast|",
            )):
                continue
            related = self._controller.detail(relation.target_key)
            icon = self._resolve(related, visited)
            if icon:
                return icon
        parts = profile_parts(detail.entry.key)
        if parts:
            for key in self._controller.profile_family_keys(parts[1]):
                icon = self._resolve(self._controller.detail(key), visited)
                if icon:
                    return icon
        return None
