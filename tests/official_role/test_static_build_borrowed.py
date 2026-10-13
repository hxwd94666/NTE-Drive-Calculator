# 用最小真实构建库保护borrowed连接/事务、populate回滚与runtime共用纯装配。
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from src.integrations.static_catalog_release import StaticCatalogReleaseError
from src.services.characters.workshop_weight_template_service import WORKSHOP_WEIGHT_TEMPLATE_ENV
from src.services.official_role.detail_inputs import materialize_official_role_detail_inputs
from src.services.official_role.detail_model import assemble_official_role_detail
from src.services.official_role.page import load_official_role_detail
from src.storage.sqlite.static_game_data_build_dao import StaticGameDataBuildDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.official_role.static_build_fixture import CHARACTER_ID, build_fixture
from tools.game_data.build_graduation_templates import populate_graduation_templates

NTE_TEST_TIER = "core"


class ObservedCursor(sqlite3.Cursor):
    closed = False

    def close(self):
        self.closed = True
        super().close()


class ObservedConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.borrowed_cursors = []
        self.close_calls = self.commit_calls = self.rollback_calls = 0

    def cursor(self, *args, **kwargs):
        cursor = super().cursor(factory=ObservedCursor)
        self.borrowed_cursors.append(cursor)
        return cursor

    def close(self):
        self.close_calls += 1
        super().close()

    def commit(self):
        self.commit_calls += 1
        super().commit()

    def rollback(self):
        self.rollback_calls += 1
        super().rollback()


def build_detail(reader):
    return assemble_official_role_detail(materialize_official_role_detail_inputs(
        reader, CHARACTER_ID, include_inventory_contexts=False, include_replacement_candidates=False,
    ))


def test_borrowed_reader_sees_uncommitted_facts_and_preserves_owner_on_error(tmp_path):
    connection, path, _config = build_fixture(tmp_path, factory=ObservedConnection)
    shared = StaticGameDataDao(path, reuse_connection=True)
    shared_connection = shared._connection
    try:
        before = connection.commit_calls, connection.rollback_calls, connection.close_calls
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE character_panel_growth SET atk_base=200")
        connection.execute("UPDATE build_owner_sentinel SET value='owner-pending'")
        sql = []
        connection.set_trace_callback(sql.append)
        reader = StaticGameDataBuildDao(connection, database_path=path)
        detail = build_detail(reader)
        assert detail['growth_rows'][0]['atk_base'] == 200
        assert 'static_release' not in detail
        assert connection.row_factory is None and connection.in_transaction
        with pytest.raises(ValueError, match='官方角色不存在'):
            materialize_official_role_detail_inputs(reader, 2999, include_inventory_contexts=False)
        with pytest.raises(sqlite3.OperationalError):
            reader._rows('SELECT * FROM fixture_table_missing')
        assert connection.borrowed_cursors and all(cursor.closed for cursor in connection.borrowed_cursors)
        assert before == (connection.commit_calls, connection.rollback_calls, connection.close_calls)
        assert not any(statement.strip().upper().startswith(('BEGIN', 'COMMIT', 'ROLLBACK')) for statement in sql)
        assert connection.execute('SELECT value FROM build_owner_sentinel').fetchone()[0] == 'owner-pending'
        with StaticGameDataDao(path, reuse_connection=True) as borrower:
            assert borrower._connection is shared_connection
        connection.rollback()
        assert connection.execute('SELECT atk_base FROM character_panel_growth').fetchone()[0] == 100
        assert connection.execute('SELECT value FROM build_owner_sentinel').fetchone()[0] == 'owner-original'
    finally:
        shared.close(force=True)
        connection.close()


def test_populate_owner_commits_success_and_rolls_back_failed_delete(tmp_path):
    connection, path, config = build_fixture(tmp_path, factory=ObservedConnection)
    try:
        counts = connection.commit_calls, connection.rollback_calls, connection.close_calls
        assert populate_graduation_templates(connection, database_path=path, config_dir=config) == 1
        assert not connection.in_transaction and not path.with_name('manifest.json').exists()
        assert (connection.commit_calls, connection.rollback_calls, connection.close_calls) == (counts[0] + 1, counts[1], counts[2])
        row = tuple(connection.execute('SELECT * FROM character_graduation_template').fetchone())
        assert connection.execute('SELECT benchmark_damage FROM character_graduation_template').fetchone()[0] > 1
        assert 'static_release' not in json.dumps(row)
        stats = json.loads((config / 'stats.json').read_text(encoding='utf-8'))
        stats['gold_base_values'].pop('暴击率%')
        (config / 'stats.json').write_text(json.dumps(stats), encoding='utf-8')
        with pytest.raises(ValueError, match='缺少四条毕业副词条'):
            populate_graduation_templates(connection, database_path=path, config_dir=config)
        assert tuple(connection.execute('SELECT * FROM character_graduation_template').fetchone()) == row
        assert (connection.commit_calls, connection.rollback_calls, connection.close_calls) == (counts[0] + 1, counts[1] + 1, counts[2])
        assert connection.execute('SELECT value FROM build_owner_sentinel').fetchone()[0] == 'owner-original'
    finally:
        connection.close()


def test_runtime_still_rejects_constructing_and_manifestless_input(tmp_path):
    connection, path, _config = build_fixture(tmp_path)
    account = tmp_path / 'never-created.sqlite3'
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('DELETE FROM character_graduation_template')
        with pytest.raises(StaticCatalogReleaseError) as failure:
            load_official_role_detail(account, CHARACTER_ID, static_database_path=path)
        assert failure.value.code == 'release_unclean'
        assert connection.in_transaction and not account.exists()
        connection.rollback()
        with pytest.raises(StaticCatalogReleaseError) as failure:
            load_official_role_detail(account, CHARACTER_ID, static_database_path=path)
        assert failure.value.code == 'release_incomplete' and not account.exists()
    finally:
        connection.close()


def test_build_and_runtime_share_pure_detail_and_numeric_benchmark(tmp_path, monkeypatch):
    connection, path, config = build_fixture(tmp_path)
    monkeypatch.setenv(WORKSHOP_WEIGHT_TEMPLATE_ENV, '')
    try:
        assert populate_graduation_templates(connection, database_path=path, config_dir=config) == 1
        expected = build_detail(StaticGameDataBuildDao(connection, database_path=path))
        dataset, importer, _stamp = connection.execute('SELECT * FROM dataset').fetchone()
        schema = connection.execute('SELECT MAX(version) FROM schema_migration').fetchone()[0]
    finally:
        connection.close()
    # 仅在真实owner关闭后为合成成品生成字节匹配清单，不伪造构建期token。
    path.with_name('manifest.json').write_text(json.dumps({
        'catalog_scope': 'game', 'database': {'filename': path.name, 'dataset_id': dataset,
        'schema_version': schema, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'size_bytes': path.stat().st_size, 'source_payloads_omitted': True},
        'build_tool': {'importer_version': importer},
    }), encoding='utf-8')
    with UserDataDao(tmp_path / "account.sqlite3", account_id="builder-parity-fixture"):
        pass
    cache = {}
    actual = load_official_role_detail(tmp_path / 'account.sqlite3', CHARACTER_ID,
        static_database_path=path, static_schema_version=schema, include_inventory_contexts=False,
        include_replacement_candidates=False, request_cache=cache)
    release = actual.pop('static_release')
    assert actual == expected
    assert all(key[0] == release for key in cache)
    assert not any(key in json.dumps(actual['profile']) for key in ('static_release', 'static_schema_version'))
