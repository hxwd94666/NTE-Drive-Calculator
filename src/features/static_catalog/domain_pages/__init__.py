"""Public factories for game-styled static-catalog domain pages."""

from src.features.static_catalog.domain_pages.character_page import (
    CharacterCatalogPage,
    build_character_catalog_page,
)

__all__ = ["CharacterCatalogPage", "build_character_catalog_page"]
