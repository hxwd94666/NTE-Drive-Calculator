# 规范化正式人物等级、突破与经验材料来源。
"""Normalize formal character level, breakthrough and EXP material sources."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, Protocol

from tools.game_data.static_database_build_support import (
    StaticDatabaseError,
    optional_text,
)


class CharacterProgressionImportContext(Protocol):
    connection: sqlite3.Connection
    rows: dict[str, dict[str, Any]]

    def source_row_id(self, table: str, row_key: str) -> int: ...


def _pack_row_identity(row_key: str, *, context: str) -> tuple[str, int]:
    pack_id, separator, raw_level = str(row_key).rpartition("_")
    if not separator or not pack_id or not raw_level.isdigit():
        raise StaticDatabaseError(f"{context}行键无效：{row_key}")
    return pack_id, int(raw_level)


def import_character_progression(
    context: CharacterProgressionImportContext,
    *,
    canonical_item_id: Callable[[str, str], str],
    parse_cost_string: Callable[[Any], list[tuple[str, int]]],
    exp_material_spec: Callable[
        [str, Any], tuple[int, list[tuple[str, int]]] | None
    ],
) -> None:
    upgrade_rows = context.rows.get("character_upgrades")
    if not isinstance(upgrade_rows, dict):
        raise StaticDatabaseError("静态来源缺少人物升级表：character_upgrades")
    upgrade_packs: dict[str, str] = {}
    for row_key, row in sorted(upgrade_rows.items()):
        if not isinstance(row, dict):
            raise StaticDatabaseError(f"人物升级记录结构无效：{row_key}")
        pack_id, level = _pack_row_identity(str(row_key), context="人物升级")
        need_exp = row.get("NeedExp")
        if (
            not 1 <= level <= 80
            or isinstance(need_exp, bool)
            or not isinstance(need_exp, int)
            or need_exp <= 0
        ):
            raise StaticDatabaseError(f"人物升级经验无效：{row_key}")
        context.connection.execute(
            "INSERT INTO character_upgrade_level VALUES (?,?,?,?)",
            (
                pack_id,
                level,
                need_exp,
                context.source_row_id("character_upgrades", str(row_key)),
            ),
        )
        normalized_pack = pack_id.casefold()
        previous_pack = upgrade_packs.setdefault(normalized_pack, pack_id)
        if previous_pack != pack_id:
            raise StaticDatabaseError(
                f"人物升级包存在大小写冲突：{previous_pack}/{pack_id}"
            )

    breakthrough_packs: dict[str, list[tuple[int, int]]] = {}
    for row_key, row in sorted(context.rows["character_breakthroughs"].items()):
        if not isinstance(row, dict):
            raise StaticDatabaseError(f"人物突破记录结构无效：{row_key}")
        pack_id, stage = _pack_row_identity(str(row_key), context="人物突破")
        max_level = row.get("MaxCharacterLevel")
        world_level = row.get("NeedMaxWorldLevel")
        modify_pack_id = optional_text(row.get("ModifyPackID"))
        if (
            not 0 <= stage <= 6
            or isinstance(max_level, bool)
            or not isinstance(max_level, int)
            or not 1 <= max_level <= 80
            or isinstance(world_level, bool)
            or not isinstance(world_level, int)
            or world_level < 0
            or modify_pack_id is None
        ):
            raise StaticDatabaseError(f"人物突破阶段无效：{row_key}")
        context.connection.execute(
            "INSERT INTO character_breakthrough_stage VALUES (?,?,?,?,?,?)",
            (
                pack_id,
                stage,
                max_level,
                world_level,
                modify_pack_id,
                context.source_row_id("character_breakthroughs", str(row_key)),
            ),
        )
        costs = [
            *parse_cost_string(row.get("NeedItems")),
            *parse_cost_string(row.get("NeedGolds")),
        ]
        seen_items: set[str] = set()
        for ordinal, (raw_item_id, quantity) in enumerate(costs):
            item_id = canonical_item_id(raw_item_id, "progression_cost")
            if item_id in seen_items:
                raise StaticDatabaseError(f"人物突破材料重复：{row_key}/{item_id}")
            seen_items.add(item_id)
            context.connection.execute(
                "INSERT INTO character_breakthrough_cost VALUES (?,?,?,?,?)",
                (pack_id, stage, ordinal, item_id, quantity),
            )
        breakthrough_packs.setdefault(pack_id, []).append((stage, max_level))
    for pack_id, stages in breakthrough_packs.items():
        ordered = sorted(stages)
        if [stage for stage, _level in ordered] != list(range(7)):
            raise StaticDatabaseError(f"人物突破阶段不完整：{pack_id}")
        if [level for _stage, level in ordered] != [20, 30, 40, 50, 60, 70, 80]:
            raise StaticDatabaseError(f"人物突破等级上限无效：{pack_id}")
    breakthrough_pack_keys: dict[str, str] = {}
    for pack_id in breakthrough_packs:
        normalized_pack = pack_id.casefold()
        previous_pack = breakthrough_pack_keys.setdefault(normalized_pack, pack_id)
        if previous_pack != pack_id:
            raise StaticDatabaseError(
                f"人物突破包存在大小写冲突：{previous_pack}/{pack_id}"
            )

    for item_id, row in sorted(context.rows["item_catalog"].items()):
        specification = exp_material_spec(str(item_id), row)
        if specification is None:
            continue
        experience, costs = specification
        context.connection.execute(
            "INSERT INTO character_exp_material VALUES (?,?,?)",
            (
                str(item_id),
                experience,
                context.source_row_id("item_catalog", str(item_id)),
            ),
        )
        for raw_cost_item_id, quantity in costs:
            context.connection.execute(
                "INSERT INTO character_exp_material_cost VALUES (?,?,?)",
                (
                    str(item_id),
                    canonical_item_id(raw_cost_item_id, "progression_cost"),
                    quantity,
                ),
            )

    official_characters = {
        int(row[0]): str(row[1])
        for row in context.connection.execute(
            """SELECT character.character_id, annotation.classification
               FROM character
               JOIN character_annotation AS annotation USING (character_id)"""
        )
    }
    for character_id, classification in sorted(official_characters.items()):
        if classification == "combat_transformation":
            continue
        row = context.rows["character"].get(str(character_id))
        element = row.get("ElementData") if isinstance(row, dict) else None
        if not isinstance(element, dict):
            continue
        upgrade_pack_id = optional_text(element.get("UpgradePackId"))
        breakthrough_pack_id = optional_text(element.get("BreakthroughPackId"))
        if not upgrade_pack_id or not breakthrough_pack_id:
            continue
        canonical_upgrade_pack = upgrade_packs.get(upgrade_pack_id.casefold())
        canonical_breakthrough_pack = breakthrough_pack_keys.get(
            breakthrough_pack_id.casefold()
        )
        if canonical_upgrade_pack is None:
            raise StaticDatabaseError(
                f"角色引用未知人物升级包：{character_id}/{upgrade_pack_id}"
            )
        if canonical_breakthrough_pack is None:
            raise StaticDatabaseError(
                f"角色引用未知人物突破包：{character_id}/{breakthrough_pack_id}"
            )
        context.connection.execute(
            "INSERT INTO character_progression_profile VALUES (?,?,?,?)",
            (
                character_id,
                canonical_upgrade_pack,
                canonical_breakthrough_pack,
                context.source_row_id("character", str(character_id)),
            ),
        )


__all__ = ["import_character_progression"]
