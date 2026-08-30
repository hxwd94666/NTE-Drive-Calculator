# 游戏资料库 B 域的 Qt 无关 DTO。
"""Immutable DTOs shared by the miscellaneous catalog service and views."""

from __future__ import annotations

from dataclasses import dataclass


ORIGIN_FORMAL = "formal_static"
ORIGIN_ANNOTATION = "project_annotation"
ORIGIN_DERIVED = "derived_display"
ORIGIN_SOURCE = "source_metadata"


@dataclass(frozen=True, slots=True)
class CatalogDomain:
    key: str
    title: str
    description: str
    entity_count: int


@dataclass(frozen=True, slots=True)
class CatalogSearchItem:
    entity_kind: str
    entity_key: str
    title: str
    subtitle: str
    origin_kind: str
    origin_label: str
    source_row_id: int | None = None
    source_file_id: int | None = None


@dataclass(frozen=True, slots=True)
class CatalogSearchPage:
    domain_key: str
    query: str
    offset: int
    limit: int
    total: int
    items: tuple[CatalogSearchItem, ...]

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


@dataclass(frozen=True, slots=True)
class CatalogField:
    label: str
    value: str
    origin_kind: str
    origin_label: str
    copy_kind: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogSection:
    title: str
    fields: tuple[CatalogField, ...]


@dataclass(frozen=True, slots=True)
class CatalogRelation:
    label: str
    target_kind: str
    target_key: str
    title: str


@dataclass(frozen=True, slots=True)
class CatalogDetail:
    entity_kind: str
    entity_key: str
    title: str
    subtitle: str
    origin_kind: str
    origin_label: str
    sections: tuple[CatalogSection, ...]
    relations: tuple[CatalogRelation, ...]
    source_row_id: int | None = None
    source_file_id: int | None = None


@dataclass(frozen=True, slots=True)
class CatalogRelationPage:
    relation_kind: str
    offset: int
    limit: int
    total: int
    rows: tuple[CatalogSection, ...]

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.rows) < self.total


@dataclass(frozen=True, slots=True)
class SourceTrace:
    source_file_id: int
    relative_path: str
    source_file_sha256: str
    declared_row_count: int
    source_row_id: int | None
    row_key: str | None
    content_sha256: str | None
    payload_present: bool
    payloads_omitted: bool
    explanation: str


@dataclass(frozen=True, slots=True)
class StaticCatalogReleaseMetadata:
    dataset_id: str
    schema_version: int
    importer_version: int
    generated_at_utc: str
    database_sha256: str
    source_payloads_omitted: bool
