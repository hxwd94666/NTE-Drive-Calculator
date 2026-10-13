# 按已核对的旧图鉴库哈希与正式掉落闭包制作可回滚的甲硬币修复候选。
"""Repair only the audited reference catalog currency projection."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

from tools.game_data.repair_published_gold_catalog import (
    PAID_DROP_SQL,
    PATCH_SQL,
    sha256,
    verify_drop_identity,
)
from tools.game_data.static_database_build_support import (
    IMPORTER_VERSION,
    SCHEMA_PATHS,
    SCHEMA_VERSION,
)


BASELINE_SHA256 = "BE81D63737722AADD14441718C6FD13E5DFDC2FCB6EFFD8D1AA863A9019CEC1C"
DATASET_ID = "cn_retail_reference_20260924_9030b45b"
BASELINE_IMPORTER = 47
BASELINE_SCHEMA = 37
EXPECTED_COUNTS = {
    "character_breakthrough_cost": 144,
    "character_exp_material_cost": 3,
    "fork_exp_material_cost": 3,
    "clone_drop_projection_item": 146,
}


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column}='Fons'"
        ).fetchone()[0]
        for table in EXPECTED_COUNTS
        for column in ("cost_item_id" if table.endswith("exp_material_cost") else "item_id",)
    }


def _identity(connection: sqlite3.Connection) -> tuple[str, int, int, str]:
    dataset_id, importer = connection.execute(
        "SELECT dataset_id, importer_version FROM dataset"
    ).fetchone()
    schema = connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
    scope = connection.execute("SELECT scope FROM dataset_scope").fetchone()[0]
    return dataset_id, importer, schema, scope


def _check_baseline(baseline: Path, manifest: Path) -> tuple[str, ...]:
    if sha256(baseline) != BASELINE_SHA256:
        raise ValueError("独立图鉴基线哈希变化")
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    if metadata["catalog_scope"] != "reference" or metadata["database"]["sha256"].upper() != BASELINE_SHA256:
        raise ValueError("独立图鉴 manifest 与基线不符")
    with closing(sqlite3.connect(f"{baseline.as_uri()}?mode=ro", uri=True)) as connection:
        if _identity(connection) != (DATASET_ID, BASELINE_IMPORTER, BASELINE_SCHEMA, "reference"):
            raise ValueError("独立图鉴基线身份变化")
        if _counts(connection) != EXPECTED_COUNTS:
            raise ValueError("独立图鉴待修记录数量变化")
        paid_ids = tuple(row[0] for row in connection.execute(PAID_DROP_SQL))
        if len(paid_ids) != 145 or "drop_fons1" in paid_ids:
            raise ValueError("付费副本闭包变化")
        if connection.execute(
            "SELECT drop_id,item_id,quantity FROM clone_drop_projection_item WHERE drop_id='drop_fons1'"
        ).fetchall() != [("drop_fons1", "Fons", 1)]:
            raise ValueError("真实方斯掉落变化")
        return paid_ids


def _apply_patch(database: Path, built_at: str) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(PATCH_SQL)
        schema = SCHEMA_PATHS[-1]
        if int(schema.name.split("_", 1)[0]) != SCHEMA_VERSION:
            raise ValueError("最新 schema 文件身份不符")
        connection.executescript(schema.read_text(encoding="utf-8"))
        connection.execute("INSERT INTO schema_migration VALUES (?,?)", (SCHEMA_VERSION, built_at))
        connection.execute(
            "UPDATE dataset SET importer_version=?,built_at_utc=?",
            (IMPORTER_VERSION, built_at),
        )
        expected = {table: 0 for table in EXPECTED_COUNTS}
        expected["clone_drop_projection_item"] = 1
        if _counts(connection) != expected:
            raise ValueError("修复后仍有误映射")
        if connection.execute(
            "SELECT item_id FROM progression_item_alias WHERE token='gold' AND context='progression_cost'"
        ).fetchall() != [("Gold",)]:
            raise ValueError("养成别名未指向甲硬币")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("候选外键检查失败")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("候选完整性检查失败")
        connection.commit()


def _compare_contents(expected: Path, actual: Path) -> None:
    with (
        closing(sqlite3.connect(f"{expected.as_uri()}?mode=ro", uri=True)) as left,
        closing(sqlite3.connect(f"{actual.as_uri()}?mode=ro", uri=True)) as right,
    ):
        tables = tuple(row[0] for row in left.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ))
        if tables != tuple(row[0] for row in right.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )):
            raise ValueError("候选表集合与定点补丁不符")
        for table in tables:
            query = f'SELECT * FROM "{table}" ORDER BY rowid'
            if left.execute(query).fetchall() != right.execute(query).fetchall():
                raise ValueError(f"候选表超出定点补丁：{table}")


def validate_gold_fix_provenance(
    *, candidate_database: Path, provenance_path: Path,
    baseline_database: Path, baseline_manifest: Path, official_source_root: Path,
) -> dict:
    """主静态沿用原定点修复，reference 则逐表精确复算验证。"""
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if provenance.get("catalog_scope") != "reference":
        from tools.game_data.repair_published_gold_catalog import validate_gold_fix_provenance as validate_main
        return validate_main(
            candidate_database=candidate_database, provenance_path=provenance_path,
            baseline_database=baseline_database, baseline_manifest=baseline_manifest,
            official_source_root=official_source_root,
        )
    paid_ids = _check_baseline(baseline_database, baseline_manifest)
    evidence = verify_drop_identity(official_source_root, paid_ids)
    if evidence != provenance.get("source_evidence"):
        raise ValueError("来源闭包证据变化")
    if provenance.get("baseline_sha256") != BASELINE_SHA256:
        raise ValueError("provenance 基线哈希不符")
    if provenance.get("modified_sha256") != sha256(candidate_database):
        raise ValueError("provenance 候选哈希不符")
    built_at = provenance.get("built_at_utc")
    if not isinstance(built_at, str) or not built_at:
        raise ValueError("provenance 构建时间缺失")
    with tempfile.TemporaryDirectory(prefix="reference-gold-audit-") as temporary:
        expected = Path(temporary) / "expected.sqlite3"
        shutil.copy2(baseline_database, expected)
        _apply_patch(expected, built_at)
        _compare_contents(expected, candidate_database)
    return provenance


def build_candidate(baseline_dir: Path, source_root: Path, output: Path) -> dict:
    baseline_dir, source_root, output = (
        value.expanduser().resolve() for value in (baseline_dir, source_root, output)
    )
    if output.exists() or output == baseline_dir or baseline_dir in output.parents:
        raise ValueError("候选目录须独立且尚不存在")
    baseline = baseline_dir / "game_static.sqlite3"
    manifest = baseline_dir / "manifest.json"
    paid_ids = _check_baseline(baseline, manifest)
    evidence = verify_drop_identity(source_root, paid_ids)
    output.mkdir(parents=True)
    shutil.copy2(baseline, output / "baseline_game_static.sqlite3")
    shutil.copy2(manifest, output / "baseline_manifest.json")
    shutil.copytree(baseline_dir, output / "baseline")
    shutil.copy2(Path(__file__).with_name("reference_gold_rollback.py"), output / "rollback.py")
    shutil.copytree(baseline_dir / "game_ui", output / "game_ui")
    (output / "report").mkdir()
    if sha256(output / "baseline_game_static.sqlite3") != BASELINE_SHA256:
        raise ValueError("候选中的基线备份哈希不符")
    local_config = {
        "dataset_id": DATASET_ID,
        "official_content_root": str(source_root),
        "baseline_database_path": str(output / "baseline_game_static.sqlite3"),
        "baseline_manifest_path": str(output / "baseline_manifest.json"),
    }
    (output / "local.paths.json").write_text(
        json.dumps(local_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    modified = output / "game_static.sqlite3"
    shutil.copy2(baseline, modified)
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _apply_patch(modified, built_at)
    modified_hash = sha256(modified)
    assets_manifest = output / "game_ui/manifest.json"
    assets = json.loads(assets_manifest.read_text(encoding="utf-8"))
    assets["database_sha256"] = modified_hash
    assets_manifest.write_text(json.dumps(assets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reference_sql = PATCH_SQL.replace(
        "-- Audited correction for cn_retail_20260924_9030b45b only.",
        f"-- Audited correction for {DATASET_ID} only.",
    )
    (output / "changes.sql").write_text(
        reference_sql + "\n" + SCHEMA_PATHS[-1].read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    result = {
        "catalog_scope": "reference", "baseline_sha256": BASELINE_SHA256,
        "modified_sha256": modified_hash, "built_at_utc": built_at,
        "source_evidence": evidence, "paid_gold_drop_count": len(paid_ids),
        "account_databases_modified": False,
    }
    (output / "gold_fix_provenance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(build_candidate(arguments.baseline_dir, arguments.source, arguments.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
