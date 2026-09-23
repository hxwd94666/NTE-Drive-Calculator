# 将已发行旧静态库按来源行哈希升级为当前 schema 候选。
"""Build an additive static release candidate from a verified prior release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.sqlite.fork_permanent_projection import (
    FORK_PERMANENT_EVIDENCE_SQL,
    FORK_REFINEMENT_LEVEL_SQL,
    cursor_dicts,
    resolve_projection_rows,
)
from tools.game_data.build_graduation_templates import (
    populate_graduation_templates,
)
from tools.game_data.catalog_characters import load_datatable
from tools.game_data.static_database_build_support import (
    IMPORTER_VERSION,
    SCHEMA_PATHS,
    SCHEMA_VERSION,
    StaticDatabaseError,
    canonical_json,
    sha256_bytes,
    write_static_manifest,
)
from tools.game_data.static_database_fork_progression_imports import (
    fork_exp_material_spec,
)
from tools.game_data.static_database_passive_imports import (
    import_catalog_passives,
)
from tools.game_data.static_database_progression_imports import (
    _parse_cost_string,
)


PROVENANCE_FILENAME = "upgrade_provenance.json"
ITEM_CATALOG_PATH = Path("DataTable/Inventory/DT_ItemConfig.json")
CHARACTER_ABILITIES_PATH = Path(
    "DataTable/Character/DT_CharacterAbilityConfig.json"
)
EXPECTED_FORK_EXP_ITEMS = frozenset({
    "WeaponUpMaterial_lv1",
    "WeaponUpMaterial_lv2",
    "WeaponUpMaterial_lv3",
})
MUTATED_BASELINE_TABLES = frozenset({
    "character_graduation_template",
    "dataset",
    "schema_migration",
})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def table_fingerprint(
    connection: sqlite3.Connection,
    table_name: str,
) -> str:
    """Hash every stored value in stable rowid order for migration auditing."""

    quoted = '"' + table_name.replace('"', '""') + '"'
    digest = hashlib.sha256()
    for row in connection.execute(f"SELECT * FROM {quoted} ORDER BY rowid"):
        digest.update(canonical_json(list(row)).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest().upper()


def table_names(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def source_file_rows(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, int], ...]:
    return tuple(
        (str(row[0]), str(row[1]), int(row[2]))
        for row in connection.execute(
            "SELECT relative_path,sha256,row_count "
            "FROM source_file ORDER BY relative_path"
        )
    )


def _source_row_hashes(
    connection: sqlite3.Connection,
    relative_path: str,
) -> dict[str, tuple[int, str]]:
    return {
        str(row[0]): (int(row[1]), str(row[2]))
        for row in connection.execute(
            """
            SELECT row.row_key,row.source_row_id,row.content_sha256
            FROM source_row AS row
            JOIN source_file AS file USING(source_file_id)
            WHERE file.relative_path=?
            """,
            (relative_path,),
        )
    }


def _verified_rows(
    connection: sqlite3.Connection,
    source_root: Path,
    relative_path: Path,
    row_keys: Iterable[str],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    source_path = source_root / relative_path
    if not source_path.is_file():
        raise StaticDatabaseError(f"升级来源文件不存在：{source_path}")
    _metadata, rows = load_datatable(source_path)
    recorded = _source_row_hashes(connection, relative_path.as_posix())
    verified: dict[str, Any] = {}
    provenance: list[dict[str, str]] = []
    for row_key in sorted(dict.fromkeys(str(value) for value in row_keys)):
        row = rows.get(row_key)
        identity = recorded.get(row_key)
        if row is None or identity is None:
            raise StaticDatabaseError(
                f"升级来源缺少已发行记录：{relative_path.as_posix()}#{row_key}"
            )
        content_hash = sha256_bytes(canonical_json(row).encode("utf-8"))
        if content_hash != identity[1]:
            raise StaticDatabaseError(
                f"升级来源行哈希变化：{relative_path.as_posix()}#{row_key}"
            )
        verified[row_key] = row
        provenance.append({
            "relative_path": relative_path.as_posix(),
            "row_key": row_key,
            "content_sha256": content_hash,
        })
    return verified, provenance


class _PassiveImportAdapter:
    def __init__(
        self,
        connection: sqlite3.Connection,
        rows: dict[str, Any],
        source_ids: dict[str, tuple[int, str]],
    ) -> None:
        self.connection = connection
        self.rows = {"character_abilities": rows}
        self._source_ids = source_ids

    def source_row_id(self, table: str, row_key: str) -> int:
        if table != "character_abilities" or row_key not in self._source_ids:
            raise StaticDatabaseError(f"升级来源行身份缺失：{table}/{row_key}")
        return self._source_ids[row_key][0]


def _apply_migrations(connection: sqlite3.Connection) -> None:
    current = int(connection.execute(
        "SELECT COALESCE(MAX(version),0) FROM schema_migration"
    ).fetchone()[0])
    if current != 31:
        raise StaticDatabaseError(f"增量升级输入 schema 必须为 31，实际为 {current}")
    applied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for schema_path in SCHEMA_PATHS:
        version = int(schema_path.name.split("_", 1)[0])
        if version <= current:
            continue
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_migration VALUES (?,?)",
            (version, applied_at),
        )
    final = int(connection.execute(
        "SELECT MAX(version) FROM schema_migration"
    ).fetchone()[0])
    if final != SCHEMA_VERSION:
        raise StaticDatabaseError(
            f"增量升级未到达当前 schema：{final}/{SCHEMA_VERSION}"
        )


def _import_fork_permanent_properties(
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    resolved, audit = resolve_projection_rows(
        cursor_dicts(connection.execute(FORK_PERMANENT_EVIDENCE_SQL)),
        cursor_dicts(connection.execute(FORK_REFINEMENT_LEVEL_SQL)),
    )
    connection.executemany(
        """
        INSERT INTO fork_permanent_property(
            fork_id,refinement_level,property_id,modifier_operation,
            property_value,source_parameter_name_id,
            source_effect_definition_id,source_calculation_asset_path,
            source_row_id
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            (
                value.fork_id,
                value.refinement_level,
                value.property_id,
                value.modifier_operation,
                value.property_value,
                value.parameter_name_id,
                value.effect_definition_id,
                value.calculation_asset_path,
                value.source_row_id,
            )
            for value in resolved
        ),
    )
    statuses = Counter(item.status for item in audit)
    return {
        "rows": len(resolved),
        "forks": len({item.fork_id for item in resolved}),
        "audit_statuses": dict(sorted(statuses.items())),
    }


def _import_fork_exp_materials(
    connection: sqlite3.Connection,
    rows: dict[str, Any],
    source_ids: dict[str, tuple[int, str]],
) -> int:
    imported: set[str] = set()
    for item_id in sorted(EXPECTED_FORK_EXP_ITEMS):
        specification = fork_exp_material_spec(
            item_id,
            rows[item_id],
            parse_cost_string=_parse_cost_string,
        )
        if specification is None:
            raise StaticDatabaseError(f"弧盘经验材料未被识别：{item_id}")
        experience, costs = specification
        connection.execute(
            "INSERT INTO fork_exp_material VALUES (?,?,?)",
            (item_id, experience, source_ids[item_id][0]),
        )
        for token, quantity in costs:
            cost_item_id = "Fons" if token.strip().lower() in {"gold", "fons"} else token
            connection.execute(
                "INSERT INTO fork_exp_material_cost VALUES (?,?,?)",
                (item_id, cost_item_id, quantity),
            )
        imported.add(item_id)
    if imported != EXPECTED_FORK_EXP_ITEMS:
        raise StaticDatabaseError(f"弧盘经验材料集合不完整：{sorted(imported)}")
    return len(imported)


def build_upgrade_candidate(
    *,
    source_database: Path,
    source_manifest: Path,
    official_source_root: Path,
    output_database: Path,
    dataset_id: str,
    config_dir: Path,
) -> dict[str, Any]:
    """Copy, migrate, backfill, verify, and atomically install one candidate."""

    source_database = source_database.expanduser().resolve()
    source_manifest = source_manifest.expanduser().resolve()
    official_source_root = official_source_root.expanduser().resolve()
    output_database = output_database.expanduser().resolve()
    if source_database == output_database:
        raise ValueError("升级输入与输出必须不同")
    if not source_database.is_file() or not source_manifest.is_file():
        raise FileNotFoundError("升级输入数据库或 manifest 缺失")
    output_database.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output_database.name}.",
        suffix=".tmp",
        dir=output_database.parent,
    )
    os.close(handle)
    temporary = Path(temporary_name)
    baseline_hash = sha256(source_database)
    try:
        shutil.copy2(source_database, temporary)
        with closing(sqlite3.connect(temporary)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            baseline_tables = table_names(connection)
            preserved_tables = tuple(
                table for table in baseline_tables
                if table not in MUTATED_BASELINE_TABLES
            )
            preserved_hashes = {
                table: table_fingerprint(connection, table)
                for table in preserved_tables
            }
            baseline_sources = source_file_rows(connection)
            character_ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT character_id FROM character ORDER BY character_id"
                )
            )
            passive_source_ids = _source_row_hashes(
                connection, CHARACTER_ABILITIES_PATH.as_posix()
            )
            passive_rows, passive_provenance = _verified_rows(
                connection,
                official_source_root,
                CHARACTER_ABILITIES_PATH,
                (
                    character_id for character_id in character_ids
                    if character_id in passive_source_ids
                ),
            )
            material_rows, material_provenance = _verified_rows(
                connection,
                official_source_root,
                ITEM_CATALOG_PATH,
                EXPECTED_FORK_EXP_ITEMS,
            )
            material_source_ids = _source_row_hashes(
                connection, ITEM_CATALOG_PATH.as_posix()
            )

            _apply_migrations(connection)
            old_dataset_id = str(connection.execute(
                "SELECT dataset_id FROM dataset"
            ).fetchone()[0])
            built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            connection.execute(
                "UPDATE dataset SET dataset_id=?,importer_version=?,built_at_utc=?",
                (dataset_id, IMPORTER_VERSION, built_at),
            )
            connection.execute(
                "INSERT INTO dataset_scope VALUES (?, 'game')",
                (dataset_id,),
            )
            fork_projection = _import_fork_permanent_properties(connection)
            import_catalog_passives(_PassiveImportAdapter(
                connection,
                passive_rows,
                passive_source_ids,
            ))
            passive_count = int(connection.execute(
                "SELECT COUNT(*) FROM catalog_character_passive"
            ).fetchone()[0])
            material_count = _import_fork_exp_materials(
                connection,
                material_rows,
                material_source_ids,
            )
            connection.commit()
            graduation_count = populate_graduation_templates(
                connection,
                database_path=temporary,
                config_dir=config_dir.expanduser().resolve(),
            )
            if source_file_rows(connection) != baseline_sources:
                raise StaticDatabaseError("增量升级改写了既有 source_file 事实")
            changed_preserved = [
                table for table, expected in preserved_hashes.items()
                if table_fingerprint(connection, table) != expected
            ]
            if changed_preserved:
                raise StaticDatabaseError(
                    f"增量升级改写了未授权旧表：{changed_preserved}"
                )
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            integrity = str(connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0])
            if violations or integrity != "ok":
                raise StaticDatabaseError(
                    f"增量候选校验失败：foreign_keys={violations[:5]}，"
                    f"integrity={integrity}"
                )
            if int(connection.execute(
                "SELECT COUNT(*) FROM source_row WHERE payload_json IS NOT NULL"
            ).fetchone()[0]):
                raise StaticDatabaseError("增量候选仍含来源 payload")
            connection.commit()

        os.replace(temporary, output_database)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    provenance = {
        "format_version": 1,
        "upgrade_kind": "additive_published_static_release",
        "baseline": {
            "database_sha256": baseline_hash,
            "manifest_sha256": sha256(source_manifest),
            "schema_version": 31,
            "dataset_id": old_dataset_id,
        },
        "candidate": {
            "database_sha256": sha256(output_database),
            "schema_version": SCHEMA_VERSION,
            "importer_version": IMPORTER_VERSION,
            "dataset_id": dataset_id,
        },
        "verified_source_rows": passive_provenance + material_provenance,
        "preserved_table_sha256": preserved_hashes,
        "mutated_baseline_tables": sorted(MUTATED_BASELINE_TABLES),
        "new_table_counts": {
            "fork_permanent_property": fork_projection["rows"],
            "catalog_character_passive": passive_count,
            "fork_exp_material": material_count,
            "graduation_templates": graduation_count,
        },
        "fork_permanent_projection": fork_projection,
    }
    provenance_path = output_database.parent / PROVENANCE_FILENAME
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path = output_database.parent / "manifest.json"
    write_static_manifest(output_database, manifest_path)
    report_dir = output_database.parent / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "upgrade_report.json").write_text(
        json.dumps({
            **provenance,
            "output_database": str(output_database),
            "output_size_bytes": output_database.stat().st_size,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return provenance


def validate_upgrade_provenance(
    *,
    candidate_database: Path,
    provenance_path: Path,
    baseline_database: Path,
    baseline_manifest: Path,
    official_source_root: Path,
) -> dict[str, Any]:
    """Reopen every migration input and prove the additive release boundary."""

    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if not isinstance(provenance, dict) or provenance.get("format_version") != 1:
        raise StaticDatabaseError("增量升级 provenance 格式无效")
    baseline = provenance.get("baseline") or {}
    candidate = provenance.get("candidate") or {}
    if sha256(baseline_database) != baseline.get("database_sha256"):
        raise StaticDatabaseError("增量升级基线数据库哈希不一致")
    if sha256(baseline_manifest) != baseline.get("manifest_sha256"):
        raise StaticDatabaseError("增量升级基线 manifest 哈希不一致")
    if sha256(candidate_database) != candidate.get("database_sha256"):
        raise StaticDatabaseError("增量升级候选数据库哈希不一致")

    with closing(sqlite3.connect(baseline_database)) as before, closing(
        sqlite3.connect(candidate_database)
    ) as after:
        if source_file_rows(before) != source_file_rows(after):
            raise StaticDatabaseError("增量升级候选的 source_file 已漂移")
        expected_fingerprints = provenance.get("preserved_table_sha256") or {}
        for table, expected in expected_fingerprints.items():
            if table_fingerprint(before, str(table)) != expected:
                raise StaticDatabaseError(f"增量升级基线表哈希变化：{table}")
            if table_fingerprint(after, str(table)) != expected:
                raise StaticDatabaseError(f"增量升级候选改写旧表：{table}")
        source_cache: dict[str, dict[str, Any]] = {}
        baseline_rows: dict[str, dict[str, tuple[int, str]]] = {}
        for identity in provenance.get("verified_source_rows") or ():
            relative_path = str(identity["relative_path"])
            row_key = str(identity["row_key"])
            rows = source_cache.get(relative_path)
            if rows is None:
                _metadata, rows = load_datatable(
                    official_source_root / Path(relative_path)
                )
                source_cache[relative_path] = rows
            row = rows.get(row_key)
            actual = (
                sha256_bytes(canonical_json(row).encode("utf-8"))
                if row is not None else ""
            )
            if actual != identity["content_sha256"]:
                raise StaticDatabaseError(
                    f"增量升级来源行变化：{relative_path}#{row_key}"
                )
            recorded = baseline_rows.setdefault(
                relative_path,
                _source_row_hashes(before, relative_path),
            ).get(row_key)
            if recorded is None or recorded[1] != actual:
                raise StaticDatabaseError(
                    f"增量升级来源行与基线不一致：{relative_path}#{row_key}"
                )
    return provenance


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--official-source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build_upgrade_candidate(
        source_database=args.source_database,
        source_manifest=args.source_manifest,
        official_source_root=args.official_source_root,
        output_database=args.output,
        dataset_id=args.dataset_id,
        config_dir=args.config_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
