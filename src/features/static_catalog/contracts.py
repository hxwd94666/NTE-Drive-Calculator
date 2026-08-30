# 兼容导出游戏资料库跨层只读契约。
"""Feature-local import surface; canonical contracts live in the domain layer.

``CatalogLink`` is an explicit navigation action, not a generic relation DTO.
Player-facing pages render useful related facts in place first and only emit a
link from a clearly labelled "查看详情" action.  This keeps ordinary browsing
inside the current page while still allowing the catalog shell to provide a
reversible cross-domain history for deliberate drill-downs.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.domain.static_catalog import (
    GLOBAL_DOMAIN_KEY,
    SOURCE_LABELS,
    CatalogDetail,
    CatalogDomain,
    CatalogField,
    CatalogItem,
    CatalogLink,
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


@dataclass(frozen=True, slots=True)
class CatalogNavigationEntry:
    """One shell-level origin retained for an explicit cross-domain drill-down.

    The domain widget itself remains alive, so its filters, selection, scroll
    position and nested detail state stay owned by that widget.  The shell only
    remembers which domain to reveal again; it never scans or mutates private
    child controls.
    """

    domain_key: str
    title: str


@runtime_checkable
class CatalogDomainNavigation(Protocol):
    """Public nested-navigation port implemented by a dedicated domain page.

    A domain page uses this instead of drawing a second permanent back bar.
    ``catalog_back_label`` returns the human-readable local destination while a
    detail is open (for example ``角色列表``), or ``None`` at the domain root.
    The listener lets the shared shell update its one back button whenever the
    page enters or leaves a nested view.
    """

    def set_catalog_navigation_listener(
        self,
        listener: Callable[[], None],
    ) -> None: ...

    def catalog_back_label(self) -> str | None: ...

    def catalog_go_back(self) -> bool: ...

__all__ = [
    "GLOBAL_DOMAIN_KEY",
    "SOURCE_LABELS",
    "CatalogDetail",
    "CatalogDomain",
    "CatalogField",
    "CatalogItem",
    "CatalogLink",
    "CatalogNavigationEntry",
    "CatalogDomainNavigation",
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
