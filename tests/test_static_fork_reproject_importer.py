# 验证定向重投影的真实迁移身份、输入保全与失败回滚。
from contextlib import closing
import hashlib
import sqlite3
from unittest.mock import patch

import pytest

from tools.game_data import reproject_fork_permanent_properties as reproject
from tools.game_data import fork_reprojection_schema as schema_rules
from tools.game_data.static_database_build_support import IMPORTER_VERSION, SCHEMA_PATHS, SCHEMA_VERSION
from tools.game_data.upgrade_static_database import ADDITIVE_UPGRADE_IMPORTER_VERSION

NTE_TEST_TIER = "core"


def _source(path, version):
    stamp = '2000-01-01T00:00:00+00:00'
    with closing(sqlite3.connect(path)) as connection, connection:
        for script in SCHEMA_PATHS:
            number = int(script.name.split('_', 1)[0])
            if number <= version:
                connection.executescript(script.read_text(encoding='utf-8'))
                connection.execute('INSERT INTO schema_migration VALUES (?,?)', (number, stamp))
        connection.execute('INSERT INTO dataset VALUES (?,?,?)', ('fixture', 49, stamp))
        connection.execute("INSERT INTO dataset_scope VALUES ('fixture','game')")
        connection.execute('CREATE TABLE test_sentinel(value TEXT)')
        connection.execute("INSERT INTO test_sentinel VALUES ('unchanged')")


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_historical_additive_upgrade_does_not_claim_new_buff_importer():
    assert ADDITIVE_UPGRADE_IMPORTER_VERSION < IMPORTER_VERSION


@pytest.mark.parametrize('schema', [38, 39, 40, 41, 42, 43, 44])
def test_reprojection_marks_candidate_with_current_importer_version(tmp_path, schema):
    source, output, audit = (tmp_path / name for name in ['source.sqlite3', 'candidate.sqlite3', 'audit.json'])
    _source(source, schema)
    before = _hash(source)
    with patch.object(reproject, 'FORK_PERMANENT_EVIDENCE_SQL', 'SELECT 1'), \
         patch.object(reproject, 'FORK_REFINEMENT_LEVEL_SQL', 'SELECT 1'), \
         patch.object(reproject, 'FORK_SOURCE_COVERAGE_SQL', 'SELECT 1'), \
         patch.object(reproject, 'resolve_projection_rows', return_value=((), ())), \
         patch.object(reproject, 'populate_graduation_templates', return_value=0), \
         patch.object(reproject.BuffImportMixin, '_import_buff_definitions'):
        report = reproject.reproject_candidate(source, output, audit, config_dir=tmp_path)
    with closing(sqlite3.connect(output)) as connection:
        version, built_at = connection.execute('SELECT importer_version,built_at_utc FROM dataset').fetchone()
        actual_schema = connection.execute('SELECT MAX(version) FROM schema_migration').fetchone()[0]
        assert connection.execute('SELECT value FROM test_sentinel').fetchone()[0] == 'unchanged'
        assert connection.execute('SELECT COUNT(*) FROM fork_permanent_review').fetchone()[0] == 0
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        versions = {r[0] for r in connection.execute('SELECT version FROM schema_migration')}
    assert actual_schema == max(39, schema) == report['schema_version']
    assert report['source_schema_version'] == schema
    assert versions == {int(p.name.split('_', 1)[0]) for p in SCHEMA_PATHS if int(p.name.split('_', 1)[0]) <= max(39, schema)}
    assert version == IMPORTER_VERSION == report['importer_version']
    assert built_at != '2000-01-01T00:00:00+00:00'
    assert _hash(source) == before and audit.is_file()


@pytest.mark.parametrize('schema', [37, SCHEMA_VERSION + 1])
def test_unknown_schema_preserves_original_and_previous_outputs(tmp_path, schema):
    source, output, audit = (tmp_path / name for name in ['source.sqlite3', 'candidate.sqlite3', 'audit.json'])
    _source(source, min(schema, SCHEMA_VERSION))
    if schema > SCHEMA_VERSION:
        with closing(sqlite3.connect(source)) as connection, connection:
            connection.execute("INSERT INTO schema_migration VALUES (?,'future')", (schema,))
    before = _hash(source)
    output.write_bytes(b'previous candidate')
    audit.write_bytes(b'previous audit')
    with pytest.raises(ValueError, match='schema'):
        reproject.reproject_candidate(source, output, audit, config_dir=tmp_path)
    assert _hash(source) == before
    assert output.read_bytes() == b'previous candidate' and audit.read_bytes() == b'previous audit'
    assert list(tmp_path.glob('.candidate.sqlite3.*.tmp')) == []


def test_failed_migration_never_publishes_candidate_or_version(tmp_path):
    source, output, audit = (tmp_path / name for name in ['source.sqlite3', 'candidate.sqlite3', 'audit.json'])
    _source(source, 38)
    before = _hash(source)
    broken = tmp_path / '039_game_static_failed.sql'
    broken.write_text('CREATE TABLE partially_applied(value); SELECT * FROM missing_migration_dependency;', encoding='utf-8')
    with patch.object(schema_rules, 'SCHEMA_PATHS', (broken,)):
        with pytest.raises(sqlite3.OperationalError):
            reproject.reproject_candidate(source, output, audit, config_dir=tmp_path)
    assert _hash(source) == before and not output.exists() and not audit.exists()
    assert list(tmp_path.glob('.candidate.sqlite3.*.tmp')) == []
