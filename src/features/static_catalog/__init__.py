# 暴露游戏资料库公共容器。
"""Public composition contracts for the read-only game data catalog."""

from src.features.static_catalog.contracts import (
    CatalogDetail,
    CatalogDomain,
    CatalogField,
    CatalogItem,
    CatalogPage,
    CatalogReference,
    CatalogRelationGroup,
    CatalogRelationPage,
    CatalogSection,
    CatalogValueSource,
    StaticCatalogProvider,
    StaticCatalogRelease,
    StaticCatalogRequest,
)

__all__ = [
    "CatalogDetail",
    "CatalogDomain",
    "CatalogField",
    "CatalogItem",
    "CatalogPage",
    "CatalogReference",
    "CatalogRelationGroup",
    "CatalogRelationPage",
    "CatalogSection",
    "CatalogValueSource",
    "StaticCatalogProvider",
    "StaticCatalogRelease",
    "StaticCatalogRequest",
]
