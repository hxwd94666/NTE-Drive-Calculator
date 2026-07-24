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
from src.services.graduation_bonus_service import graduation_extra_shape_stats
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
    # Clear rows that may have been created by an older release, then keep the
    # official static database as the sole source of shape metadata.
    del config_dir
    connection.execute("DELETE FROM logical_character_shape_bonus_property")
    connection.execute("DELETE FROM logical_character_shape_bonus")
    connection.execute("DELETE FROM character_shape_bonus_property")
    connection.execute("DELETE FROM character_shape_bonus")
    return 0

def _top_stat_names(
    values: Mapping[str, Any],
    weights: Mapping[str, float],
) -> tuple[str, ...]:
    rows = [
        (str(property_id), str(name), float(weights.get(property_id, 0.0)))
        for name in values
        if (property_id := _property_id(str(name))) is not None
        and float(weights.get(property_id, 0.0)) > 0
    ]
    # Keep the static template's tie-breaker identical to the role page,
    # which sorts official property IDs after weight.
    rows.sort(key=lambda row: (-row[2], row[0]))
    return tuple(name for _property_id, name, _weight in rows[:4])


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


def _stats(config_dir: Path) -> dict[str, Any]:
    """Read shared stat values; character recommendations come from SQLite."""

    return _read_json(config_dir / "stats.json")


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


def _extra_shape_drive_count(
    shape_bonus: Mapping[str, Any],
    equipment_plan: Mapping[str, Any] | None,
    equipment_by_id: Mapping[str, Mapping[str, Any]],
) -> int:
    """Count the role's bonus-shape modules from its invariant blueprint set.

    A label such as ``Type-3`` identifies the grid size, not the number of
    bonus modules.  One role can have multiple valid board layouts, but their
    module multiset is fixed, so the official plan is the canonical and stable
    source for this count.
    """

    target_grid_count = int(shape_bonus.get("shape_grid_count") or 0)
    if target_grid_count <= 0:
        numbers = re.findall(r"\d+", str(shape_bonus.get("shape_label") or ""))
        target_grid_count = int(numbers[-1]) if numbers else 0
    if target_grid_count <= 0 or not equipment_plan:
        return 0
    return sum(
        int((equipment_by_id.get(str(item_id)) or {}).get("grid_count") or 0)
        == target_grid_count
        for item_id in equipment_plan.get("module_item_ids") or ()
    )


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
    stats = _stats(config_dir)
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
                equipment_plans = {
                    int(character["character_id"]): static_dao.get_equipment_plan(
                        int(character["character_id"])
                    )
                    for character in characters
                }
                equipment_by_id = {
                    str(item["item_id"]): item
                    for item in static_dao.list_equipment_items()
                }
                recommendations = {
                    int(row["character_id"]): row
                    for row in static_dao.list_character_recommended_weights()
                }
            for character in characters:
                character_id = int(character["character_id"])
                detail = load_official_role_detail(user_database, character_id)
                recommendation = recommendations.get(character_id) or {}
                # The API synchronization above writes these rows into the
                # same temporary static database before templates are built.
                # Runtime account preferences deliberately do not participate.
                source_kind = "official_default"
                weights = dict(recommendation.get("property_weights") or {})
                main_weights = dict(
                    recommendation.get("main_property_weights") or weights
                )
                extra_shape_count = _extra_shape_drive_count(
                    detail.get("shape_bonus") or {},
                    equipment_plans.get(character_id),
                    equipment_by_id,
                )
                drive_names = _top_stat_names(gold_values, weights)
                core_sub_names = _top_stat_names(core_sub_values, weights)
                if len(drive_names) < 4 or len(core_sub_names) < 4:
                    fallback = dict(recommendation.get("property_weights") or {})
                    drive_names = _top_stat_names(gold_values, fallback)
                    core_sub_names = _top_stat_names(core_sub_values, fallback)
                if len(drive_names) < 4 or len(core_sub_names) < 4:
                    raise ValueError(f"角色 {character_id} 缺少四条毕业副词条")

                extra_stats = graduation_extra_shape_stats(
                    detail.get("shape_bonus"),
                    extra_shape_count,
                    detail.get("attributes"),
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
                suit_id = official_suits.get(character_id)
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
