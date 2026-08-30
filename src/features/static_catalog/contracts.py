# 兼容导出游戏资料库跨层只读契约。
"""Feature-local import surface; canonical contracts live in the domain layer."""

from src.domain.static_catalog import (
    GLOBAL_DOMAIN_KEY,
    SOURCE_LABELS,
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
    "GLOBAL_DOMAIN_KEY",
    "SOURCE_LABELS",
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
