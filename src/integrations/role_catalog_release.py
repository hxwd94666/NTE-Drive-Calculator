# 校验独立角色目录身份与哈希，保持默认战报数据库路径不变。
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing


@dataclass(frozen=True, slots=True)
class RoleCatalogRelease:
    database_path: Path
    asset_root: Path
    dataset_id: str
    sha256: str
    scope: str = "role_page"

    @property
    def catalog_domains(self) -> tuple[str, ...]:
        if self.scope == "reference":
            return ("coverage", "character", "fork", "monsters", "equipment", "skills", "assets", "sources")
        return ("character", "fork")


def resolve_role_catalog(game_database_path: Path) -> RoleCatalogRelease | None:
    """一次选定完整目录；缺失整包沿用原库，损坏或用途冲突明确失败。"""
    directory = game_database_path.resolve().parent / "role_catalog"
    if not directory.exists():
        return None
    return read_role_catalog(directory)


def read_role_catalog(directory: Path) -> RoleCatalogRelease:
    """同时核对目录数据库和同数据集图片，用于运行时读取与构建晋升。"""
    directory = directory.resolve()
    database = directory / "game_static.sqlite3"
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        metadata = manifest["database"]
        scope = manifest.get("catalog_scope")
        if scope not in {"role_page", "reference"} or metadata["filename"] != database.name:
            raise ValueError("目录用途不匹配")
        with database.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest().upper()
        if digest != str(metadata["sha256"]).upper() or database.stat().st_size != metadata["size_bytes"]:
            raise ValueError("目录文件校验失败")
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
            identities = connection.execute(
                "SELECT d.dataset_id, s.scope FROM dataset d JOIN dataset_scope s USING(dataset_id)"
            ).fetchall()
        if identities != [(metadata["dataset_id"], scope)]:
            raise ValueError("目录内部身份不匹配")
        validate_role_assets(directory / "game_ui", metadata["dataset_id"], digest)
        return RoleCatalogRelease(database, directory / "game_ui", metadata["dataset_id"], digest, scope)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error) as exc:
        raise ValueError("独立角色目录不可用，请重新安装完整的角色目录包") from exc


def validate_role_assets(root: Path, dataset_id: str, database_sha256: str) -> dict:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("dataset_id") != dataset_id or manifest.get("database_sha256") != database_sha256:
        raise ValueError("角色图片与目录数据集不一致")
    if manifest.get("unresolved_assets"):
        raise ValueError("角色图片来源尚未完整解析")
    files = manifest["files"]
    if not isinstance(files, dict) or len(files) > 4096:
        raise ValueError("角色图片清单无效")
    for group in ("characters", "fork_items"):
        if any(path not in files for path in manifest[group].values()):
            raise ValueError("角色图片引用不在清单中")
    for relative, metadata in files.items():
        path = (root / relative).resolve()
        if root.resolve() not in path.parents or path.suffix != ".png":
            raise ValueError("角色图片路径越界或类型错误")
        if hashlib.sha256(path.read_bytes()).hexdigest().upper() != metadata["sha256"]:
            raise ValueError("角色图片哈希不匹配")
    return manifest
