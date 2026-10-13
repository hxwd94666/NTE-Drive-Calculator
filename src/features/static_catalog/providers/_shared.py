# 提供角色与弧盘资料适配器共用的投影函数。
"""Shared projection helpers used only by character and fork providers."""

from __future__ import annotations

from pathlib import Path

from src.features.static_catalog.contracts import (
    CatalogField,
    CatalogValueSource,
    StaticCatalogRelease,
)


def ensure_release_path(release: StaticCatalogRelease, expected_path: Path) -> None:
    """访问领域 DAO 前核对只读属性和冻结路径，不混入另一发行库。"""

    if not release.read_only:
        raise RuntimeError("游戏资料库只接受只读发行快照")
    if release.database_path.resolve() != expected_path:
        raise RuntimeError("游戏资料库请求的发行数据库已与领域 Provider 不一致")


def ensure_release_metadata(
    release: StaticCatalogRelease,
    *,
    dataset_id: str,
    schema_version: int,
    importer_version: int,
    built_at_utc: str,
) -> None:
    """数据库四项元信息须同时匹配调用方冻结身份，变化时中止本次请求。"""

    actual = (dataset_id, schema_version, importer_version, built_at_utc)
    frozen = (
        release.dataset_id,
        release.schema_version,
        release.importer_version,
        release.built_at_utc,
    )
    if actual != frozen:
        raise RuntimeError("发行静态库元信息在资料请求期间发生变化")


def display(value: object) -> str:
    # None/空串与明确零/否分开；来源标签由下方 official/derived/annotation 显式给出。
    if value is None or value == "":
        return "未保留"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def official(label: str, value: object, *, copyable: bool = False) -> CatalogField:
    return CatalogField(
        label=label,
        value=display(value),
        source=CatalogValueSource.OFFICIAL_STATIC,
        copyable=copyable,
    )


def derived(label: str, value: object, *, copyable: bool = False) -> CatalogField:
    return CatalogField(
        label=label,
        value=display(value),
        source=CatalogValueSource.DERIVED_DISPLAY,
        copyable=copyable,
    )


def annotation(label: str, value: object, *, copyable: bool = False) -> CatalogField:
    return CatalogField(
        label=label,
        value=display(value),
        source=CatalogValueSource.PROJECT_ANNOTATION,
        copyable=copyable,
    )


def lines(values: list[str] | tuple[str, ...]) -> str:
    return "\n".join(values) if values else "无"
