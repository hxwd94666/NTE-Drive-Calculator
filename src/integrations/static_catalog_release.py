# 在同一个真实文件pin和专属只读事务内校验静态发行清单与字节。
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import os
import sqlite3
from typing import Iterator

from src.domain.static_catalog import StaticCatalogRelease
from src.integrations._static_release_pin import WindowsStaticFilePin
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao, StaticGameDataError, resolve_static_database


class StaticCatalogReleaseError(RuntimeError):
    """包不完整、字节失配、占用等待与读取期间更新分别保留错误码。"""

    def __init__(self, message: str, *, code: str = "release_invalid"):
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class StaticCatalogReadUnit:
    """仅在owner上下文内有效；所有静态值应在退出前完全物化。"""

    dao: StaticGameDataDao
    release: StaticCatalogRelease
    _active: bool = True

    def require_source(self, path: str | Path | None, schema: int | None, expected: StaticCatalogRelease | None) -> None:
        if not self._active:
            raise StaticCatalogReleaseError("静态读取单元已结束，请重新发起读取", code="read_unit_closed")
        if (path is not None and Path(path).expanduser().absolute() != self.release.database_path
                or schema is not None and schema != self.release.schema_version
                or expected is not None and expected != self.release):
            raise StaticCatalogReleaseError("静态读取来源与本次冻结身份不一致，请重新加载", code="release_changed")


def _reject_sidecars(path: Path) -> None:
    if any(os.path.lexists(path.with_name(path.name + suffix)) for suffix in ("-wal", "-shm", "-journal")):
        raise StaticCatalogReleaseError("静态发行包含活动或恢复日志，请安装完整干净包", code="release_unclean")


def _manifest(path: Path) -> tuple[bytes, dict, dict, str]:
    try:
        raw = path.with_name("manifest.json").read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("manifest超限")
        manifest = json.loads(raw)
        database, tool = manifest["database"], manifest["build_tool"]
        scope = manifest.get("catalog_scope", "game")
        if not isinstance(database, dict) or not isinstance(tool, dict) or scope not in {"game", "role_page", "reference"}:
            raise ValueError("清单结构或用途不一致")
        digest = database["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise ValueError("清单缺少有效SHA")
        if database.get("filename") != path.name:
            raise ValueError("清单文件身份不一致")
        for field in ("schema_version", "size_bytes"):
            if type(database.get(field)) is not int or database[field] <= 0:
                raise ValueError("清单数值字段无效")
        if type(tool.get("importer_version")) is not int or tool["importer_version"] <= 0:
            raise ValueError("清单导入器无效")
        if not isinstance(database.get("dataset_id"), str) or not database["dataset_id"]:
            raise ValueError("清单缺少dataset")
        return raw, database, tool, scope
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StaticCatalogReleaseError("静态发行清单不完整，请重新安装完整包", code="release_incomplete") from exc


class StaticCatalogReleaseReader:
    """复用正式发行语义owner，不缓存未持pin的路径或旧共享连接。"""

    @contextmanager
    def read_unit(self, database_path: str | Path | None = None, *,
                  expected_release: StaticCatalogRelease | None = None,
                  expected_schema_version: int | None = None) -> Iterator[StaticCatalogReadUnit]:
        path = (Path(database_path).expanduser().absolute() if database_path is not None
                else resolve_static_database())
        _reject_sidecars(path)
        raw, database, tool, scope = _manifest(path)
        if expected_release is not None and (
                expected_release.database_path != path or expected_release.sha256 != database["sha256"].lower()):
            raise StaticCatalogReleaseError("静态资料库已更新，请重新加载资料", code="release_changed")
        unit = None
        try:
            # FILE_SHARE_READ ONLY从hash之前持续到SQLite关闭；拒绝已有writer和新的替换。
            with WindowsStaticFilePin(path) as pin:
                _reject_sidecars(path)
                actual_sha, header = pin.sha256()
                if header[:16] != b"SQLite format 3\0" or header[18:20] != b"\x01\x01":
                    raise StaticCatalogReleaseError("静态发行数据库不是干净的非WAL文件", code="release_unclean")
                if actual_sha != database["sha256"].lower() or pin.size != database["size_bytes"]:
                    raise StaticCatalogReleaseError("静态数据库字节与发行清单不一致，请重新安装完整包", code="release_bytes_mismatch")
                with StaticGameDataDao(path, expected_schema_version=expected_schema_version, reuse_connection=False) as dao:
                    with dao.read_consistent_state():
                        # 固定本机非重解析路径及存活pin保证fresh SQLite打开同一对象；文件ID仅复核绑定。
                        with WindowsStaticFilePin(path) as binding:
                            if binding.identity != pin.identity:
                                raise StaticCatalogReleaseError("静态数据库对象在读取期间改变", code="release_changed")
                        _reject_sidecars(path)
                        metadata = dao.dataset_metadata()
                        actual_scope = dao.get_catalog_scope()
                        release = StaticCatalogRelease(
                            database_path=dao.database_path, dataset_id=str(metadata["dataset_id"]),
                            schema_version=dao.schema_version, importer_version=int(metadata["importer_version"]),
                            built_at_utc=str(metadata["built_at_utc"]),
                            source_payloads_omitted=bool(database.get("source_payloads_omitted")),
                            sha256=actual_sha, scope=actual_scope,
                        )
                        if (release.dataset_id, release.schema_version, release.importer_version, release.scope) != (
                                database["dataset_id"], database["schema_version"], tool["importer_version"], scope):
                            raise StaticCatalogReleaseError("静态数据库与清单的dataset/schema/importer/scope不一致", code="release_metadata_mismatch")
                        if expected_release is not None and release != expected_release:
                            raise StaticCatalogReleaseError("静态资料库已更新，请重新加载资料", code="release_changed")
                        unit = StaticCatalogReadUnit(dao, release)
                        yield unit
                        _reject_sidecars(path)
                        if path.with_name("manifest.json").read_bytes() != raw:
                            raise StaticCatalogReleaseError("静态发行包在读取期间更新，请重新发起读取", code="release_changed")
        except (OSError, StaticGameDataError, sqlite3.Error) as exc:
            raise StaticCatalogReleaseError("静态资料库被占用或读取失败，请稍候重新查询", code="release_waiting") from exc
        finally:
            if unit is not None:
                unit._active = False

    def freeze(self, database_path: str | Path) -> StaticCatalogRelease:
        with self.read_unit(database_path) as unit:
            return unit.release

    def ensure_unchanged(self, release: StaticCatalogRelease) -> None:
        with self.read_unit(release.database_path, expected_release=release):
            pass


@contextmanager
def static_catalog_read_unit(database_path: str | Path | None = None, *,
                             expected_schema_version: int | None = None,
                             expected_release: StaticCatalogRelease | None = None,
                             read_unit: StaticCatalogReadUnit | None = None) -> Iterator[StaticCatalogReadUnit]:
    """多角色与延迟组装借用同一owner；借用者不关闭别人的事务或pin。"""
    if read_unit is not None:
        read_unit.require_source(database_path, expected_schema_version, expected_release)
        yield read_unit
    else:
        with StaticCatalogReleaseReader().read_unit(database_path, expected_schema_version=expected_schema_version,
                                                    expected_release=expected_release) as unit:
            yield unit


@contextmanager
def static_catalog_commit_unit(
    database_path: str | Path, *, expected_release: StaticCatalogRelease | None = None,
) -> Iterator[StaticCatalogReadUnit]:
    """提交专属短lease：先pin清单再验证DB，先退出reader再释放清单。"""
    path = Path(database_path).expanduser().absolute()
    try:
        manifest_pin = WindowsStaticFilePin(path.with_name("manifest.json"))
    except OSError as exc:
        raise StaticCatalogReleaseError("静态发行清单被占用，请稍后重新保存", code="release_waiting") from exc
    # 双pin覆盖最终核验、真实commit/replace及reader退出，不把提交前hash当CAS。
    with manifest_pin, StaticCatalogReleaseReader().read_unit(
            path, expected_release=expected_release) as unit:
        yield unit
