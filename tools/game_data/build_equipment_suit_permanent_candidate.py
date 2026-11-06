# 将已复核的套装常驻录入独立审查候选，不覆盖正式静态或冒充可晋升的game资料包。
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.game_data.equipment_suit_permanent import effect_evidence, project_review

KIND = "equipment_suit_permanent_review_candidate_v1"
SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA user_version=1;
CREATE TABLE candidate_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE equipment_suit_effect_review(
    suit_id TEXT NOT NULL, required_count INTEGER NOT NULL CHECK(required_count IN (2,4)),
    name_zh TEXT NOT NULL, classification TEXT NOT NULL CHECK(classification IN ('permanent_only','mixed','conditional_only')),
    has_conditional INTEGER NOT NULL CHECK(has_conditional IN (0,1)), permanent_count INTEGER NOT NULL,
    source_effect_definition_id TEXT NOT NULL, source_effect_row_id INTEGER NOT NULL,
    evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64), evidence_json TEXT NOT NULL,
    note TEXT NOT NULL, PRIMARY KEY(suit_id,required_count));
CREATE TABLE equipment_suit_permanent_property(
    suit_id TEXT NOT NULL, required_count INTEGER NOT NULL, property_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK(operation='additive'), value REAL NOT NULL,
    unit TEXT NOT NULL CHECK(unit='ratio'), source_kind TEXT NOT NULL CHECK(source_kind IN ('modify_pack','buff_modifier')),
    source_key TEXT NOT NULL, source_ordinal INTEGER NOT NULL, curve_id TEXT,
    PRIMARY KEY(suit_id,required_count,property_id),
    FOREIGN KEY(suit_id,required_count) REFERENCES equipment_suit_effect_review(suit_id,required_count));
"""


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_candidate(database: Path, reviews: Path, output: Path) -> dict:
    """唯一输出域是build；所有证据先闭合，再原子生成新的小型候选，失败不留正式结果。"""
    database, reviews, output = database.resolve(), reviews.resolve(), output.resolve()
    if not output.is_relative_to(ROOT / "build") or output == database or output.exists():
        raise ValueError("候选须为build中的新文件，不覆盖现有输入或结果")
    review_bytes = reviews.read_bytes()
    review_hash = hashlib.sha256(review_bytes).hexdigest()
    policy = json.loads(review_bytes)
    if policy.get("format") != "equipment_suit_permanent_reviews_v1":
        raise ValueError("套装审查格式不匹配")
    original = file_hash(database)
    if policy["source_database_sha256"] != original:
        raise ValueError("静态来源哈希已变化，需重新逐条审查")
    projected = []
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        dataset = connection.execute("SELECT dataset_id FROM dataset").fetchone()[0]
        schema = connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0]
        scope = connection.execute("SELECT scope FROM dataset_scope").fetchone()[0]
        if (dataset, schema, scope) != (policy["source_dataset_id"], policy["source_static_schema"], "game"):
            raise ValueError("套装审查只消费指定完整主静态身份")
        actual = set(connection.execute("SELECT suit_id,required_count FROM equipment_suit_effect"))
        selected = [(row["suit_id"], row["required_count"]) for row in policy["reviews"]]
        if len(selected) != len(set(selected)) or set(selected) != actual:
            raise ValueError("套装审查未逐条覆盖，或存在重复身份")
        for review in policy["reviews"]:
            evidence = effect_evidence(connection, review["suit_id"], review["required_count"])
            projected.append(project_review(evidence, review))
    if file_hash(database) != original or file_hash(reviews) != review_hash:
        raise ValueError("审查期间静态或审查输入变化")
    properties = sum(len(values) for _, values in projected)
    metadata = {"artifact_kind": KIND, "release_ready": False, "runtime_integration": "pending",
                "source_dataset_id": dataset, "source_static_schema": schema, "source_database_sha256": original,
                "review_policy_sha256": review_hash, "effect_count": len(projected),
                "property_count": properties, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "promotion_note": "独立审查录入产物；非主静态包。正式迁移、importer和消费者闭合后另走晋升。"}
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="suit-candidate-", suffix=".sqlite3", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        with closing(sqlite3.connect(temporary)) as target:
            target.executescript(SCHEMA)
            with target:
                target.executemany("INSERT INTO candidate_metadata VALUES (?,?)", (
                    (key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()))
                for row, values in projected:
                    target.execute("INSERT INTO equipment_suit_effect_review VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                        row["suit_id"], row["required_count"], row["name_zh"], row["classification"],
                        int(row["has_conditional"]), row["permanent_count"], row["source_effect_definition_id"],
                        row["source_effect_row_id"], row["evidence_sha256"],
                        json.dumps(row["evidence"], ensure_ascii=False, sort_keys=True), row["note"]))
                    target.executemany("INSERT INTO equipment_suit_permanent_property VALUES (?,?,?,?,?,?,?,?,?,?)", (
                        (row["suit_id"], row["required_count"], value["property_id"], value["operation"], value["value"],
                         value["unit"], value["source_kind"], value["source_key"], value["source_ordinal"], value["curve_id"])
                        for value in values))
            if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or target.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("候选完整性校验失败")
        if output.exists() or file_hash(database) != original or file_hash(reviews) != review_hash:
            raise ValueError("结果路径或审查输入在提交前变化")
        temporary.replace(output)
        with closing(sqlite3.connect(output.as_uri() + "?mode=ro", uri=True)) as check:
            assert check.execute("SELECT COUNT(*) FROM equipment_suit_effect_review").fetchone()[0] == len(projected)
            assert check.execute("SELECT COUNT(*) FROM equipment_suit_permanent_property").fetchone()[0] == properties
    finally:
        temporary.unlink(missing_ok=True)
    return {**metadata, "output": str(output), "sha256": file_hash(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "data/game_static.sqlite3")
    parser.add_argument("--reviews", type=Path, default=Path(__file__).with_name("equipment_suit_permanent_reviews.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_candidate(args.database, args.reviews, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
