# 只为临时合成SQLite生成实际字节清单，release必须由正式reader校验产生。
import hashlib
import json
from pathlib import Path
from shutil import copyfile
from src.integrations.static_catalog_release import StaticCatalogReleaseReader
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

ROOT = Path(__file__).resolve().parents[2]
while not (ROOT / "pyproject.toml").is_file():
    ROOT = ROOT.parent


def publish_test_manifest(database):
    """先关写连接/日志，再按实际schema、dataset、scope和SHA构造完整临时发行包。"""
    with StaticGameDataDao(database, reuse_connection=False) as dao:
        metadata = dao.dataset_metadata()
        schema = dao.schema_version
        scope = dao.get_catalog_scope()
    manifest = {
        "catalog_scope": scope,
        "database": {"filename": database.name, "schema_version": schema,
                     "dataset_id": metadata["dataset_id"], "size_bytes": database.stat().st_size,
                     "sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                     "source_payloads_omitted": False},
        "build_tool": {"importer_version": metadata["importer_version"]},
    }
    database.with_name("manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return StaticCatalogReleaseReader().freeze(database)


def clone_test_release(directory):
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "static.sqlite3"
    copyfile(ROOT / "data/game_static.sqlite3", database)
    return database, publish_test_manifest(database)
