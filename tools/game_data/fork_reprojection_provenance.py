# 核验弧盘常驻属性重投影候选只改变经来源证明的静态表。
"""Baseline-bound provenance for a fork-only static database projection."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from src.storage.sqlite.catalog.fork_permanent_projection import (
    FORK_PERMANENT_EVIDENCE_SQL,
    FORK_REFINEMENT_LEVEL_SQL,
    FORK_SOURCE_COVERAGE_SQL,
    cursor_dicts,
    resolve_projection_rows,
)
from tools.game_data.static_database_build_support import IMPORTER_VERSION, SCHEMA_VERSION


PROVENANCE_FILENAME = "fork_reprojection_provenance.json"
_CHANGED_TABLES = frozenset({
    "dataset", "schema_migration", "buff_definition", "buff_modifier",
    "buff_trigger_effect", "fork_permanent_property", "fork_permanent_review",
    "character_graduation_template",
})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _fingerprint(connection: sqlite3.Connection, table: str) -> str:
    if not table.replace("_", "").isalnum():
        raise ValueError(f"非法表名：{table}")
    digest = hashlib.sha256()
    for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
        values = [
            {"blob_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
            if isinstance(value, bytes) else value
            for value in row
        ]
        digest.update(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest().upper()


def _rows_for_assets(
    connection: sqlite3.Connection, table: str, assets: set[str] | None = None,
) -> list[tuple[Any, ...]]:
    rows = [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')]
    if assets is not None:
        rows = [row for row in rows if str(row[0]) in assets]
    return sorted(rows, key=repr)


def validate_fork_reprojection_provenance(
    *, candidate_database: Path, provenance_path: Path,
    baseline_database: Path, baseline_manifest: Path,
) -> dict[str, Any]:
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if provenance.get("kind") != "fork_permanent_reprojection_v1":
        raise ValueError("弧盘候选 provenance 类型不符")
    for path, field in (
        (baseline_database, "baseline_database_sha256"),
        (baseline_manifest, "baseline_manifest_sha256"),
        (candidate_database, "candidate_database_sha256"),
    ):
        if _sha256(path) != provenance.get(field):
            raise ValueError(f"弧盘候选 {field} 已变化")
    with closing(_open_readonly(baseline_database)) as old, closing(
        _open_readonly(candidate_database)
    ) as new:
        prior_tables, next_tables = _tables(old), _tables(new)
        if next_tables != prior_tables | {"fork_permanent_review"}:
            raise ValueError("弧盘候选表集合存在额外变化")
        for table in sorted(prior_tables - _CHANGED_TABLES):
            if _fingerprint(old, table) != _fingerprint(new, table):
                raise ValueError(f"弧盘候选改动了非目标表：{table}")
        old_dataset = old.execute("SELECT dataset_id FROM dataset").fetchone()[0]
        dataset = new.execute(
            "SELECT dataset_id, importer_version FROM dataset"
        ).fetchone()
        if dataset != (old_dataset, IMPORTER_VERSION):
            raise ValueError("弧盘候选 dataset/importer 身份不符")
        old_versions = {int(row[0]) for row in old.execute("SELECT version FROM schema_migration")}
        new_versions = {int(row[0]) for row in new.execute("SELECT version FROM schema_migration")}
        if new_versions != old_versions | {SCHEMA_VERSION}:
            raise ValueError("弧盘候选 schema 迁移不符")

        prior_buffs = {str(row[0]) for row in old.execute("SELECT asset_path FROM buff_definition")}
        next_buffs = {str(row[0]) for row in new.execute("SELECT asset_path FROM buff_definition")}
        added_buffs = next_buffs - prior_buffs
        if not added_buffs or prior_buffs - next_buffs:
            raise ValueError("弧盘候选 Buff 增量不符")
        linked_calculations = {
            str(row[0]) for row in new.execute("""
                SELECT DISTINCT asset.asset_path
                FROM combat_blueprint_asset AS asset
                JOIN combat_effect_buff_link AS link
                  ON link.target_asset_path = asset.asset_path
                WHERE asset.asset_kind = 'calculation'
                  AND link.effect_definition_id LIKE 'fork_star:%'
            """)
        }
        if not added_buffs <= linked_calculations:
            raise ValueError("弧盘候选新增了非精炼计算 Buff")
        if _rows_for_assets(old, "buff_definition") != _rows_for_assets(
            new, "buff_definition", prior_buffs
        ):
            raise ValueError("弧盘候选改动了原有 Buff 定义")
        for table in ("buff_modifier", "buff_trigger_effect"):
            if _rows_for_assets(old, table) != _rows_for_assets(new, table, prior_buffs):
                raise ValueError(f"弧盘候选改动了原有 {table}")
            if any(str(row[0]) not in added_buffs for row in _rows_for_assets(new, table)
                   if str(row[0]) not in prior_buffs):
                raise ValueError(f"弧盘候选 {table} 出现非精炼增量")

        resolved, audit = resolve_projection_rows(
            cursor_dicts(new.execute(FORK_PERMANENT_EVIDENCE_SQL)),
            cursor_dicts(new.execute(FORK_REFINEMENT_LEVEL_SQL)),
            cursor_dicts(new.execute(FORK_SOURCE_COVERAGE_SQL)),
        )
        actual_properties = _rows_for_assets(new, "fork_permanent_property")
        expected_properties = sorted((
            value.fork_id, value.refinement_level, value.property_id,
            value.modifier_operation, value.property_value,
            value.parameter_name_id, value.effect_definition_id,
            value.calculation_asset_path, value.source_row_id,
        ) for value in resolved)
        if actual_properties != expected_properties:
            raise ValueError("弧盘候选常驻属性与来源重算不符")
        actual_review = _rows_for_assets(new, "fork_permanent_review")
        expected_review = sorted((
            item.fork_id, item.status, len(item.expected_levels),
            len(item.resolved_levels), item.candidate_count, item.detail,
        ) for item in audit)
        if actual_review != expected_review or len(audit) != int(
            new.execute("SELECT COUNT(*) FROM fork_item").fetchone()[0]
        ):
            raise ValueError("弧盘候选逐件审查与来源重算不符")
        if new.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("弧盘候选 SQLite 完整性失败")
        if new.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("弧盘候选外键完整性失败")
    return {
        "added_buff_count": len(added_buffs),
        "reviewed_forks": len(audit),
        "permanent_rows": len(resolved),
    }


def write_fork_reprojection_provenance(
    candidate_database: Path, baseline_database: Path,
    baseline_manifest: Path, destination: Path,
) -> dict[str, Any]:
    provenance = {
        "kind": "fork_permanent_reprojection_v1",
        "baseline_database_sha256": _sha256(baseline_database),
        "baseline_manifest_sha256": _sha256(baseline_manifest),
        "candidate_database_sha256": _sha256(candidate_database),
    }
    destination.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    provenance.update(validate_fork_reprojection_provenance(
        candidate_database=candidate_database,
        provenance_path=destination,
        baseline_database=baseline_database,
        baseline_manifest=baseline_manifest,
    ))
    destination.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return provenance
