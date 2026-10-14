# 验证弧盘常驻重投影不会冒用完整静态构建的 importer 身份。
from __future__ import annotations

import sqlite3
from unittest.mock import patch

from tools.game_data import reproject_fork_permanent_properties as reproject
from tools.game_data.static_database_build_support import IMPORTER_VERSION
from tools.game_data.upgrade_static_database import ADDITIVE_UPGRADE_IMPORTER_VERSION


NTE_TEST_TIER = "core"


def test_historical_additive_upgrade_does_not_claim_new_buff_importer():
    assert ADDITIVE_UPGRADE_IMPORTER_VERSION < IMPORTER_VERSION


def test_reprojection_marks_candidate_with_current_importer_version(tmp_path):
    source = tmp_path / "source.sqlite3"
    output = tmp_path / "candidate.sqlite3"
    audit = tmp_path / "audit.json"
    with sqlite3.connect(source) as connection:
        # 使用已发布 dataset/schema_migration 的真实字段；候选重投影要更新构建时间身份。
        connection.executescript("""
            CREATE TABLE dataset(
                dataset_id TEXT PRIMARY KEY, game_version TEXT,
                importer_version INTEGER NOT NULL, built_at_utc TEXT NOT NULL
            );
            INSERT INTO dataset VALUES ('fixture', NULL, 49, '2000-01-01T00:00:00+00:00');
            CREATE TABLE schema_migration(version INTEGER PRIMARY KEY, applied_at_utc TEXT NOT NULL);
            INSERT INTO schema_migration VALUES (39, '2000-01-01T00:00:00+00:00');
            CREATE TABLE fork_permanent_property(
                fork_id TEXT, refinement_level INTEGER, property_id TEXT,
                modifier_operation TEXT, property_value REAL,
                source_parameter_name_id TEXT, source_effect_definition_id TEXT,
                source_calculation_asset_path TEXT, source_row_id INTEGER
            );
            CREATE TABLE character_graduation_template(
                character_id INTEGER PRIMARY KEY, generated_at_utc TEXT
            );
            CREATE TABLE fork_permanent_review(
                fork_id TEXT, status TEXT, expected_level_count INTEGER,
                resolved_level_count INTEGER, candidate_count INTEGER, detail TEXT
            );
        """)
    with (
        patch.object(reproject, "FORK_PERMANENT_EVIDENCE_SQL", "SELECT 1"),
        patch.object(reproject, "FORK_REFINEMENT_LEVEL_SQL", "SELECT 1"),
        patch.object(reproject, "FORK_SOURCE_COVERAGE_SQL", "SELECT 1"),
        patch.object(reproject, "resolve_projection_rows", return_value=((), ())),
        patch.object(reproject, "populate_graduation_templates", return_value=0),
        patch.object(reproject.BuffImportMixin, "_import_buff_definitions"),
    ):
        report = reproject.reproject_candidate(
            source, output, audit, config_dir=tmp_path,
        )

    with sqlite3.connect(output) as connection:
        version, built_at = connection.execute(
            "SELECT importer_version, built_at_utc FROM dataset"
        ).fetchone()
    with sqlite3.connect(source) as connection:
        source_version, source_built_at = connection.execute(
            "SELECT importer_version, built_at_utc FROM dataset"
        ).fetchone()
    assert version == IMPORTER_VERSION
    assert source_version == 49
    assert source_built_at == '2000-01-01T00:00:00+00:00'
    assert built_at != source_built_at
    assert report["importer_version"] == IMPORTER_VERSION
    assert audit.is_file()
