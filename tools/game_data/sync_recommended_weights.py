# 开发期从异环工坊 API 更新发行版只读静态库中的角色推荐权重。
"""Synchronize workshop character weights into game_static.sqlite3.

The API key is read only by this developer tool. Packaged applications never
contact the workshop API and never read the legacy roles.json weight cache.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.domain.recommended_weights import parse_workshop_recommendations
from src.features.settings.workshop_weights import fetch_workshop_weight_configs
from tools import build_cli
from tools.game_data.build_graduation_templates import populate_graduation_templates
from tools.sync_workshop_weights import resolve_api_key


MINIMUM_SCHEMA_VERSION = 11


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT MAX(version) FROM schema_migration").fetchone()
    return int(row[0] or 0)


def _ensure_schema(connection: sqlite3.Connection) -> None:
    version = _schema_version(connection)
    if version >= MINIMUM_SCHEMA_VERSION:
        return
    raise RuntimeError(
        f"静态数据库结构版本为 {version}；推荐权重同步至少需要 v11"
    )


def update_static_database(
    database_path: Path,
    records: list[dict],
    *,
    api_source_kind: str = "workshop_api",
    config_dir: Path = ROOT / "config",
) -> dict[str, int]:
    """Atomically replace bundled recommendations after an API response is available."""

    if api_source_kind not in {"workshop_api", "workshop_cache"}:
        raise ValueError("api_source_kind 必须是 workshop_api 或 workshop_cache")
    database_path = Path(database_path).expanduser().resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"静态数据库不存在：{database_path}")
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{database_path.name}.", suffix=".tmp", dir=database_path.parent,
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(database_path, temporary)
        with closing(sqlite3.connect(temporary)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            _ensure_schema(connection)
            connection.commit()
            character_ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT character_id FROM equipment_plan ORDER BY character_id"
                )
            ]
            recommendations = parse_workshop_recommendations(records, character_ids)
            known_properties = {
                str(row[0])
                for row in connection.execute("SELECT attribute_id FROM equipment_attribute")
            }
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM character_weight_recommendation_property")
            connection.execute("DELETE FROM character_weight_recommendation")
            api_count = 0
            default_count = 0
            property_count = 0
            for character_id in character_ids:
                recommendation = recommendations[character_id]
                source_kind = str(recommendation["source_kind"])
                if source_kind == "workshop_api":
                    source_kind = api_source_kind
                api_count += int(source_kind == "workshop_api")
                api_count += int(source_kind == "workshop_cache")
                default_count += int(source_kind == "default")
                connection.execute(
                    """INSERT INTO character_weight_recommendation(
                           character_id, source_kind, source_item_id, source_name,
                           source_updated_at_utc
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        character_id, source_kind,
                        recommendation.get("source_item_id"),
                        recommendation.get("source_name"), now,
                    ),
                )
                rows = [
                    (
                        character_id, str(row["property_id"]), float(row["weight"]),
                        float(row["main_weight"]), int(row["ordinal"]),
                    )
                    for row in recommendation.get("properties") or ()
                    if str(row["property_id"]) in known_properties
                ]
                if not rows:
                    raise RuntimeError(f"角色 {character_id} 没有可写入的有效推荐词条")
                connection.executemany(
                    """INSERT INTO character_weight_recommendation_property(
                           character_id, property_id, weight, main_weight, ordinal
                       ) VALUES (?, ?, ?, ?, ?)""",
                    rows,
                )
                property_count += len(rows)
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"静态数据库外键检查失败：{violations[:3]}")
            connection.commit()
            graduation_count = populate_graduation_templates(
                connection,
                database_path=temporary,
                config_dir=Path(config_dir).expanduser().resolve(),
            )
        os.replace(temporary, database_path)
        return {
            "character_count": len(character_ids),
            "api_count": api_count,
            "default_count": default_count,
            "property_count": property_count,
            "graduation_count": graduation_count,
        }
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "data" / "game_static.sqlite3")
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--optional", action="store_true")
    parser.add_argument("--prompt-key", action="store_true")
    parser.add_argument("--fallback-normal", action="store_true")
    args = parser.parse_args()
    api_key, source = resolve_api_key(
        args.env_file,
        prompt_when_missing=args.prompt_key,
        allow_normal_fallback=args.fallback_normal,
    )
    if not api_key:
        if source == "normal":
            build_cli.skip("已进入普通模式：不更新静态角色权重。")
            return 0
        message = "缺少 WORKSHOP_API_KEY；开发发布前请写入 .env 或手动输入。"
        if args.optional:
            build_cli.warn(message)
            return 0
        build_cli.fail(message)
        return 2
    try:
        records = fetch_workshop_weight_configs(api_key)
        summary = update_static_database(
            args.database,
            records,
            config_dir=args.config_dir,
        )
    except Exception as exc:
        build_cli.fail(f"静态角色权重同步失败：{exc}")
        return 1
    build_cli.ok(
        "静态角色权重已更新"
        f"（来源={source}，API={summary['api_count']}，默认={summary['default_count']}，"
        f"词条={summary['property_count']}，毕业模板={summary['graduation_count']}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
