# 将旧版毕业方案在构建期固化为静态 SQLite 角色模板。
"""Build fixed per-character graduation templates inside game_static.sqlite3."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.domain.recommended_weights import WORKSHOP_STAT_PROPERTY_IDS
from src.services.official_role_page_service import (
    calculate_official_role_margins,
    load_official_role_detail,
)
from src.storage.sqlite.static_game_data_dao import (
    STATIC_DATABASE_ENV,
    StaticGameDataDao,
)
from src.storage.sqlite.user_data_dao import UserDataDao


SCHEMA_PATH = (
    PROJECT_ROOT
    / "src"
    / "storage"
    / "sqlite"
    / "schema"
    / "012_game_static_graduation_template.sql"
)
DRIVE_AREA = 20


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _property_id(name: str) -> str | None:
    return WORKSHOP_STAT_PROPERTY_IDS.get(str(name).strip())


def _property_weights(raw: Mapping[str, Any]) -> dict[str, float]:
    return {
        property_id: float(value)
        for name, value in raw.items()
        if (property_id := _property_id(str(name))) is not None
        and float(value) > 0
    }


def populate_logical_character_shape_bonuses(
    connection: sqlite3.Connection,
    *,
    config_dir: Path,
) -> int:
    """Freeze one shape rule per logical character into the readonly database."""

    # The legacy role profile has been retired.  Account-owned shape settings
    # must not be reintroduced into the immutable database during a rebuild.
    if not (config_dir / "roles.json").is_file():
        return 0

    roles = _read_json(config_dir / "roles.json")
    character_rows = connection.execute(
        """
        SELECT c.character_id, c.name_zh, a.logical_character_key,
               a.canonical_character_id, a.classification
        FROM character AS c
        JOIN character_annotation AS a USING (character_id)
        """
    ).fetchall()
    character_by_id = {int(row[0]): row for row in character_rows}
    character_rows_by_name: dict[str, list[sqlite3.Row | tuple[Any, ...]]] = {}
    character_rows_by_logical_key: dict[str, list[sqlite3.Row | tuple[Any, ...]]] = {}
    for row in character_rows:
        if row[1] is not None:
            character_rows_by_name.setdefault(str(row[1]), []).append(row)
        character_rows_by_logical_key.setdefault(str(row[2]), []).append(row)
    known_property_ids = {
        str(row[0])
        for row in connection.execute(
            "SELECT attribute_id FROM equipment_attribute"
        ).fetchall()
    }
    connection.execute("DELETE FROM logical_character_shape_bonus_property")
    connection.execute("DELETE FROM logical_character_shape_bonus")
    # v14 stored one duplicate row per concrete avatar ID. v15 supersedes it.
    connection.execute("DELETE FROM character_shape_bonus_property")
    connection.execute("DELETE FROM character_shape_bonus")
    rules: dict[str, tuple[str, int, tuple[tuple[str, float], ...]]] = {}
    for role_name, raw in roles.items():
        if not isinstance(raw, Mapping):
            continue
        shape_label = str(raw.get("extra_shape_label") or "").strip()
        numbers = re.findall(r"\d+", shape_label)
        if not shape_label or not numbers:
            continue
        character_ids = {
            int(value)
            for value in (
                [raw.get("workshop_item_id")]
                + list(raw.get("workshop_item_ids") or ())
            )
            if value is not None
            and str(value).isdigit()
            and int(value) in character_by_id
        }
        if not character_ids:
            character_ids.update(
                int(row[0])
                for row in character_rows_by_name.get(str(role_name), ())
                if str(row[4]) != "combat_transformation"
            )
        logical_keys = {
            str(character_by_id[character_id][2])
            for character_id in character_ids
        }
        if not logical_keys:
            continue
        if len(logical_keys) != 1:
            raise RuntimeError(
                f"角色 [{role_name}] 同时映射到多个逻辑角色：{sorted(logical_keys)}"
            )
        logical_key = next(iter(logical_keys))
        properties = tuple(
            (property_id, float(value))
            for name, value in (raw.get("extra_shape_buffs") or {}).items()
            if (
                property_id := _property_id(str(name))
            ) is not None
            and property_id in known_property_ids
            and float(value)
        )
        rule = (shape_label, int(numbers[-1]), properties)
        existing = rules.get(logical_key)
        if existing is not None and existing != rule:
            raise RuntimeError(
                f"逻辑角色 {logical_key!r} 存在互相冲突的形状规则"
            )
        rules[logical_key] = rule

    for logical_key, (shape_label, grid_count, properties) in sorted(rules.items()):
        representatives = character_rows_by_logical_key[logical_key]
        representative_character_id = min(
            int(row[0])
            for row in representatives
            if str(row[4]) != "combat_transformation"
        )
        connection.execute(
            """
            INSERT INTO logical_character_shape_bonus(
                logical_character_key, representative_character_id,
                shape_label, shape_grid_count, source_kind
            ) VALUES (?, ?, ?, ?, 'legacy_role_profile')
            """,
            (
                logical_key,
                representative_character_id,
                shape_label,
                grid_count,
            ),
        )
        connection.executemany(
            """
            INSERT INTO logical_character_shape_bonus_property(
                logical_character_key, property_id, display_value, ordinal
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (logical_key, property_id, value, ordinal)
                for ordinal, (property_id, value) in enumerate(properties)
            ],
        )
    return len(rules)


def _top_stat_names(
    values: Mapping[str, Any],
    weights: Mapping[str, float],
) -> tuple[str, ...]:
    rows = [
        (str(name), float(weights.get(property_id, 0.0)))
        for name in values
        if (property_id := _property_id(str(name))) is not None
        and float(weights.get(property_id, 0.0)) > 0
    ]
    rows.sort(key=lambda row: (-row[1], row[0]))
    return tuple(name for name, _weight in rows[:4])


def _stat(
    name: str,
    value: float,
    *,
    percent_property_ids: set[str],
) -> dict[str, Any]:
    property_id = _property_id(name)
    if property_id is None:
        raise ValueError(f"毕业模板词条缺少官方属性映射：{name}")
    percent = property_id in percent_property_ids
    return {
        "property_id": property_id,
        "value": float(value) / 100.0 if percent else float(value),
        "percent": percent,
    }


def _role_configs(config_dir: Path) -> tuple[dict[int, tuple[str, dict]], dict]:
    roles_path = config_dir / "roles.json"
    roles = _read_json(roles_path) if roles_path.is_file() else {}
    by_character: dict[int, tuple[str, dict]] = {}
    for role_name, role in roles.items():
        values = role.get("workshop_item_ids") or [role.get("workshop_item_id")]
        for value in values:
            if value not in (None, ""):
                by_character[int(value)] = (str(role_name), dict(role))
    return by_character, _read_json(config_dir / "stats.json")


def _template_profile(detail: Mapping[str, Any]) -> dict[str, Any]:
    profile = dict(detail.get("profile") or {})
    growth = max(
        detail.get("growth_rows") or (),
        key=lambda row: (
            int(row.get("level") or 0),
            int(row.get("breakthrough_stage") or 0),
        ),
    )
    max_stage = int(growth.get("breakthrough_stage") or 0)
    skill_levels = {}
    for skill in detail.get("skills") or ():
        levels = [
            int(row.get("level") or 0)
            for row in skill.get("levels") or ()
            if int(row.get("required_breakthrough_stage") or 0) <= max_stage
            and int(row.get("required_awaken_level") or 0) <= 6
        ]
        if levels:
            skill_levels[str(skill.get("skill_id"))] = max(levels)
    profile.update({
        "character_level": int(growth["level"]),
        "breakthrough_stage": max_stage,
        "awakening_level": 6,
        "skill_levels": skill_levels,
    })
    if profile.get("fork_id"):
        profile["fork_refinement_level"] = 1
    return profile


def populate_graduation_templates(
    connection: sqlite3.Connection,
    *,
    database_path: Path,
    config_dir: Path,
) -> int:
    """Replace all templates using the old full-board selection rules."""

    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("DELETE FROM character_graduation_template")
    role_configs, stats = _role_configs(config_dir)
    gold_values = dict(stats.get("gold_base_values") or {})
    core_sub_values = dict(stats.get("tape_stat_values") or {})
    # The weight editor and the graduation reference must share the strict
    # equipment sub-stat set.  Main-only core attributes (element/healing) are
    # deliberately excluded even when they have a high role weight.
    sub_stat_names = tuple(
        name for name in core_sub_values
        if name in gold_values
    )
    gold_values = {name: gold_values[name] for name in sub_stat_names}
    core_sub_values = {name: core_sub_values[name] for name in sub_stat_names}
    core_main_values = dict(stats.get("tape_main_stat_values") or {})
    previous_static_path = os.environ.get(STATIC_DATABASE_ENV)
    os.environ[STATIC_DATABASE_ENV] = str(database_path.resolve())
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    inserted = 0
    try:
        with tempfile.TemporaryDirectory() as temporary_dir:
            user_database = Path(temporary_dir) / "graduation.sqlite3"
            with UserDataDao(user_database, account_id="graduation-builder"):
                pass
            with StaticGameDataDao(database_path) as static_dao:
                suits_by_name = {
                    str(row.get("name_zh") or ""): str(row["suit_id"])
                    for row in static_dao.list_suits()
                }
                percent_property_ids = {
                    str(row["attribute_id"])
                    for row in static_dao.list_equipment_attributes()
                    if bool(row.get("show_percent"))
                }
                characters = static_dao.list_role_template_characters()
                official_suits = {
                    int(character["character_id"]): (
                        static_dao.get_character_default_suit(
                            int(character["character_id"])
                        ) or {}
                    ).get("suit_id")
                    for character in characters
                }
            for character in characters:
                character_id = int(character["character_id"])
                detail = load_official_role_detail(user_database, character_id)
                configured = role_configs.get(character_id)
                if configured is None:
                    role_name = str(character.get("name_zh") or character_id)
                    role_config: dict[str, Any] = {}
                    source_kind = "official_default"
                    weights = dict(detail.get("property_weights") or {})
                    main_weights = dict(detail.get("main_property_weights") or weights)
                    shape_numbers = re.findall(
                        r"\d+", str((detail.get("shape_bonus") or {}).get("shape_label") or "")
                    )
                    extra_shape_count = int(shape_numbers[-1]) if shape_numbers else 0
                else:
                    role_name, role_config = configured
                    source_kind = "legacy_role_config"
                    weights = _property_weights(role_config.get("weights") or {})
                    main_weights = _property_weights(
                        role_config.get("main_weights") or role_config.get("weights") or {}
                    )
                    shape_numbers = re.findall(
                        r"\d+", str(role_config.get("extra_shape_label") or "")
                    )
                    extra_shape_count = int(shape_numbers[-1]) if shape_numbers else 0
                drive_names = _top_stat_names(gold_values, weights)
                core_sub_names = _top_stat_names(core_sub_values, weights)
                if len(drive_names) < 4 or len(core_sub_names) < 4:
                    fallback = dict(detail.get("property_weights") or {})
                    drive_names = _top_stat_names(gold_values, fallback)
                    core_sub_names = _top_stat_names(core_sub_values, fallback)
                if len(drive_names) < 4 or len(core_sub_names) < 4:
                    raise ValueError(f"角色 {character_id} 缺少四条毕业副词条")

                extra_stats = (
                    [
                        {
                            "property_id": str(row["property_id"]),
                            "value": float(row["display_value"]) * extra_shape_count,
                            "percent": str(row["property_id"]) in percent_property_ids,
                        }
                        for row in (detail.get("shape_bonus") or {}).get("properties") or ()
                    ]
                    if configured is None
                    else [
                        _stat(
                            name,
                            float(value) * extra_shape_count,
                            percent_property_ids=percent_property_ids,
                        )
                        for name, value in (role_config.get("extra_shape_buffs") or {}).items()
                    ]
                )
                module = {
                    "kind": "module",
                    "item_id": "graduation-module",
                    "quality": "orange",
                    "grid_count": DRIVE_AREA,
                    "geometry": None,
                    "main_stats": [],
                    "sub_stats": [
                        _stat(
                            name,
                            float(gold_values[name]) * DRIVE_AREA,
                            percent_property_ids=percent_property_ids,
                        )
                        for name in drive_names
                    ] + extra_stats,
                }
                core_sub_stats = [
                    _stat(
                        name,
                        float(core_sub_values[name]),
                        percent_property_ids=percent_property_ids,
                    )
                    for name in core_sub_names
                ]
                candidate_names = [
                    name
                    for name in core_main_values
                    if (
                        (property_id := _property_id(name)) is not None
                        and float(main_weights.get(property_id, 0.0)) > 0
                    )
                ]
                if not candidate_names:
                    candidate_names = [
                        name for name in core_main_values
                        if _property_id(name) in set(detail.get("main_property_weights") or {})
                    ]
                if not candidate_names:
                    raise ValueError(f"角色 {character_id} 缺少毕业空幕主词条")

                profile = _template_profile(detail)
                configured_suit_id = suits_by_name.get(
                    str(role_config.get("default_set") or "")
                )
                suit_id = configured_suit_id or official_suits.get(character_id)
                best_name = candidate_names[0]
                best_damage = -1.0
                best_equipment: list[dict[str, Any]] = []
                for main_name in candidate_names:
                    core = {
                        "kind": "core",
                        "item_id": "graduation-core",
                        "quality": "orange",
                        "suit_id": suit_id,
                        "main_stats": [
                            _stat(
                                main_name,
                                float(core_main_values[main_name]),
                                percent_property_ids=percent_property_ids,
                            )
                        ],
                        "sub_stats": core_sub_stats,
                    }
                    equipment = [module, core]
                    candidate_detail = {
                        **detail,
                        "profile": profile,
                        "property_weights": weights,
                        "equipment_contexts": {
                            **detail["equipment_contexts"],
                            "graduation": {
                                "title": "毕业基准",
                                "available": True,
                                "items": equipment,
                            },
                        },
                    }
                    margins = calculate_official_role_margins(
                        candidate_detail, "graduation"
                    )
                    damage = float((margins or {}).get("damage") or 0.0)
                    if damage > best_damage:
                        best_name = main_name
                        best_damage = damage
                        best_equipment = equipment
                if best_damage <= 0:
                    raise ValueError(f"角色 {character_id} 的毕业基准伤害无效")
                connection.execute(
                    """
                    INSERT INTO character_graduation_template(
                        character_id, source_kind, fork_id, fork_level,
                        fork_refinement_level, core_suit_id,
                        core_main_property_id, drive_area, extra_shape_count,
                        benchmark_damage, profile_json, equipment_json,
                        generated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        character_id, source_kind, profile.get("fork_id"),
                        profile.get("fork_level"),
                        profile.get("fork_refinement_level"), suit_id,
                        _property_id(best_name), DRIVE_AREA, extra_shape_count,
                        best_damage, _json(profile), _json(best_equipment),
                        generated_at,
                    ),
                )
                inserted += 1
        connection.commit()
        return inserted
    except BaseException:
        connection.rollback()
        raise
    finally:
        if previous_static_path is None:
            os.environ.pop(STATIC_DATABASE_ENV, None)
        else:
            os.environ[STATIC_DATABASE_ENV] = previous_static_path


def upgrade_and_populate(database_path: Path, config_dir: Path) -> int:
    database_path = database_path.expanduser().resolve()
    connection = sqlite3.connect(database_path)
    try:
        current = int(
            connection.execute(
                "SELECT MAX(version) FROM schema_migration"
            ).fetchone()[0]
        )
        if current == 11:
            connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO schema_migration(version, applied_at_utc) VALUES (12, ?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
            )
        elif current != 12:
            raise ValueError(f"静态数据库 schema 必须是 11 或 12，实际为 {current}")
        connection.execute("UPDATE dataset SET importer_version = 12")
        connection.commit()
        return populate_graduation_templates(
            connection,
            database_path=database_path,
            config_dir=config_dir.expanduser().resolve(),
        )
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    count = upgrade_and_populate(args.database, args.config_dir)
    print(f"已写入 {count} 个角色毕业模板：{args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
