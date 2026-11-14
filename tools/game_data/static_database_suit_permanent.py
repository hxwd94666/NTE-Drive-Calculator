# 将逐条审查过的套装来源导入主静态，并在晋升前从本次来源重算核对。
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from tools.game_data.equipment_suit_permanent import digest, effect_evidence, project_review

POLICY = Path(__file__).with_name("equipment_suit_permanent_reviews.json")


def reviewed_properties(connection: sqlite3.Connection, policy_path: Path = POLICY):
    raw = policy_path.read_bytes()
    policy = json.loads(raw)
    if policy.get("format") != "equipment_suit_permanent_reviews_v1":
        raise ValueError("套装审查格式无效")
    actual = set(connection.execute("SELECT suit_id,required_count FROM equipment_suit_effect").fetchall())
    selected = {(row['suit_id'], row['required_count']) for row in policy['reviews']}
    # row_factory可能属于构建owner，统一取值比较，不替换或关闭该owner。
    actual = {tuple(row) for row in actual}
    if actual != selected or len(selected) != len(policy['reviews']):
        raise ValueError("套装审查尚未覆盖本次全部效果")
    rows = []
    for review in policy['reviews']:
        evidence = effect_evidence(connection, review['suit_id'], review['required_count'])
        checked, properties = project_review(evidence, review)
        rows.append((checked, properties, digest(evidence)))
    if policy_path.read_bytes() != raw:
        raise ValueError("套装审查输入已变化")
    return rows, hashlib.sha256(raw).hexdigest()


def populate_suit_permanent(connection: sqlite3.Connection, policy_path: Path = POLICY) -> None:
    rows, policy_hash = reviewed_properties(connection, policy_path)
    connection.execute("SAVEPOINT suit_permanent_import")
    try:
        connection.execute("DELETE FROM equipment_suit_permanent_property")
        connection.execute("DELETE FROM equipment_suit_effect_review")
        for review, properties, evidence_hash in rows:
            sid, count = review['suit_id'], review['required_count']
            connection.execute("INSERT INTO equipment_suit_effect_review VALUES (?,?,?,?,?,?,?)", (
                sid, count, review['classification'], evidence_hash, policy_hash,
                review['source_effect_definition_id'], review['note']))
            connection.executemany("INSERT INTO equipment_suit_permanent_property VALUES (?,?,?,?,?,?,?,?,?,?)", (
                (sid, count, row['property_id'], row['value'], row['unit'], row['operation'],
                 row['source_kind'], row['source_key'], row['source_ordinal'], row['curve_id']) for row in properties))
    except BaseException:
        connection.execute("ROLLBACK TO suit_permanent_import")
        connection.execute("RELEASE suit_permanent_import")
        raise
    connection.execute("RELEASE suit_permanent_import")


def validate_suit_permanent(connection: sqlite3.Connection, policy_path: Path = POLICY) -> None:
    rows, policy_hash = reviewed_properties(connection, policy_path)
    expected = []
    for review, properties, evidence_hash in rows:
        actual = connection.execute("SELECT classification,evidence_sha256,policy_sha256,source_effect_id "
            "FROM equipment_suit_effect_review WHERE suit_id=? AND required_count=?",
            (review['suit_id'], review['required_count'])).fetchone()
        if actual is None or tuple(actual) != (review['classification'], evidence_hash, policy_hash, review['source_effect_definition_id']):
            raise ValueError("套装审查记录与正式来源不一致")
        expected.extend((review['suit_id'],review['required_count'],r['property_id'],r['value'],r['unit'],r['operation'],
                         r['source_kind'],r['source_key'],r['source_ordinal'],r['curve_id']) for r in properties)
    actual = connection.execute("SELECT * FROM equipment_suit_permanent_property ORDER BY suit_id,required_count,property_id").fetchall()
    if sorted(expected) != [tuple(row) for row in actual]:
        raise ValueError("套装常驻属性与审查来源不一致")
    if connection.execute("SELECT COUNT(*) FROM equipment_suit_effect_review").fetchone()[0] != len(rows):
        raise ValueError("套装审查存在额外记录")
