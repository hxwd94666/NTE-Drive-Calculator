# 按已核对的来源闭包修复旧发行库中的甲硬币误映射，并生成可复核候选。
"""Build an audited, reversible Gold correction without changing raw source facts."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_DATASET = ("cn_retail_20260924_9030b45b", 48)
CORRECTED_DATASET = EXPECTED_DATASET[0]
CORRECTED_IMPORTER_VERSION = 49
EXPECTED_BASELINE_SHA256 = "87F19FF41AAF2CD374C583FEF06ADA218989A7E0EB8881BB82A1EE605814FA98"
EXPECTED_COUNTS = {
    "character_breakthrough_cost": 144,
    "character_exp_material_cost": 3,
    "fork_exp_material_cost": 3,
    "clone_drop_projection_item": 146,
}
PAID_DROP_SQL = """SELECT DISTINCT i.drop_id
FROM clone_drop_projection_item AS i
JOIN clone_activity_difficulty AS d USING (drop_id)
WHERE i.item_id = 'Fons' AND d.stamina_cost > 0
ORDER BY i.drop_id"""
PATCH_SQL = """-- Audited correction for cn_retail_20260924_9030b45b only.
UPDATE character_breakthrough_cost SET item_id='Gold' WHERE item_id='Fons';
UPDATE character_exp_material_cost SET cost_item_id='Gold' WHERE cost_item_id='Fons';
UPDATE fork_exp_material_cost SET cost_item_id='Gold' WHERE cost_item_id='Fons';
UPDATE clone_drop_projection_item SET item_id='Gold'
WHERE item_id='Fons' AND drop_id IN (
    SELECT DISTINCT d.drop_id FROM clone_activity_difficulty AS d
    WHERE d.stamina_cost > 0
);
UPDATE progression_item_alias SET item_id='Gold'
WHERE token='gold' AND context='progression_cost' AND item_id='Fons';
"""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def rows(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise ValueError(f"DataTable 外层结构无效：{path.name}")
    result = payload[0].get("Rows")
    if not isinstance(result, dict) or not result:
        raise ValueError(f"DataTable Rows 为空或结构无效：{path.name}")
    return result


def verify_drop_identity(content: Path, paid_drop_ids: tuple[str, ...]) -> dict:
    group_path = content / "DataTable/Drop/Client/ClientDropGroupDataTable.json"
    sequence_path = content / "DataTable/Drop/DropSequenceDataTable.json"
    groups = rows(group_path)
    sequences = rows(sequence_path)
    checked = {}
    for drop_id in (*paid_drop_ids, "drop_fons1"):
        members = [
            (key, value) for key, value in groups.items()
            if key.casefold().startswith(f"{drop_id}_".casefold())
        ]
        if not members:
            raise ValueError(f"掉落组缺失：{drop_id}")
        currency = set()
        for _key, value in members:
            sequence_id = value.get("SequenceId")
            if not isinstance(sequence_id, str):
                raise ValueError(f"掉落序列缺失：{drop_id}")
            sequence_members = [
                row for key, row in sequences.items()
                if key.casefold().startswith(f"{sequence_id}_".casefold())
            ]
            if not sequence_members:
                raise ValueError(f"掉落序列为空：{drop_id}/{sequence_id}")
            currency.update(
                row.get("ItemID") for row in sequence_members
                if row.get("ItemID") in {"Fons", "Gold"}
            )
        expected = {"Fons"} if drop_id == "drop_fons1" else {"Gold"}
        if currency != expected:
            raise ValueError(f"货币来源冲突：{drop_id}，{currency}")
        checked[drop_id] = next(iter(expected))
    return {
        "drop_count": len(checked),
        "gold_drop_count": sum(value == "Gold" for value in checked.values()),
        "fons_drop_id": "drop_fons1",
        "group_sha256": sha256(group_path),
        "sequence_sha256": sha256(sequence_path),
    }


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts = {}
    for table in EXPECTED_COUNTS:
        column = "cost_item_id" if table.endswith("exp_material_cost") else "item_id"
        counts[table] = int(connection.execute(
            f"SELECT count(*) FROM {table} WHERE {column}='Fons'"
        ).fetchone()[0])
    return counts


def build_candidate(baseline: Path, manifest: Path, content: Path, output: Path) -> dict:
    baseline, manifest, content, output = (
        value.expanduser().resolve() for value in (baseline, manifest, content, output)
    )
    if output == baseline or output.is_relative_to(baseline.parent):
        raise ValueError("候选必须位于正式数据库目录之外")
    baseline_hash = sha256(baseline)
    if baseline_hash != EXPECTED_BASELINE_SHA256:
        raise ValueError("正式库哈希与本次审计基线不符")
    baseline_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    if baseline_manifest["database"]["sha256"].upper() != baseline_hash:
        raise ValueError("正式库与 manifest 不一致")
    with sqlite3.connect(f"{baseline.as_uri()}?mode=ro", uri=True) as connection:
        identity = connection.execute(
            "SELECT dataset_id, importer_version FROM dataset"
        ).fetchone()
        if identity != EXPECTED_DATASET:
            raise ValueError("正式数据集身份与审计基线不符")
        before = _counts(connection)
        if before != EXPECTED_COUNTS:
            raise ValueError(f"待修复记录数量变化：{before}")
        paid_drop_ids = tuple(row[0] for row in connection.execute(PAID_DROP_SQL))
        if len(paid_drop_ids) != 145 or "drop_fons1" in paid_drop_ids:
            raise ValueError("付费副本掉落范围变化")
        if connection.execute(
            "SELECT item_id FROM clone_drop_projection_item WHERE drop_id='drop_fons1'"
        ).fetchall() != [("Fons",)]:
            raise ValueError("玛门方斯来源变化")
    source_evidence = verify_drop_identity(content, paid_drop_ids)
    output.mkdir(parents=True, exist_ok=True)
    original = output / "baseline_game_static.sqlite3"
    modified = output / "game_static.sqlite3"
    if any(path.exists() for path in (original, modified)):
        raise FileExistsError("候选输出已存在；保留既有证据，不覆盖")
    shutil.copy2(baseline, original)
    if sha256(original) != baseline_hash:
        raise ValueError("基线备份哈希不一致")
    shutil.copy2(original, modified)
    with sqlite3.connect(modified) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(PATCH_SQL)
        connection.execute(
            "UPDATE dataset SET dataset_id=?, importer_version=?, built_at_utc=?",
            (CORRECTED_DATASET, CORRECTED_IMPORTER_VERSION,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        after = _counts(connection)
        expected_after = {table: 0 for table in EXPECTED_COUNTS}
        expected_after["clone_drop_projection_item"] = 1
        if after != expected_after:
            raise ValueError(f"修复仍有误映射：{after}")
        if connection.execute(
            "SELECT item_id FROM clone_drop_projection_item WHERE drop_id='drop_fons1'"
        ).fetchall() != [("Fons",)]:
            raise ValueError("玛门方斯来源被改写")
        if connection.execute(
            "SELECT item_id FROM progression_item_alias "
            "WHERE token='gold' AND context='progression_cost'"
        ).fetchall() != [("Gold",)]:
            raise ValueError("养成消耗别名未修复")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("候选外键校验失败")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("候选完整性校验失败")
        connection.commit()
    modified_hash = sha256(modified)
    (output / "changes.sql").write_text(PATCH_SQL, encoding="utf-8")
    shutil.copy2(manifest, output / "baseline_manifest.json")
    corrected_manifest = json.loads(json.dumps(baseline_manifest))
    corrected_manifest["database"].update({
        "dataset_id": CORRECTED_DATASET,
        "size_bytes": modified.stat().st_size,
        "sha256": modified_hash,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    corrected_manifest["build_tool"] = {
        "path": "tools/game_data/repair_published_gold_catalog.py",
        "importer_version": CORRECTED_IMPORTER_VERSION,
    }
    (output / "manifest.json").write_text(
        json.dumps(corrected_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    result = {
        "baseline_sha256": baseline_hash,
        "modified_sha256": modified_hash,
        "dataset": list(EXPECTED_DATASET),
        "corrected_dataset": [CORRECTED_DATASET, CORRECTED_IMPORTER_VERSION],
        "before_fons_counts": before,
        "after_fons_counts": after,
        "preserved_fons_drop": "drop_fons1",
        "source_evidence": source_evidence,
        "account_databases_modified": False,
        "release_database_modified": False,
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (output / "verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "gold_fix_provenance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rollback = (
        "param([string]$Target = 'F:\\NTE\\data\\game_static.sqlite3', "
        "[string]$TargetManifest = 'F:\\NTE\\data\\manifest.json')\n"
        "$ErrorActionPreference = 'Stop'\n"
        f"$baseline = '{original}'\n"
        f"$baselineManifest = '{output / 'baseline_manifest.json'}'\n"
        f"$expected = '{modified_hash}'\n"
        f"$originalHash = '{baseline_hash}'\n"
        "if ((Get-Process NTE_Drive_Calc -ErrorAction SilentlyContinue)) "
        "{ throw '请先关闭计算器进程' }\n"
        "if ((Get-FileHash -LiteralPath $baseline -Algorithm SHA256).Hash -ne $originalHash) "
        "{ throw '备份哈希不符' }\n"
        "if ((Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash -ne $expected) "
        "{ throw '目标不是本次候选' }\n"
        "Copy-Item -LiteralPath $baseline -Destination $Target -Force\n"
        "Copy-Item -LiteralPath $baselineManifest -Destination $TargetManifest -Force\n"
        "if ((Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash -ne $originalHash) "
        "{ throw '回滚验证失败' }\n"
    )
    (output / "rollback.ps1").write_text(rollback, encoding="utf-8")
    return result


def validate_gold_fix_provenance(
    *, candidate_database: Path, provenance_path: Path,
    baseline_database: Path, baseline_manifest: Path, official_source_root: Path,
) -> dict:
    """Verify exact audited table changes against the preserved release baseline."""

    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if sha256(baseline_database) != EXPECTED_BASELINE_SHA256:
        raise ValueError("甲硬币修复基线哈希变化")
    if provenance.get("baseline_sha256") != EXPECTED_BASELINE_SHA256:
        raise ValueError("甲硬币修复 provenance 基线错误")
    if provenance.get("modified_sha256") != sha256(candidate_database):
        raise ValueError("甲硬币修复候选哈希变化")
    baseline_info = json.loads(baseline_manifest.read_text(encoding="utf-8"))
    if baseline_info["database"]["sha256"].upper() != EXPECTED_BASELINE_SHA256:
        raise ValueError("甲硬币修复基线 manifest 不匹配")
    changed_tables = set(EXPECTED_COUNTS) | {"progression_item_alias", "dataset"}
    with (
        sqlite3.connect(f"{baseline_database.resolve().as_uri()}?mode=ro", uri=True) as before,
        sqlite3.connect(f"{candidate_database.resolve().as_uri()}?mode=ro", uri=True) as after,
    ):
        if before.execute("SELECT dataset_id,importer_version FROM dataset").fetchone() != EXPECTED_DATASET:
            raise ValueError("甲硬币修复基线 dataset 不匹配")
        if after.execute("SELECT dataset_id,importer_version FROM dataset").fetchone() != (
            CORRECTED_DATASET, CORRECTED_IMPORTER_VERSION,
        ):
            raise ValueError("甲硬币修复候选 dataset 不匹配")
        paid_ids = tuple(row[0] for row in before.execute(PAID_DROP_SQL))
        evidence = verify_drop_identity(official_source_root, paid_ids)
        if evidence != provenance.get("source_evidence"):
            raise ValueError("甲硬币修复来源证据变化")
        if _counts(before) != EXPECTED_COUNTS:
            raise ValueError("甲硬币修复基线数量变化")
        expected_after = {table: 0 for table in EXPECTED_COUNTS}
        expected_after["clone_drop_projection_item"] = 1
        if _counts(after) != expected_after:
            raise ValueError("甲硬币修复候选数量错误")
        if after.execute(
            "SELECT drop_id,item_id,quantity FROM clone_drop_projection_item "
            "WHERE item_id='Fons'"
        ).fetchall() != [("drop_fons1", "Fons", 1)]:
            raise ValueError("玛门方斯未保留")
        if after.execute("SELECT item_id FROM progression_item_alias WHERE token='gold' AND context='progression_cost'").fetchone() != ("Gold",):
            raise ValueError("养成别名未修复")
        if after.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("甲硬币修复候选外键错误")
        if after.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("甲硬币修复候选完整性错误")
        tables = tuple(row[0] for row in before.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ))
        if tables != tuple(row[0] for row in after.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )):
            raise ValueError("甲硬币修复候选 schema 表集合变化")
        for table in tables:
            if table in changed_tables:
                continue
            before_rows = before.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            after_rows = after.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            if before_rows != after_rows:
                raise ValueError(f"甲硬币修复误改其他表：{table}")
        for table in EXPECTED_COUNTS:
            before_rows = before.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            after_rows = after.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            column = "cost_item_id" if table.endswith("exp_material_cost") else "item_id"
            index = [row[1] for row in before.execute(f'PRAGMA table_info("{table}")')].index(column)
            patched = []
            for row in before_rows:
                values = list(row)
                if values[index] == "Fons" and (
                    table != "clone_drop_projection_item" or row[0] in paid_ids
                ):
                    values[index] = "Gold"
                patched.append(tuple(values))
            if patched != after_rows:
                raise ValueError(f"甲硬币修复表内容超出范围：{table}")
        before_alias = before.execute('SELECT * FROM progression_item_alias ORDER BY rowid').fetchall()
        after_alias = after.execute('SELECT * FROM progression_item_alias ORDER BY rowid').fetchall()
        expected_alias = [
            (row[0], row[1], "Gold", row[3])
            if row[:3] == ("gold", "progression_cost", "Fons") else row
            for row in before_alias
        ]
        if expected_alias != after_alias:
            raise ValueError("甲硬币修复别名超出范围")
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--content", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_candidate(args.baseline, args.manifest, args.content, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
