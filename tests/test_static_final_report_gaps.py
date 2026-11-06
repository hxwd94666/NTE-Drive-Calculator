# 验证最终化按数据库重建毕业缺项事实，既不丢失未知来源，也不把图鉴用途当毕业能力。
from contextlib import closing
import json
import sqlite3

import pytest

from tools.game_data.promote_static_release import refresh_final_report, validate_final_report
from tools.game_data.static_release_size_policy import StaticReleasePromotionError


NTE_TEST_TIER = "core"


def database(tmp_path, scope):
    path = tmp_path / "game_static.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
            CREATE TABLE dataset_scope(scope TEXT);
            CREATE TABLE character_likeability_gap(character_id INTEGER,source_row_id INTEGER);
            CREATE TABLE character_graduation_template(character_id INTEGER);
            INSERT INTO character_likeability_gap VALUES(1090,9);
        """)
        db.execute("INSERT INTO dataset_scope VALUES(?)", (scope,))
        db.commit()
    return path


@pytest.mark.parametrize("scope", ["game", "reference", "role_page"])
def test_finalization_keeps_source_gap_for_its_actual_scope(tmp_path, scope):
    path = database(tmp_path, scope)
    report_path = tmp_path / "report.json"
    summary = {"schema_version": 42, "dataset_id": "fixture-static", "built_at_utc": "2026-11-02T00:00:00+00:00"}
    report = refresh_final_report(path, report_path, summary)
    expected = [{"character_id": 1090, "reason": "likeability_source_unavailable", "source_row_id": 9}] if scope == "game" else []
    assert report["graduation_template_gaps"] == expected
    assert validate_final_report(path, report_path, summary)["graduation_template_gaps"] == expected
    assert "毕业模板资料缺项" in report_path.with_suffix(".md").read_text("utf-8")
    if scope == "game":
        report["graduation_template_gaps"] = []
        report_path.write_text(json.dumps(report), encoding="utf-8")
        with pytest.raises(StaticReleasePromotionError, match="最终报告与数据库不一致"):
            validate_final_report(path, report_path, summary)


def test_legacy_schema_without_gap_table_has_no_invented_gap(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE character_graduation_template(character_id INTEGER)")
        db.commit()
    summary = {"schema_version": 39, "dataset_id": "legacy-fixture", "built_at_utc": "2026-11-02T00:00:00+00:00"}
    report = refresh_final_report(path, tmp_path / "legacy.json", summary)
    assert report["graduation_template_gaps"] == []
