# 编排游戏资料库公共搜索与详情请求。
"""Read-only catalog orchestration over independently owned domain providers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from src.domain.static_catalog import (
    GLOBAL_DOMAIN_KEY,
    CatalogDetail,
    CatalogPage,
    CatalogRelationPage,
    StaticCatalogProvider,
    StaticCatalogRequest,
)
from src.integrations.static_catalog_release import StaticCatalogReleaseReader


class StaticCatalogServiceError(RuntimeError):
    """A catalog request could not be routed through the frozen release."""


class StaticCatalogService:
    """Freeze release identity and route bounded queries to narrow providers."""

    MAX_PAGE_SIZE = 100

    def __init__(
        self,
        *,
        static_database_path: str | Path,
        providers: Iterable[StaticCatalogProvider],
        release_reader: StaticCatalogReleaseReader | None = None,
    ) -> None:
        self._database_path = Path(static_database_path).resolve()
        self._release_reader = release_reader or StaticCatalogReleaseReader()
        provider_map = {provider.domain.key: provider for provider in providers}
        if GLOBAL_DOMAIN_KEY in provider_map:
            raise ValueError(f"领域 key {GLOBAL_DOMAIN_KEY!r} 保留给全局搜索")
        self._providers = provider_map

    def start_request(self) -> StaticCatalogRequest:
        release = self._release_reader.freeze(self._database_path)
        domains = tuple(
            sorted((provider.domain for provider in self._providers.values()), key=lambda item: item.order)
        )
        return StaticCatalogRequest(release=release, domains=domains)

    def search(
        self,
        request: StaticCatalogRequest,
        *,
        domain_key: str,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        self._release_reader.ensure_unchanged(request.release)
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(self.MAX_PAGE_SIZE, int(limit)))
        normalized_query = query.strip()
        if domain_key != GLOBAL_DOMAIN_KEY:
            provider = self._providers.get(domain_key)
            if provider is None:
                raise StaticCatalogServiceError(f"未知资料领域：{domain_key}")
            return provider.search(
                request.release,
                query=normalized_query,
                offset=safe_offset,
                limit=safe_limit,
            )
        requested = safe_offset + safe_limit
        pages = [
            provider.search(
                request.release,
                query=normalized_query,
                offset=0,
                limit=requested,
            )
            for provider in self._providers.values()
        ]
        merged = sorted(
            (item for page in pages for item in page.items),
            key=lambda item: (item.title.casefold(), item.domain_key, item.record_id),
        )
        return CatalogPage(
            items=tuple(merged[safe_offset:requested]),
            total=sum(page.total for page in pages),
            offset=safe_offset,
            limit=safe_limit,
        )

    def detail(
        self,
        request: StaticCatalogRequest,
        *,
        domain_key: str,
        record_id: str,
    ) -> CatalogDetail | None:
        self._release_reader.ensure_unchanged(request.release)
        provider = self._providers.get(domain_key)
        if provider is None:
            raise StaticCatalogServiceError(f"未知资料领域：{domain_key}")
        return provider.detail(request.release, str(record_id))

    def relations(
        self,
        request: StaticCatalogRequest,
        *,
        domain_key: str,
        record_id: str,
        relation_kind: str,
        offset: int,
        limit: int,
    ) -> CatalogRelationPage:
        """Route an optional high-cardinality relation page through its owner."""

        self._release_reader.ensure_unchanged(request.release)
        provider = self._providers.get(domain_key)
        loader = getattr(provider, "relations", None)
        if not callable(loader):
            raise StaticCatalogServiceError(f"资料领域不支持分页关系：{domain_key}")
        return loader(
            request.release,
            str(record_id),
            str(relation_kind),
            offset=max(0, int(offset)),
            limit=max(1, min(self.MAX_PAGE_SIZE, int(limit))),
        )

    def close(self) -> None:
        """Release provider-owned read-only handles at application shutdown."""

        errors: list[Exception] = []
        for provider in self._providers.values():
            try:
                provider.close()
            except Exception as exc:  # provider boundaries must all get a close chance
                errors.append(exc)
        if errors:
            raise StaticCatalogServiceError(
                f"关闭游戏资料库时有 {len(errors)} 个数据提供器失败"
            ) from errors[0]
