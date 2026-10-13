# 兼容导出游戏资料库跨层只读契约。
"""Feature 的窄导出入口；只读数据契约的唯一实现仍在 Domain。

CatalogLink 是明确的导航动作，不是泛用关联数据；相关事实先在本页展示，
只有用户点击“查看详情”才跨领域，外壳保留可返回的来源历史。
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
    """外壳只记录明确跨领域导航的来源，不扫描或改写子页私有控件。

    领域页继续存活并拥有筛选、选择、滚动和局部详情；返回时只重新显示该领域页。
    """

    domain_key: str
    title: str


@runtime_checkable
class CatalogDomainNavigation(Protocol):
    """领域页提供局部导航端口，外壳维护唯一返回按钮，不另建永久导航栏。

    catalog_back_label 在详情中返回可读目的地，在领域根返回 None；
    进入/退出局部详情时通知外壳，不以页号或私有控件遍历定位。
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
