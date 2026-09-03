# 汇总游戏资料库领域页面的公开入口。
"""Public factories for game-styled static-catalog domain pages."""

from src.features.static_catalog.domain_pages.character_page import (
    CharacterCatalogPage,
    build_character_catalog_page,
)

__all__ = ["CharacterCatalogPage", "build_character_catalog_page"]
