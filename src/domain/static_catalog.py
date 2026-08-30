# 定义游戏资料库跨层只读投影契约。
"""Qt-free domain contracts shared by catalog services, adapters, and UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


GLOBAL_DOMAIN_KEY = "all"


class CatalogValueSource(StrEnum):
    """Provenance shown beside every user-facing value."""

    OFFICIAL_STATIC = "official_static"
    PROJECT_ANNOTATION = "project_annotation"
    DERIVED_DISPLAY = "derived_display"


SOURCE_LABELS = {
    CatalogValueSource.OFFICIAL_STATIC: "正式静态",
    CatalogValueSource.PROJECT_ANNOTATION: "项目注解",
    CatalogValueSource.DERIVED_DISPLAY: "派生显示值",
}


@dataclass(frozen=True, slots=True)
class StaticCatalogRelease:
    """One immutable release identity frozen before a catalog request."""

    database_path: Path
    dataset_id: str
    schema_version: int
    importer_version: int
    built_at_utc: str
    source_payloads_omitted: bool
    read_only: bool = True


@dataclass(frozen=True, slots=True)
class CatalogDomain:
    key: str
    label: str
    description: str
    order: int


@dataclass(frozen=True, slots=True)
class CatalogItem:
    domain_key: str
    record_id: str
    title: str
    subtitle: str = ""
    source: CatalogValueSource = CatalogValueSource.OFFICIAL_STATIC


@dataclass(frozen=True, slots=True)
class CatalogField:
    label: str
    value: str
    source: CatalogValueSource
    copyable: bool = False


@dataclass(frozen=True, slots=True)
class CatalogReference:
    label: str
    domain_key: str
    record_id: str


@dataclass(frozen=True, slots=True)
class CatalogSection:
    title: str
    fields: tuple[CatalogField, ...]
    references: tuple[CatalogReference, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogRelationGroup:
    """A high-cardinality relation that must be loaded independently."""

    kind: str
    label: str
    total: int


@dataclass(frozen=True, slots=True)
class CatalogDetail:
    item: CatalogItem
    sections: tuple[CatalogSection, ...]
    notes: tuple[str, ...] = ()
    relation_groups: tuple[CatalogRelationGroup, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogPage:
    items: tuple[CatalogItem, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class CatalogRelationPage:
    """One bounded page of rows belonging to a detail relation group."""

    relation_kind: str
    rows: tuple[CatalogSection, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class StaticCatalogRequest:
    release: StaticCatalogRelease
    domains: tuple[CatalogDomain, ...]


class StaticCatalogProvider(Protocol):
    """Narrow adapter implemented for one catalog domain."""

    @property
    def domain(self) -> CatalogDomain: ...

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage: ...

    def detail(
        self,
        release: StaticCatalogRelease,
        record_id: str,
    ) -> CatalogDetail | None: ...

    def close(self) -> None: ...
