# 提供领域资料适配器共用的发行校验与记录编码。
"""Shared, Qt-free mapping helpers for static-catalog provider adapters."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote

from src.features.static_catalog.contracts import (
    CatalogField,
    CatalogValueSource,
    StaticCatalogRelease,
)


_SOURCE_BY_ORIGIN = {
    "official_static": CatalogValueSource.OFFICIAL_STATIC,
    "formal_static": CatalogValueSource.OFFICIAL_STATIC,
    "formula_profile": CatalogValueSource.DERIVED_DISPLAY,
    "project_annotation": CatalogValueSource.PROJECT_ANNOTATION,
    "derived_display": CatalogValueSource.DERIVED_DISPLAY,
    "source_metadata": CatalogValueSource.OFFICIAL_STATIC,
    "unavailable": CatalogValueSource.DERIVED_DISPLAY,
}


def validate_release_path(
    release: StaticCatalogRelease,
    database_path: str | Path,
) -> None:
    """Reject requests whose frozen release is not this provider's database."""

    expected = Path(database_path).expanduser().resolve()
    actual = release.database_path.expanduser().resolve()
    if actual != expected:
        raise RuntimeError("资料库请求的发行静态数据库已变化")
    if not release.read_only:
        raise RuntimeError("游戏资料库只接受只读发行快照")


def validate_release_identity(
    release: StaticCatalogRelease,
    *,
    dataset_id: str,
    schema_version: int,
    importer_version: int,
    built_at_utc: str | None = None,
) -> None:
    """Compare service evidence with the identity frozen by the controller."""

    if release.dataset_id != dataset_id:
        raise RuntimeError("资料库请求期间静态 dataset 已变化")
    if release.schema_version != schema_version:
        raise RuntimeError("资料库请求期间静态 schema 已变化")
    if release.importer_version != importer_version:
        raise RuntimeError("资料库请求期间 importer 版本已变化")
    if built_at_utc is not None and release.built_at_utc != built_at_utc:
        raise RuntimeError("资料库请求期间静态构建标识已变化")


def source_for(origin: str) -> CatalogValueSource:
    """Map domain provenance without upgrading unavailable data to official."""

    return _SOURCE_BY_ORIGIN.get(
        str(origin),
        CatalogValueSource.PROJECT_ANNOTATION,
    )


def field(
    label: object,
    value: object,
    *,
    source: CatalogValueSource,
    copyable: bool = False,
) -> CatalogField:
    return CatalogField(
        label=str(label),
        value="不可用" if value is None or value == "" else str(value),
        source=source,
        copyable=copyable,
    )


def encode_typed_record_id(entity_kind: str, entity_key: object) -> str:
    return f"{entity_kind}|{quote(str(entity_key), safe='')}"


def decode_typed_record_id(record_id: str) -> tuple[str, str]:
    kind, separator, encoded_key = str(record_id).partition("|")
    if not separator or not kind or not encoded_key:
        raise ValueError("资料库记录键格式无效")
    return kind, unquote(encoded_key)
