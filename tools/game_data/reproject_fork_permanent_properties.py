# 在静态候选副本中重建 v32 弧盘常驻属性及毕业模板。
"""Rebuild only the v32 fork-permanent projection in a copied static candidate."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.storage.sqlite.fork_permanent_projection import (
    FORK_PERMANENT_EVIDENCE_SQL,
    FORK_REFINEMENT_LEVEL_SQL,
    cursor_dicts,
    resolve_projection_rows,
)
from tools.game_data.build_graduation_templates import populate_graduation_templates
from tools.game_data.static_database_build_support import (
    IMPORTER_VERSION,
    file_sha256,
)


def reproject_candidate(
    source: Path,
    output: Path,
    audit_path: Path,
    *,
    config_dir: Path = PROJECT_ROOT / "config",
) -> dict[str, Any]:
    """Copy one normalized candidate, replace its projection, and verify it."""

    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    audit_path = audit_path.expanduser().resolve()
    if source == output:
        raise ValueError("输入库与输出库必须不同，以保留可回滚原件")
    if not source.is_file():
        raise FileNotFoundError(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        with closing(sqlite3.connect(temporary)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            schema_version = int(connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migration"
            ).fetchone()[0])
            if schema_version < 32:
                raise RuntimeError(f"候选静态库 schema 不是 v32：{schema_version}")
            resolved, audit = resolve_projection_rows(
                cursor_dicts(connection.execute(FORK_PERMANENT_EVIDENCE_SQL)),
                cursor_dicts(connection.execute(FORK_REFINEMENT_LEVEL_SQL)),
            )
            connection.execute("BEGIN")
            connection.execute("DELETE FROM fork_permanent_property")
            connection.executemany(
                """
                INSERT INTO fork_permanent_property(
                    fork_id, refinement_level, property_id,
                    modifier_operation, property_value,
                    source_parameter_name_id, source_effect_definition_id,
                    source_calculation_asset_path, source_row_id
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
            connection.execute(
                "UPDATE dataset SET importer_version = ?",
                (IMPORTER_VERSION,),
            )
            connection.commit()
            template_columns = tuple(
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(character_graduation_template)"
                )
            )
            prior_templates = {
                int(row[0]): tuple(row[1:])
                for row in connection.execute(
                    "SELECT character_id, "
                    + ", ".join(
                        column
                        for column in template_columns
                        if column != "character_id"
                    )
                    + " FROM character_graduation_template"
                )
            }
            graduation_template_count = populate_graduation_templates(
                connection,
                database_path=temporary,
                config_dir=config_dir.expanduser().resolve(),
            )
            comparison_columns = tuple(
                column
                for column in template_columns
                if column not in ("character_id", "generated_at_utc")
            )
            all_value_columns = tuple(
                column for column in template_columns if column != "character_id"
            )
            generated_index = all_value_columns.index("generated_at_utc")
            for current in connection.execute(
                "SELECT character_id, " + ", ".join(all_value_columns)
                + " FROM character_graduation_template"
            ).fetchall():
                character_id = int(current[0])
                previous = prior_templates.get(character_id)
                if previous is None:
                    continue
                current_values = tuple(current[1:])
                if all(
                    current_values[all_value_columns.index(column)]
                    == previous[all_value_columns.index(column)]
                    for column in comparison_columns
                ):
                    connection.execute(
                        "UPDATE character_graduation_template "
                        "SET generated_at_utc = ? WHERE character_id = ?",
                        (previous[generated_index], character_id),
                    )
            violations = [
                tuple(row) for row in connection.execute("PRAGMA foreign_key_check")
            ]
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            if violations or integrity != "ok":
                raise RuntimeError(
                    f"候选校验失败：foreign_keys={violations[:5]}, integrity={integrity}"
                )
            connection.commit()
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    statuses = Counter(item.status for item in audit)
    report = {
        "format_version": 1,
        "input_filename": source.name,
        "input_sha256": file_sha256(source).upper(),
        "output_filename": output.name,
        "output_sha256": file_sha256(output).upper(),
        "schema_version": schema_version,
        "importer_version": IMPORTER_VERSION,
        "resolved_forks": len({value.fork_id for value in resolved}),
        "resolved_rows": len(resolved),
        "graduation_template_count": graduation_template_count,
        "status_counts": dict(sorted(statuses.items())),
        "forks": [item.to_dict() for item in audit],
        "foreign_key_check": [],
        "integrity_check": "ok",
    }
    audit_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = reproject_candidate(
        args.source,
        args.output,
        args.audit,
        config_dir=args.config_dir,
    )
    print(json.dumps({
        "resolved_forks": report["resolved_forks"],
        "resolved_rows": report["resolved_rows"],
        "graduation_template_count": report["graduation_template_count"],
        "status_counts": report["status_counts"],
        "output_sha256": report["output_sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
