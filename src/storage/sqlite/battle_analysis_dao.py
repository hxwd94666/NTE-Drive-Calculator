# 只读投影战报长页所需的逐击证据、配装与角色属性快照。
"""Account-owned read model for finalized battle analysis."""

from __future__ import annotations

from typing import Any

from .protocols import UserDataDaoMixinHost
from .user_data_support import (
    UserDataValidationError,
    _decoded,
    _finite_number,
    _integer,
    _json,
    _utc_now,
)


_TARGET_RESISTANCE_COLUMNS = {
    "normal": "resistance_normal",
    "chaos": "resistance_chaos",
    "cosmos": "resistance_cosmos",
    "incantation": "resistance_incantation",
    "lakshana": "resistance_lakshana",
    "nature": "resistance_nature",
    "psyche": "resistance_psyche",
    "psychically": "resistance_psychically",
}


class BattleAnalysisDaoMixin(UserDataDaoMixinHost):
    def load_battle_target_condition(
        self,
        battle_record_id: int,
    ) -> dict[str, Any] | None:
        """Load the user-confirmed single-target combat condition."""

        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        row = self._one(
            "SELECT * FROM battle_target_condition WHERE battle_record_id = ?",
            (record_id,),
        )
        if row is None:
            return None
        row["resistances"] = {
            damage_type: float(row.pop(column))
            for damage_type, column in _TARGET_RESISTANCE_COLUMNS.items()
        }
        row["selected_target_ids"] = _decoded(
            row.pop("selected_target_ids_json"),
            [],
        )
        row["feast_options"] = _decoded(
            row.pop("feast_options_json"),
            {},
        )
        row["witch_buff_is_percent"] = bool(row["witch_buff_is_percent"])
        row["confirmed"] = True
        return row

    def save_battle_target_condition(
        self,
        battle_record_id: int,
        condition: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically replace one record's user-confirmed target condition."""

        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        target_name = str(condition.get("target_name") or "").strip()
        if not target_name:
            raise UserDataValidationError("目标名称不能为空")
        scene = str(condition.get("scene") or "").strip()
        if scene not in {"outer_realm", "open_world"}:
            raise UserDataValidationError("敌方场景必须是轨外之境或大世界")
        environment_kind = str(
            condition.get("environment_kind") or "manual"
        ).strip()
        if environment_kind not in {
            "manual", "open_world", "outer_realm", "feast",
        }:
            raise UserDataValidationError("战斗环境类型无效")
        environment_ref = str(condition.get("environment_ref") or "").strip()
        raw_target_ids = condition.get("selected_target_ids") or ()
        if not isinstance(raw_target_ids, (list, tuple)):
            raise UserDataValidationError("已选目标必须是数组")
        selected_target_ids = tuple(dict.fromkeys(
            str(value).strip() for value in raw_target_ids if str(value).strip()
        ))
        primary_target_id = str(
            condition.get("primary_target_id") or ""
        ).strip()
        if primary_target_id and primary_target_id not in selected_target_ids:
            raise UserDataValidationError("当前计算对象必须属于已选目标")
        if environment_kind != "manual" and not selected_target_ids:
            raise UserDataValidationError("请至少选择一个战斗对象")
        if selected_target_ids and not primary_target_id:
            raise UserDataValidationError("请选择一个当前计算对象")
        raw_difficulty = condition.get("difficulty_id")
        difficulty_id = (
            None
            if raw_difficulty in (None, "")
            else _integer(raw_difficulty, "争锋难度", minimum=1)
        )
        if difficulty_id is not None and difficulty_id > 4:
            raise UserDataValidationError("争锋难度必须在 1 到 4 之间")
        raw_feast_options = condition.get("feast_options") or {}
        if not isinstance(raw_feast_options, dict):
            raise UserDataValidationError("争锋加成必须是对象")
        feast_options = {
            str(key): str(value)
            for key, value in raw_feast_options.items()
            if str(key).strip() and str(value).strip()
        }
        witch_buff_id = str(condition.get("witch_buff_id") or "").strip()
        witch_buff_name = str(
            condition.get("witch_buff_name_zh") or ""
        ).strip()
        witch_property = str(
            condition.get("witch_buff_property_id") or ""
        ).strip()
        raw_witch_value = condition.get("witch_buff_value")
        witch_value = (
            None
            if raw_witch_value in (None, "")
            else _finite_number(raw_witch_value, "魔女赐福数值")
        )
        witch_is_percent = int(bool(condition.get("witch_buff_is_percent")))
        if bool(witch_buff_id) != bool(witch_property and witch_value is not None):
            raise UserDataValidationError("魔女赐福 ID、属性与数值必须同时存在")
        if witch_buff_id and not witch_buff_name:
            raise UserDataValidationError("魔女赐福名称不能为空")
        resistances = condition.get("resistances")
        if not isinstance(resistances, dict):
            raise UserDataValidationError("敌方分属性抗性必须是对象")
        values = {
            damage_type: _finite_number(
                resistances.get(damage_type),
                f"{damage_type} 最终抗性",
                minimum=-5.0,
                maximum=5.0,
            )
            for damage_type in _TARGET_RESISTANCE_COLUMNS
        }
        enemy_level = _finite_number(
            condition.get("enemy_level"),
            "敌方等级",
            minimum=1.0,
            maximum=999.0,
        )
        raw_defense_base = condition.get("enemy_defense_base")
        enemy_defense_base = (
            None
            if raw_defense_base in (None, "", 0, 0.0)
            else _finite_number(
                raw_defense_base,
                "敌方 DefBase",
                minimum=0.0,
            )
        )
        enemy_defense_up = _finite_number(
            condition.get("enemy_defense_up", 0.0),
            "敌方 DefUp",
            minimum=-1.0,
            maximum=10.0,
        )
        enemy_defense_add = _finite_number(
            condition.get("enemy_defense_add", 0.0),
            "敌方 DefAdd",
            minimum=-1_000_000_000.0,
            maximum=1_000_000_000.0,
        )
        enemy_topple_limit = _finite_number(
            condition.get("enemy_topple_limit", 50.0),
            "敌方 UnbalMax",
            minimum=0.0,
            maximum=1_000_000.0,
        )
        defense_reduction = _finite_number(
            condition.get("defense_reduction"),
            "敌方防御降低",
            minimum=-1.0,
            maximum=1.0,
        )
        vulnerability = _finite_number(
            condition.get("vulnerability"),
            "敌方易伤",
            minimum=-1.0,
            maximum=10.0,
        )
        updated_at = _utc_now()
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM battle_record WHERE battle_record_id = ?",
                (record_id,),
            ).fetchone() is None:
                raise UserDataValidationError("战报记录不存在")
            connection.execute(
                """
                INSERT INTO battle_target_condition (
                    battle_record_id,
                    source_kind,
                    target_name,
                    enemy_level,
                    scene,
                    enemy_defense_base,
                    enemy_defense_up,
                    enemy_defense_add,
                    enemy_topple_limit,
                    defense_reduction,
                    vulnerability,
                    resistance_normal,
                    resistance_chaos,
                    resistance_cosmos,
                    resistance_incantation,
                    resistance_lakshana,
                    resistance_nature,
                    resistance_psyche,
                    resistance_psychically,
                    environment_kind,
                    environment_ref,
                    selected_target_ids_json,
                    primary_target_id,
                    difficulty_id,
                    feast_options_json,
                    witch_buff_id,
                    witch_buff_name_zh,
                    witch_buff_property_id,
                    witch_buff_value,
                    witch_buff_is_percent,
                    updated_at_utc
                ) VALUES (
                    ?, 'user_confirmed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(battle_record_id) DO UPDATE SET
                    source_kind = excluded.source_kind,
                    target_name = excluded.target_name,
                    enemy_level = excluded.enemy_level,
                    scene = excluded.scene,
                    enemy_defense_base = excluded.enemy_defense_base,
                    enemy_defense_up = excluded.enemy_defense_up,
                    enemy_defense_add = excluded.enemy_defense_add,
                    enemy_topple_limit = excluded.enemy_topple_limit,
                    defense_reduction = excluded.defense_reduction,
                    vulnerability = excluded.vulnerability,
                    resistance_normal = excluded.resistance_normal,
                    resistance_chaos = excluded.resistance_chaos,
                    resistance_cosmos = excluded.resistance_cosmos,
                    resistance_incantation = excluded.resistance_incantation,
                    resistance_lakshana = excluded.resistance_lakshana,
                    resistance_nature = excluded.resistance_nature,
                    resistance_psyche = excluded.resistance_psyche,
                    resistance_psychically = excluded.resistance_psychically,
                    environment_kind = excluded.environment_kind,
                    environment_ref = excluded.environment_ref,
                    selected_target_ids_json = excluded.selected_target_ids_json,
                    primary_target_id = excluded.primary_target_id,
                    difficulty_id = excluded.difficulty_id,
                    feast_options_json = excluded.feast_options_json,
                    witch_buff_id = excluded.witch_buff_id,
                    witch_buff_name_zh = excluded.witch_buff_name_zh,
                    witch_buff_property_id = excluded.witch_buff_property_id,
                    witch_buff_value = excluded.witch_buff_value,
                    witch_buff_is_percent = excluded.witch_buff_is_percent,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    record_id,
                    target_name,
                    enemy_level,
                    scene,
                    enemy_defense_base,
                    enemy_defense_up,
                    enemy_defense_add,
                    enemy_topple_limit,
                    defense_reduction,
                    vulnerability,
                    *(values[key] for key in _TARGET_RESISTANCE_COLUMNS),
                    environment_kind,
                    environment_ref or None,
                    _json(list(selected_target_ids)),
                    primary_target_id or None,
                    difficulty_id,
                    _json(feast_options),
                    witch_buff_id or None,
                    witch_buff_name or None,
                    witch_property or None,
                    witch_value,
                    witch_is_percent,
                    updated_at,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        saved = self.load_battle_target_condition(record_id)
        if saved is None:
            raise RuntimeError("敌方条件保存后丢失")
        return saved

    def load_battle_build_snapshot(
        self,
        battle_record_id: int,
    ) -> dict[str, Any] | None:
        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        header = self._one(
            "SELECT * FROM battle_build_snapshot WHERE battle_record_id = ?",
            (record_id,),
        )
        if header is None:
            return None
        characters = self._rows(
            """
            SELECT * FROM battle_character_build_snapshot
            WHERE battle_record_id = ? ORDER BY ordinal, character_id
            """,
            (record_id,),
        )
        equipment = self._rows(
            """
            SELECT * FROM battle_equipment_snapshot
            WHERE battle_record_id = ?
            ORDER BY character_id, kind, uid_slot, uid_serial
            """,
            (record_id,),
        )
        stats = self._rows(
            """
            SELECT * FROM battle_equipment_stat_snapshot
            WHERE battle_record_id = ?
            ORDER BY uid_slot, uid_serial, stat_group, ordinal
            """,
            (record_id,),
        )
        stats_by_uid: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for stat in stats:
            key = (int(stat["uid_serial"]), int(stat["uid_slot"]))
            stat["is_percent"] = bool(stat["is_percent"])
            stat["names"] = _decoded(stat.pop("names_json"), {})
            stat.pop("raw_stat_json", None)
            stats_by_uid.setdefault(key, []).append(stat)
        equipment_by_character: dict[int, list[dict[str, Any]]] = {}
        for item in equipment:
            key = (int(item["uid_serial"]), int(item["uid_slot"]))
            item["locked"] = bool(item["locked"])
            item["names"] = _decoded(item.pop("names_json"), {})
            item["suit_names"] = _decoded(item.pop("suit_names_json"), {})
            item["stats"] = stats_by_uid.get(key, [])
            equipment_by_character.setdefault(
                int(item["character_id"]),
                [],
            ).append(item)
        for character in characters:
            character["stats"] = self._rows(
                """
                SELECT source_group, property_id, display_name, value,
                       is_percent, ordinal
                FROM battle_character_stat_snapshot
                WHERE battle_record_id = ? AND character_id = ?
                ORDER BY source_group, ordinal, property_id
                """,
                (record_id, int(character["character_id"])),
            )
            for stat in character["stats"]:
                stat["is_percent"] = bool(stat["is_percent"])
            source_groups = {
                str(stat["source_group"]) for stat in character["stats"]
            }
            character["stat_snapshot_source"] = (
                "frozen_v30"
                if {"character", "fork", "equipment"}.issubset(source_groups)
                else ("frozen_v25" if character["stats"] else "missing")
            )
            character["profile"] = _decoded(
                character.pop("raw_profile_json"),
                {},
            )
            character["skills"] = self._rows(
                """
                SELECT skill_id, skill_level
                FROM battle_character_skill_snapshot
                WHERE battle_record_id = ? AND character_id = ?
                ORDER BY skill_id
                """,
                (record_id, int(character["character_id"])),
            )
            character["equipment"] = equipment_by_character.get(
                int(character["character_id"]),
                [],
            )
        header["characters"] = characters
        return header

    def load_battle_axis_evidence(
        self,
        battle_record_id: int,
    ) -> dict[str, Any] | None:
        """Load immutable hit and time-stop evidence for one finalized battle."""

        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        capture = self._one(
            """
            SELECT capture_id, axis_complete, stored_hits, contract_version,
                   source_generation, first_sequence, total_hits
            FROM battle_axis_capture
            WHERE battle_record_id = ? AND capture_state = 'finalized'
            """,
            (record_id,),
        )
        if capture is None:
            return None
        capture_id = int(capture["capture_id"])
        hits = self._rows(
            """
            SELECT sequence_text, sequence_order, timestamp_unix_us,
                   relative_time_us, abyss_half, character_id, character_name,
                   character_known, attribution_status, attribution_source,
                   attribution_unknown_reason, direction, damage,
                   follow_up_damage, total_damage, follow_up_timestamp_unix_us,
                   target_id, target_name, target_monster_id,
                   target_context_json, target_hp_before, target_hp_after,
                   target_max_hp, target_hp_percent, gameplay_effect_index,
                   gameplay_effect_name, ability_name, damage_name,
                   damage_component, attack_type, damage_attribute,
                    follow_up_damage_name, follow_up_damage_component,
                    follow_up_attack_type, follow_up_damage_attribute,
                    follow_up_labels_json, raw_hit_json
            FROM battle_hit_evidence
            WHERE capture_id = ?
            ORDER BY relative_time_us, sequence_order
            """,
            (capture_id,),
        )
        for hit in hits:
            raw_hit = _decoded(hit.pop("raw_hit_json"), {})
            raw_overkill = raw_hit.get("overkill_damage")
            hit["overkill_damage"] = (
                None
                if raw_overkill is None
                else _finite_number(
                    raw_overkill,
                    "raw_hit.overkill_damage",
                    minimum=0.0,
                    maximum=float(hit["damage"]),
                )
            )
            hit["character_known"] = bool(hit["character_known"])
            hit["target_context"] = _decoded(
                hit.pop("target_context_json"),
                [],
            )
            hit["follow_up_labels"] = _decoded(
                hit.pop("follow_up_labels_json"),
                [],
            )
        intervals = self._rows(
            """
            SELECT ordinal, start_unix_us, end_unix_us, duration_us,
                   raw_interval_json
            FROM battle_time_stop_interval
            WHERE capture_id = ? ORDER BY ordinal
            """,
            (capture_id,),
        )
        for interval in intervals:
            interval["raw_interval"] = _decoded(
                interval.pop("raw_interval_json"),
                {},
            )
        capture["hits"] = hits
        capture["time_stop_intervals"] = intervals
        return capture
