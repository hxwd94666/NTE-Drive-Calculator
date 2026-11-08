# 验证定向 schema 保留与来源证明共同拒绝额外结构、行和未来版本变更。
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
from unittest.mock import patch

import pytest

from tools.game_data import fork_reprojection_provenance as provenance
from tools.game_data.static_database_build_support import IMPORTER_VERSION

NTE_TEST_TIER = "core"


def _sources(tmp_path, schema):
    source = tmp_path / "source.sqlite3"
    candidate = tmp_path / "candidate.sqlite3"
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    with closing(sqlite3.connect(source)) as c, c:
        c.executescript("""
            CREATE TABLE dataset(dataset_id, importer_version);
            INSERT INTO dataset VALUES ('fixture',49);
            CREATE TABLE schema_migration(version INTEGER PRIMARY KEY);
            CREATE TABLE untouched(value); INSERT INTO untouched VALUES ('preserve');
            CREATE TABLE buff_definition(asset_path PRIMARY KEY);
            INSERT INTO buff_definition VALUES ('old-buff');
            CREATE TABLE buff_modifier(asset_path, ordinal);
            CREATE TABLE buff_trigger_effect(asset_path, ordinal);
            CREATE TABLE combat_blueprint_asset(asset_path, asset_kind);
            INSERT INTO combat_blueprint_asset VALUES ('calculation-buff','calculation');
            CREATE TABLE combat_effect_buff_link(target_asset_path,effect_definition_id);
            INSERT INTO combat_effect_buff_link VALUES ('calculation-buff','fork_star:fixture');
            CREATE TABLE fork_permanent_property(value);
            CREATE TABLE fork_item(fork_id);
        """)
        c.executemany("INSERT INTO schema_migration VALUES (?)", [(v,) for v in range(2, schema + 1)])
        if schema >= 39:
            c.execute("CREATE TABLE fork_permanent_review(value)")
    shutil.copyfile(source, candidate)
    with closing(sqlite3.connect(candidate)) as c, c:
        if schema == 38:
            c.execute("CREATE TABLE fork_permanent_review(value)")
            c.execute("INSERT INTO schema_migration VALUES (39)")
        c.execute("UPDATE dataset SET importer_version=?", (IMPORTER_VERSION,))
        c.execute("INSERT INTO buff_definition VALUES ('calculation-buff')")
    return source, candidate, manifest


def _validate(tmp_path, source, candidate, manifest):
    # 此组只验证 provenance 的结构/行边界；公式重算由独立弧盘投影专项覆盖。
    with (
        patch.object(provenance, "FORK_PERMANENT_EVIDENCE_SQL", "SELECT 1"),
        patch.object(provenance, "FORK_REFINEMENT_LEVEL_SQL", "SELECT 1"),
        patch.object(provenance, "FORK_SOURCE_COVERAGE_SQL", "SELECT 1"),
        patch.object(provenance, "resolve_projection_rows", return_value=((), ())),
    ):
        return provenance.write_fork_reprojection_provenance(candidate, source, manifest, tmp_path / "provenance.json")


@pytest.mark.parametrize("schema", [38, 39, 42, 44])
def test_source_proof_accepts_only_the_required_review_migration(tmp_path, schema):
    source, candidate, manifest = _sources(tmp_path, schema)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    report = _validate(tmp_path, source, candidate, manifest)
    assert report["added_buff_count"] == 1 and report["permanent_rows"] == 0
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert json.loads((tmp_path / "provenance.json").read_text())["kind"] == "fork_permanent_reprojection_v1"


@pytest.mark.parametrize("change,reason", [("schema", "迁移"), ("table", "非目标表"), ("extra", "表集合")])
def test_resigned_proof_still_rejects_unrelated_changes(tmp_path, change, reason):
    source, candidate, manifest = _sources(tmp_path, 42)
    with closing(sqlite3.connect(candidate)) as c, c:
        if change == "schema":
            c.execute("INSERT INTO schema_migration VALUES (44)")
        elif change == "table":
            c.execute("UPDATE untouched SET value='modified'")
        else:
            c.execute("CREATE TABLE unexpected(value)")
    with pytest.raises(ValueError, match=reason):
        _validate(tmp_path, source, candidate, manifest)
