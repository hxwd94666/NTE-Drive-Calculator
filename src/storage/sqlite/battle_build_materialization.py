# 将采集开始时冻结的角色养成与装备物化为战报独立副本。
from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .battle_build_stage_support import normalize_frozen_advancement
from .user_data_support import UserDataError, _decoded, _json


def _optional_text(value: Any) -> str | None:
    return str(value).strip() or None if value is not None else None


def materialize_character_builds(
    connection: sqlite3.Connection,
    *,
    record_id: int,
    snapshot_id: int | None,
    profiles: Sequence[tuple[int, str, dict[str, Any]]],
    frozen_equipment: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    for character_id, observed_name, profile in profiles:
        skills = profile.get("skill_levels") or {}
        if not isinstance(skills, Mapping):
            raise UserDataError(f"角色 {character_id} 的冻结技能配置损坏")
        (
            character_level,
            breakthrough_stage,
            fork_id,
            fork_level,
            fork_breakthrough_stage,
            frozen_profile,
        ) = normalize_frozen_advancement(profile, character_id)
        connection.execute(
            """
            INSERT INTO battle_character_build_snapshot(
                battle_record_id, character_id, observed_name,
                profile_source, character_level, breakthrough_stage,
                awakening_level, fork_id, fork_level,
                fork_breakthrough_stage,
                fork_refinement_level, selected_skill_id, ordinal,
                raw_profile_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record_id,
                character_id,
                observed_name or None,
                str(profile.get("profile_source") or "unknown"),
                character_level,
                breakthrough_stage,
                int(
                    6
                    if profile.get("awakening_level") is None
                    else profile["awakening_level"]
                ),
                fork_id,
                fork_level,
                fork_breakthrough_stage,
                profile.get("fork_refinement_level"),
                _optional_text(profile.get("selected_skill_id")),
                int(profile.get("ordinal") or 0),
                _json(frozen_profile),
            ),
        )
        connection.executemany(
            """
            INSERT INTO battle_character_skill_snapshot(
                battle_record_id, character_id, skill_id, skill_level
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (record_id, character_id, str(skill_id), int(skill_level))
                for skill_id, skill_level in sorted(skills.items())
            ],
        )
    if not profiles or (snapshot_id is None and frozen_equipment is None):
        return
    character_ids = [row[0] for row in profiles]
    placeholders = ",".join("?" for _ in character_ids)
    items = connection.execute(
        f"""
        SELECT * FROM inventory_item
        WHERE snapshot_id = ? AND equipped = 1
          AND equipped_character_id IN ({placeholders})
        ORDER BY equipped_character_id, kind, uid_slot, uid_serial
        """,
        (snapshot_id, *character_ids),
    ).fetchall() if frozen_equipment is None else [
        {
            **item, "names_json": _json(item.get("names") or {}),
            "suit_names_json": _json(item.get("suit_names") or {}),
            "raw_item_json": _json(item),
        }
        for item in frozen_equipment
        if item.get("equipped") and item.get("equipped_character_id") in character_ids
    ]
    frozen_by_uid = {
        (int(item["uid_serial"]), int(item["uid_slot"])): item for item in frozen_equipment or ()
    }
    selected_uids: list[tuple[int, int]] = []
    for item in items:
        raw_item = _decoded(str(item["raw_item_json"]), {})
        placement = (
            raw_item.get("equipped_placement")
            if isinstance(raw_item, Mapping)
            else None
        )
        placement = placement if isinstance(placement, Mapping) else {}
        connection.execute(
            """
            INSERT INTO battle_equipment_snapshot(
                battle_record_id, character_id, uid_serial, uid_slot,
                kind, item_id, suit_id, geometry, grid_count, quality,
                level, max_level, locked, target_row, target_column,
                names_json, suit_names_json, raw_item_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record_id,
                int(item["equipped_character_id"]),
                int(item["uid_serial"]),
                int(item["uid_slot"]),
                item["kind"],
                item["item_id"],
                item["suit_id"],
                item["geometry"],
                int(item["grid_count"] or 0),
                item["quality"],
                item["level"],
                item["max_level"],
                int(item["locked"]),
                placement.get("row"),
                placement.get("column"),
                item["names_json"],
                item["suit_names_json"],
                item["raw_item_json"],
            ),
        )
        selected_uids.append((int(item["uid_serial"]), int(item["uid_slot"])))
    for uid_serial, uid_slot in selected_uids:
        stats = connection.execute(
            """
            SELECT stat_group, ordinal, property_id, value, is_percent,
                   names_json, raw_stat_json
            FROM inventory_item_stat
            WHERE snapshot_id = ? AND uid_serial = ? AND uid_slot = ?
            ORDER BY stat_group, ordinal
            """,
            (snapshot_id, uid_serial, uid_slot),
        ).fetchall() if frozen_equipment is None else [
            {**stat, "stat_group": group, "ordinal": ordinal,
             "is_percent": stat.get("is_percent", stat.get("percent", False)), "names_json": _json(stat.get("names") or {}),
             "raw_stat_json": _json(stat)}
            for group, key in (("main", "main_stats"), ("sub", "sub_stats"))
            for ordinal, stat in enumerate(frozen_by_uid[(uid_serial, uid_slot)].get(key) or ())
        ]
        connection.executemany(
            """
            INSERT INTO battle_equipment_stat_snapshot(
                battle_record_id, uid_serial, uid_slot, stat_group,
                ordinal, property_id, value, is_percent, names_json,
                raw_stat_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    record_id,
                    uid_serial,
                    uid_slot,
                    stat["stat_group"],
                    int(stat["ordinal"]),
                    stat["property_id"],
                    float(stat["value"]),
                    int(stat["is_percent"]),
                    stat["names_json"],
                    stat["raw_stat_json"],
                )
                for stat in stats
            ],
        )

