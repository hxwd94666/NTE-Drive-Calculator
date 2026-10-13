# 用真实临时SQLite保护正式清单字节、专属读寿命与同路径共享注册所有权。
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from src.integrations.static_catalog_release import StaticCatalogReleaseError, StaticCatalogReleaseReader
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

NTE_TEST_TIER = "core"


def release_fixture(directory: Path, name="game_static.sqlite3", value="A"):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with closing(sqlite3.connect(path)) as database:
        database.executescript("""
            CREATE TABLE schema_migration(version INTEGER);
            INSERT INTO schema_migration VALUES(39);
            CREATE TABLE dataset(dataset_id TEXT, importer_version INTEGER, built_at_utc TEXT);
            INSERT INTO dataset VALUES('same-fixture', 1, '2026-01-01T00:00:00Z');
            CREATE TABLE dataset_scope(dataset_id TEXT, scope TEXT);
            INSERT INTO dataset_scope VALUES('same-fixture', 'game');
            CREATE TABLE marker(value TEXT);
        """)
        database.execute("INSERT INTO marker VALUES(?)", (value,))
        database.commit()
    write_manifest(path)
    return path


def write_manifest(path):
    manifest = {"catalog_scope": "game", "database": {
        "filename": path.name, "dataset_id": "same-fixture", "schema_version": 39,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size,
        "source_payloads_omitted": True,
    }, "build_tool": {"importer_version": 1}}
    path.with_name("manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_same_path_same_metadata_changed_bytes_early_failure_and_retry(tmp_path):
    path = release_fixture(tmp_path / "release")
    reader = StaticCatalogReleaseReader()
    with reader.read_unit(path) as unit:
        token = unit.release
        assert unit.dao._one("SELECT value FROM marker")["value"] == "A"
        assert token.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    second = release_fixture(tmp_path / "candidate", value="B")
    stamp = path.stat()
    os.replace(second, path)
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    write_manifest(path)
    with pytest.raises(StaticCatalogReleaseError):
        with reader.read_unit(path, expected_release=token):
            pytest.fail("旧token不应读取新的字节")
    with reader.read_unit(path) as unit:
        assert unit.release.dataset_id == token.dataset_id
        assert unit.release.sha256 != token.sha256
        assert unit.dao._one("SELECT value FROM marker")["value"] == "B"
    with pytest.raises(StaticCatalogReleaseError):
        reader.ensure_unchanged(token)


@pytest.mark.parametrize("field", ["sha256", "size_bytes", "dataset_id", "schema_version", "importer_version", "catalog_scope"])
def test_incomplete_manifest_pair_fails_and_complete_pair_retries(tmp_path, field):
    path = release_fixture(tmp_path)
    manifest = write_manifest(path)
    if field == "catalog_scope":
        manifest[field] = "reference"
    elif field == "importer_version":
        manifest["build_tool"][field] = 2
    else:
        old = manifest["database"][field]
        manifest["database"][field] = old + 1 if isinstance(old, int) else "different"
    path.with_name("manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    reader = StaticCatalogReleaseReader()
    with pytest.raises(StaticCatalogReleaseError):
        reader.freeze(path)
    write_manifest(path)
    with reader.read_unit(path) as unit:
        assert unit.dao._one("SELECT value FROM marker")["value"] == "A"


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_release_sidecars_rejected_without_cleanup_or_account_checkpoint(tmp_path, suffix):
    path = release_fixture(tmp_path)
    sidecar = path.with_name(path.name + suffix)
    sidecar.write_bytes(b"fixture-preserved")
    with pytest.raises(StaticCatalogReleaseError):
        StaticCatalogReleaseReader().freeze(path)
    assert sidecar.read_bytes() == b"fixture-preserved"


def test_wal_header_without_sidecars_is_not_main_sha_release(tmp_path):
    path = release_fixture(tmp_path)
    with closing(sqlite3.connect(path)) as database:
        assert database.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    write_manifest(path)
    with pytest.raises(StaticCatalogReleaseError):
        StaticCatalogReleaseReader().freeze(path)


def test_dedicated_close_does_not_unregister_shared_owner(tmp_path):
    path = release_fixture(tmp_path)
    shared = StaticGameDataDao(path, reuse_connection=True)
    dedicated = StaticGameDataDao(path, reuse_connection=False)
    handle = shared._connection
    try:
        dedicated.close()
        with StaticGameDataDao(path, reuse_connection=True) as borrower:
            assert borrower._connection is handle
            assert shared._one("SELECT value FROM marker")["value"] == "A"
    finally:
        dedicated.close()
        shared.close(force=True)


def test_pin_dedicated_transaction_and_cleanup_remain_request_owned(tmp_path):
    path = release_fixture(tmp_path / "release")
    other = release_fixture(tmp_path / "replacement", value="B")
    reader = StaticCatalogReleaseReader()
    with reader.read_unit(path) as first:
        with reader.read_unit(path, expected_release=first.release) as second:
            assert first.dao._connection is not second.dao._connection
            assert first.dao._connection.in_transaction and second.dao._connection.in_transaction
            with pytest.raises(OSError):
                os.replace(other, path)
            with pytest.raises(OSError):
                with path.open("r+b"):
                    pytest.fail("pin期间不应取得写句柄")
        assert first.dao._connection.in_transaction
        assert first.dao._one("SELECT value FROM marker")["value"] == "A"
    os.replace(other, path)
    write_manifest(path)
    assert reader.freeze(path).sha256 != first.release.sha256
    with pytest.raises(StaticCatalogReleaseError):
        with reader.read_unit(path, expected_release=replace(first.release, sha256="0" * 64)):
            pytest.fail("不同字节身份应早失败")

def test_corrupt_schema_constructor_releases_owned_connection_with_traceback_retained(tmp_path, monkeypatch):
    path = release_fixture(tmp_path / "release")
    replacement = release_fixture(tmp_path / "replacement", value="B")
    shared = StaticGameDataDao(path, reuse_connection=True)
    shared_connection = shared._connection
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("UPDATE schema_migration SET version='broken-schema'")
        writer.commit()
    write_manifest(path)
    actual_connect = sqlite3.connect
    opened = []

    def observed_connect(*args, **kwargs):
        connection = actual_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed_connect)
    failures = []
    try:
        try:
            StaticCatalogReleaseReader().freeze(path)
        except (StaticCatalogReleaseError, ValueError) as error:
            # 明确保留traceback与实际connection引用；不依赖CPython析构或GC收尾。
            failures.append((error, error.__traceback__))
        assert failures and failures[0][1] is not None and len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            opened[0].execute("SELECT value FROM marker")
        assert shared._one("SELECT value FROM marker")["value"] == "A"
        # borrowed constructor也校验schema；先完整修复包，再验证原shared注册复用。
        with closing(actual_connect(path)) as repair:
            repair.execute("UPDATE schema_migration SET version=39")
            repair.commit()
        write_manifest(path)
        with StaticGameDataDao(path, reuse_connection=True) as borrower:
            assert borrower._connection is shared_connection
    finally:
        shared.close(force=True)
        for connection in opened:
            connection.close()
    # 此时异常/traceback仍存活，但自有DAO和native pin已物理释放；同一路径完整包可重试。
    os.replace(replacement, path)
    write_manifest(path)
    with StaticCatalogReleaseReader().read_unit(path) as unit:
        assert unit.dao._one("SELECT value FROM marker")["value"] == "B"
    assert failures[0][1] is not None
