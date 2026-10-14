# 仅构造基础权重公共用例的合成静态输入，不读取或晋升发行库和账号资源。
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from tools.game_data.static_database_build_support import SCHEMA_PATHS, SCHEMA_VERSION


def build_basic_weight_static_fixture(database: Path) -> Path:
    """在用例临时目录复用正式 schema；数据与清单显式标为合成，不提供游戏能力。"""
    database = database.resolve()
    if database.exists() or database.with_name("manifest.json").exists():
        raise ValueError("合成夹具目标已存在，保留原文件")
    database.parent.mkdir(parents=True, exist_ok=True)
    dataset_id, timestamp = "basic-weight-synthetic-fixture", "2000-01-01T00:00:00Z"
    synthetic_sha = hashlib.sha256(dataset_id.encode()).hexdigest()
    with closing(sqlite3.connect(database)) as connection, connection:
        for schema in SCHEMA_PATHS:
            connection.executescript(schema.read_text(encoding="utf-8"))
            connection.execute("INSERT INTO schema_migration VALUES (?, ?)",
                               (int(schema.name.split("_", 1)[0]), timestamp))
        connection.execute("INSERT INTO dataset VALUES (?, ?, ?)", (dataset_id, 1, timestamp))
        connection.execute("INSERT INTO dataset_scope VALUES (?, ?)", (dataset_id, "game"))
        connection.execute("INSERT INTO source_file VALUES (1, ?, ?, 7)",
                           ("synthetic/basic-weight.json", synthetic_sha))
        for ordinal in range(1, 8):
            connection.execute("INSERT INTO source_row VALUES (?, 1, ?, NULL, ?)",
                               (ordinal, f"synthetic:{ordinal}", synthetic_sha))
        for ordinal, cid in enumerate((1001, 1002, 1046, 1051), 1):
            connection.execute("INSERT INTO character(character_id, name_zh, source_row_id) VALUES (?, ?, ?)",
                               (cid, f"合成角色 {cid}", ordinal))
            logical = "protagonist" if cid in (1046, 1051) else f"fixture:{cid}"
            canonical = 1046 if cid in (1046, 1051) else cid
            classification = "available_avatar_variant" if cid in (1046, 1051) else "available_character"
            connection.execute("INSERT INTO character_annotation VALUES (?, ?, ?, ?, ?)",
                               (cid, logical, canonical, classification, "synthetic_fixture"))
        for ordinal, pid, label in ((5, "CritBase", "暴击率"), (6, "AtkUp", "攻击力百分比")):
            connection.execute(
                "INSERT INTO equipment_attribute(attribute_id, display_name_zh, filter_name_zh, "
                "show_percent, show_outside, show_inside, source_row_id) VALUES (?, ?, ?, 1, 1, 1, ?)",
                (pid, label, label, ordinal),
            )
        connection.execute("INSERT INTO equipment_suit(suit_id, name_zh, source_row_id) VALUES (?, ?, 7)",
                           ("fixture-suit", "合成套装"))
        for cid in (1001, 1002, 1046):
            connection.execute("INSERT INTO character_weight_recommendation VALUES (?, 'default', NULL, ?, ?)",
                               (cid, "synthetic_fixture", timestamp))
            for ordinal, pid, weight in ((0, "CritBase", 1.0), (1, "AtkUp", 0.8)):
                connection.execute("INSERT INTO character_weight_recommendation_property VALUES (?, ?, ?, 0, ?)",
                                   (cid, pid, weight, ordinal))
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise AssertionError("合成静态库完整性校验失败")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise AssertionError("合成静态库外键校验失败")
    payload = database.read_bytes()
    manifest = {
        "catalog_scope": "game", "synthetic_fixture": True,
        "database": {"filename": database.name, "dataset_id": dataset_id,
                     "schema_version": SCHEMA_VERSION, "size_bytes": len(payload),
                     "sha256": hashlib.sha256(payload).hexdigest(), "source_payloads_omitted": True},
        "build_tool": {"name": "basic_weight_fixture", "importer_version": 1},
    }
    database.with_name("manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                   encoding="utf-8")
    return database
