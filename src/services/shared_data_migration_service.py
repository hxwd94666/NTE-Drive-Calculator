# 把旧版静态库中的本机额外形状差异迁移到公共覆盖库。
"""旧版公共额外形状差异迁移。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from src.observability.context import OperationContext
from src.observability.operation import operation_scope
from src.storage.sqlite.shared_data_dao import SharedDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


MIGRATION_KEY = "legacy_static_shape_bonus_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_shape_bonus_catalog(database_path: str | Path) -> dict[str, Any]:
    """只读取旧库中历史允许用户修改的两个逻辑形状表。"""

    path = Path(database_path).expanduser().resolve()
    uri = f"{path.as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        dataset = connection.execute(
            "SELECT dataset_id FROM dataset"
        ).fetchone()
        schema = connection.execute(
            "SELECT MAX(version) AS version FROM schema_migration"
        ).fetchone()
        rows = connection.execute(
            """SELECT logical_character_key, representative_character_id,
                      shape_label, shape_grid_count
               FROM logical_character_shape_bonus
               ORDER BY logical_character_key"""
        ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            record = dict(row)
            record["properties"] = [
                dict(property_row)
                for property_row in connection.execute(
                    """SELECT property_id, display_value, ordinal
                       FROM logical_character_shape_bonus_property
                       WHERE logical_character_key = ?
                       ORDER BY ordinal""",
                    (record["logical_character_key"],),
                )
            ]
            records.append(record)
    except sqlite3.Error as exc:
        raise RuntimeError(f"旧版静态数据库无法读取：{path}") from exc
    finally:
        if "connection" in locals():
            connection.close()
    if dataset is None or schema is None:
        raise RuntimeError(f"旧版静态数据库缺少数据集信息：{path}")
    return {
        "database_path": str(path),
        "database_sha256": _sha256(path),
        "dataset_id": str(dataset["dataset_id"]),
        "schema_version": int(schema["version"]),
        "records": records,
    }


def write_shape_bonus_baseline(
    database_path: str | Path,
    output_path: str | Path,
    *,
    release_version: str,
) -> dict[str, Any]:
    """从确认未修改的发行库生成机器可读迁移基线。"""

    catalog = read_shape_bonus_catalog(database_path)
    payload = {
        "format_version": 1,
        "baselines": [
            {
                "release_version": str(release_version),
                "database_sha256": catalog["database_sha256"],
                "dataset_id": catalog["dataset_id"],
                "schema_version": catalog["schema_version"],
                "records": catalog["records"],
            }
        ],
    }
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def _record_value(record: dict[str, Any]) -> tuple[object, ...]:
    return (
        str(record.get("shape_label") or ""),
        int(record.get("shape_grid_count") or 0),
        tuple(
            (
                str(row.get("property_id") or ""),
                float(row.get("display_value") or 0.0),
                int(row.get("ordinal") or 0),
            )
            for row in record.get("properties") or ()
        ),
    )


def _matching_baseline(
    legacy: dict[str, Any],
    baseline_path: Path,
) -> dict[str, Any]:
    try:
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取旧版额外形状迁移基线：{baseline_path}") from exc
    baselines = payload.get("baselines") if isinstance(payload, dict) else None
    if not isinstance(baselines, list):
        raise RuntimeError(f"旧版额外形状迁移基线格式无效：{baseline_path}")
    exact = next(
        (
            baseline
            for baseline in baselines
            if str(baseline.get("database_sha256") or "").upper()
            == legacy["database_sha256"]
        ),
        None,
    )
    if exact is not None:
        return exact
    compatible = [
        baseline
        for baseline in baselines
        if str(baseline.get("dataset_id") or "") == legacy["dataset_id"]
        and int(baseline.get("schema_version") or 0) == legacy["schema_version"]
    ]
    if len(compatible) != 1:
        raise RuntimeError(
            "没有与旧版静态库数据集和结构版本匹配的额外形状迁移基线"
        )
    return compatible[0]


def migrate_legacy_static_shape_bonuses(
    *,
    legacy_database_path: str | Path,
    current_static_database_path: str | Path,
    shared_database_path: str | Path,
    baseline_path: str | Path,
    operation_context: OperationContext | None = None,
) -> dict[str, Any]:
    """只迁移相对旧发行默认确实发生过的额外形状差异。"""

    context = operation_context or OperationContext.create("database_migration")
    with operation_scope(
        context,
        started_event="database.shape_bonus_migration_started",
        succeeded_event="database.shape_bonus_migration_succeeded",
        failed_event="database.shape_bonus_migration_failed",
        message="迁移旧版公共额外形状覆盖",
    ) as span:
        result = _migrate_legacy_static_shape_bonuses(
            legacy_database_path=legacy_database_path,
            current_static_database_path=current_static_database_path,
            shared_database_path=shared_database_path,
            baseline_path=baseline_path,
        )
        span.annotate(
            status=result.get("status"),
            migrated_count=int(result.get("migrated_count") or 0),
            detected_difference_count=int(
                result.get("detected_difference_count") or 0
            ),
        )
        return result


def _migrate_legacy_static_shape_bonuses(
    *,
    legacy_database_path: str | Path,
    current_static_database_path: str | Path,
    shared_database_path: str | Path,
    baseline_path: str | Path,
) -> dict[str, Any]:
    legacy_path = Path(legacy_database_path).expanduser().resolve()
    if not legacy_path.is_file():
        return {"status": "no_legacy_backup", "migrated_count": 0}

    with SharedDataDao(shared_database_path) as shared_dao:
        if shared_dao.migration_completed(MIGRATION_KEY):
            return {"status": "already_completed", "migrated_count": 0}

    legacy = read_shape_bonus_catalog(legacy_path)
    baseline = _matching_baseline(
        legacy,
        Path(baseline_path).expanduser().resolve(),
    )
    baseline_records = {
        str(record["logical_character_key"]): record
        for record in baseline.get("records") or ()
    }
    changed = [
        {
            **record,
            "based_on_dataset_id": legacy["dataset_id"],
        }
        for record in legacy["records"]
        if (
            str(record["logical_character_key"]) in baseline_records
            and _record_value(record)
            != _record_value(
                baseline_records[str(record["logical_character_key"])]
            )
        )
    ]

    with StaticGameDataDao(current_static_database_path) as static_dao:
        current_keys = {
            str(row["logical_character_key"])
            for row in static_dao.list_characters()
            if row.get("logical_character_key")
        }
        known_properties = {
            str(row["attribute_id"])
            for row in static_dao.list_equipment_attributes()
        }
    unsupported_keys = sorted(
        str(record["logical_character_key"])
        for record in changed
        if str(record["logical_character_key"]) not in current_keys
    )
    unsupported_properties = sorted({
        str(property_row["property_id"])
        for record in changed
        for property_row in record.get("properties") or ()
        if str(property_row["property_id"]) not in known_properties
    })
    if unsupported_keys or unsupported_properties:
        raise RuntimeError(
            "旧版额外形状差异无法映射到新版静态库："
            f"logical_keys={unsupported_keys}，properties={unsupported_properties}"
        )

    details = {
        "legacy_database_path": str(legacy_path),
        "legacy_dataset_id": legacy["dataset_id"],
        "legacy_schema_version": legacy["schema_version"],
        "baseline_release_version": baseline.get("release_version"),
        "detected_difference_count": len(changed),
    }
    with SharedDataDao(shared_database_path) as shared_dao:
        migrated_count = shared_dao.apply_shape_bonus_migration(
            changed,
            migration_key=MIGRATION_KEY,
            source_fingerprint=legacy["database_sha256"],
            details=details,
        )
    return {
        "status": "completed",
        "migrated_count": migrated_count,
        **details,
    }
