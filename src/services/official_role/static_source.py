# 校验替换读取显式来源与页面已冻结来源一致，不解析默认库。
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.domain.static_catalog import StaticCatalogRelease


def frozen_static_source(
    detail: Mapping[str, Any], *,
    static_database_path: str | Path, static_schema_version: int,
    static_release: StaticCatalogRelease,
) -> tuple[Path, int]:
    """路径/schema/token只属读模型；调用方必须显式传入同一个已验字节身份。"""
    def canonical_path(value: Any) -> Path:
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValueError("替换读取需要明确的静态数据库绝对路径。")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("替换读取需要明确的静态数据库绝对路径。")
        return path.resolve()

    def schema(value: Any) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("替换读取需要明确的正整数静态 schema。")
        return value

    explicit_path, explicit_schema = canonical_path(static_database_path), schema(static_schema_version)
    detail_path = canonical_path(detail.get("static_database_path"))
    detail_schema = schema(detail.get("static_schema_version"))
    if explicit_path != detail_path or explicit_schema != detail_schema:
        raise ValueError("替换读取来源与冻结角色资料不一致，请重新加载资料。")
    frozen_release = detail.get("static_release")
    if (not isinstance(static_release, StaticCatalogRelease)
            or not isinstance(frozen_release, StaticCatalogRelease)
            or static_release != frozen_release
            or static_release.database_path != explicit_path
            or static_release.schema_version != explicit_schema
            or len(static_release.sha256) != 64
            or any(c not in "0123456789abcdef" for c in static_release.sha256)):
        raise ValueError("替换读取缺少同一已验证静态字节身份，请重新加载资料。")
    return explicit_path, explicit_schema
