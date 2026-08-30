# 管理游戏资料库页面的冻结请求与分页状态。
"""Qt-free controller for the read-only static catalog page."""

from __future__ import annotations

from src.features.static_catalog.contracts import (
    GLOBAL_DOMAIN_KEY,
    CatalogDetail,
    CatalogPage,
    CatalogRelationPage,
    StaticCatalogRequest,
)
from src.services.static_catalog_service import StaticCatalogService


class StaticCatalogController:
    PAGE_SIZE = 50

    def __init__(self, service: StaticCatalogService) -> None:
        self._service = service
        self._request: StaticCatalogRequest | None = None

    @property
    def request(self) -> StaticCatalogRequest:
        if self._request is None:
            self._request = self._service.start_request()
        return self._request

    def refresh_release(self) -> StaticCatalogRequest:
        self._request = self._service.start_request()
        return self._request

    def search(self, *, domain_key: str, query: str, offset: int = 0) -> CatalogPage:
        return self._service.search(
            self.request,
            domain_key=domain_key or GLOBAL_DOMAIN_KEY,
            query=query,
            offset=offset,
            limit=self.PAGE_SIZE,
        )

    def detail(self, *, domain_key: str, record_id: str) -> CatalogDetail | None:
        return self._service.detail(
            self.request,
            domain_key=domain_key,
            record_id=record_id,
        )

    def relations(
        self,
        *,
        domain_key: str,
        record_id: str,
        relation_kind: str,
        offset: int = 0,
    ) -> CatalogRelationPage:
        return self._service.relations(
            self.request,
            domain_key=domain_key,
            record_id=record_id,
            relation_kind=relation_kind,
            offset=offset,
            limit=self.PAGE_SIZE,
        )

    def close(self) -> None:
        self._service.close()
        self._request = None
