# 保存战报原始快照之上的单个用户角色配置副本。
"""Account-owned battle build edit persistence."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.battle_report_transfer import battle_equipment_sha256

from .protocols import UserDataDaoMixinHost
from .user_data_support import (
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _json_object,
    _utc_now,
)


class BattleBuildEditDaoMixin(UserDataDaoMixinHost):
    """Own the one-edit-per-battle transaction and activation pointer."""

    def load_battle_build_edit(
        self,
        battle_record_id: int,
    ) -> dict[str, Any] | None:
        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        header = self._one(
            "SELECT * FROM battle_build_edit WHERE battle_record_id = ?",
            (record_id,),
        )
        if header is None:
            return None
        header["is_active"] = bool(header["is_active"])
        characters = self._rows(
            """
            SELECT * FROM battle_character_build_edit
            WHERE battle_record_id = ? ORDER BY ordinal, character_id
            """,
            (record_id,),
        )
        for character in characters:
            character_id = int(character["character_id"])
            profile = _decoded(character.pop("raw_profile_json"), {})
            skills = self._rows(
                """
                SELECT skill_id, skill_level FROM battle_character_skill_edit
                WHERE battle_record_id = ? AND character_id = ?
                ORDER BY skill_id
                """,
                (record_id, character_id),
            )
            awakenings = self._rows(
                """
                SELECT effect_id, ordinal FROM battle_character_awaken_edit
                WHERE battle_record_id = ? AND character_id = ?
                ORDER BY ordinal, effect_id
                """,
                (record_id, character_id),
            )
            profile["skill_levels"] = {
                str(row["skill_id"]): int(row["skill_level"])
                for row in skills
            }
            profile["selected_awaken_effect_ids"] = [
                str(row["effect_id"]) for row in awakenings
            ]
            profile["awakening_selection_initialized"] = True
            profile["likeability_level_10_enabled"] = bool(
                character["likeability_level_10_enabled"]
            )
            character["likeability_level_10_enabled"] = bool(
                character["likeability_level_10_enabled"]
            )
            character["profile"] = profile
            character["skills"] = skills
            character["selected_awaken_effect_ids"] = tuple(
                profile["selected_awaken_effect_ids"]
            )
        header["characters"] = characters
        return header

    def load_battle_report_import_origin(
        self,
        battle_record_id: int,
    ) -> dict[str, Any] | None:
        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        return self._one(
            "SELECT * FROM battle_report_import_origin WHERE battle_record_id = ?",
            (record_id,),
        )

    def load_battle_import_equipment_locks(
        self,
        battle_record_id: int,
    ) -> dict[int, dict[str, Any]]:
        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        rows = self._rows(
            """
            SELECT character_id, equipment_source_kind, equipment_sha256,
                   locked_equipment_json, created_at_utc
            FROM battle_character_import_equipment_lock
            WHERE battle_record_id = ? ORDER BY character_id
            """,
            (record_id,),
        )
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            equipment = _decoded(row.pop("locked_equipment_json"), [])
            if not isinstance(equipment, list):
                raise UserDataError("导入战报的固化配装不是数组")
            digest = battle_equipment_sha256(equipment)
            if digest != str(row["equipment_sha256"]):
                raise UserDataError("导入战报的固化配装摘要不匹配")
            row["equipment"] = equipment
            result[int(row["character_id"])] = row
        return result

    def battle_report_equipment_editable(self, battle_record_id: int) -> bool:
        return self.load_battle_report_import_origin(battle_record_id) is None

    def battle_report_counterfactual_editable(self, battle_record_id: int) -> bool:
        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        row = self._one(
            """
            SELECT contract_version, capture_state
            FROM battle_axis_capture WHERE battle_record_id = ?
            """,
            (record_id,),
        )
        return bool(
            row is not None
            and str(row.get("capture_state") or "") == "finalized"
            and int(row.get("contract_version") or 0) >= 4
        )

    def repair_battle_build_edit_shape_profiles(
        self,
        shape_fields_by_character: Mapping[int, Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Backfill derived official shape fields in mutable battle edit copies."""

        normalized = {
            int(character_id): {
                "extra_shape_label": str(fields.get("extra_shape_label") or ""),
                "extra_shape_buffs": {
                    str(property_id): float(value)
                    for property_id, value in (
                        fields.get("extra_shape_buffs") or {}
                    ).items()
                },
                "extra_shape_source": "static_database",
            }
            for character_id, fields in shape_fields_by_character.items()
        }
        connection = self._db()
        rows = connection.execute(
            """SELECT battle_record_id, character_id, raw_profile_json
               FROM battle_character_build_edit
               ORDER BY battle_record_id, character_id"""
        ).fetchall()
        updates: list[tuple[str, int, int]] = []
        changed_characters: set[int] = set()
        for row in rows:
            character_id = int(row["character_id"])
            fields = normalized.get(character_id)
            if fields is None:
                continue
            profile = _decoded(row["raw_profile_json"], {})
            if all(profile.get(key) == value for key, value in fields.items()):
                continue
            profile.update(fields)
            updates.append((
                _json_object(profile, "战报角色修改副本额外形状修复"),
                int(row["battle_record_id"]),
                character_id,
            ))
            changed_characters.add(character_id)
        if updates:
            with connection:
                connection.executemany(
                    """UPDATE battle_character_build_edit
                       SET raw_profile_json = ?
                       WHERE battle_record_id = ? AND character_id = ?""",
                    updates,
                )
        return {
            "updated_profile_count": len(updates),
            "updated_character_ids": sorted(changed_characters),
            "skipped_profile_count": len(rows) - len(updates),
        }

    def save_battle_build_edit(
        self,
        battle_record_id: int,
        profiles: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        normalized = [self._normalize_battle_build_edit_profile(row) for row in profiles]
        if not normalized:
            raise UserDataValidationError("战报角色修改副本不能为空")
        if len({row["character_id"] for row in normalized}) != len(normalized):
            raise UserDataValidationError("战报角色修改副本包含重复角色")
        connection = self._db()
        import_origin = connection.execute(
            "SELECT 1 FROM battle_report_import_origin WHERE battle_record_id = ?",
            (record_id,),
        ).fetchone()
        if import_origin is not None:
            lock_rows = connection.execute(
                """
                SELECT character_id, equipment_sha256
                FROM battle_character_import_equipment_lock
                WHERE battle_record_id = ?
                """,
                (record_id,),
            ).fetchall()
            locks = {
                int(row["character_id"]): str(row["equipment_sha256"])
                for row in lock_rows
            }
            if {int(row["character_id"]) for row in normalized} != set(locks):
                raise UserDataValidationError("导入战报缺少完整的逐角色配装锁")
            for row in normalized:
                if "equipment_override" not in row:
                    continue
                if battle_equipment_sha256(row["equipment_override"]) != locks[
                    int(row["character_id"])
                ]:
                    raise UserDataValidationError("导入战报的空幕/驱动不可修改")
                for key in (
                    "equipment_override",
                    "equipment_context_key",
                    "equipment_context_title",
                    "equipment_source_kind",
                ):
                    row.pop(key, None)

        equipment_owners: dict[tuple[int, int], int] = {}
        for row in normalized:
            for item in row.get("equipment_override") or ():
                uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                previous = equipment_owners.setdefault(uid, int(row["character_id"]))
                if previous != int(row["character_id"]):
                    raise UserDataValidationError("战报配装副本包含跨角色重复装备 UID")
        try:
            original_rows = connection.execute(
                """
                SELECT character_id FROM battle_character_build_snapshot
                WHERE battle_record_id = ? ORDER BY character_id
                """,
                (record_id,),
            ).fetchall()
            original_ids = {int(row["character_id"]) for row in original_rows}
            edited_ids = {int(row["character_id"]) for row in normalized}
            if not original_ids:
                raise UserDataValidationError("当前战报没有可编辑的角色配置快照")
            if edited_ids != original_ids:
                raise UserDataValidationError("修改副本必须完整包含原始战报中的全部角色")
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO battle_build_edit(
                    battle_record_id, is_active, created_at_utc, updated_at_utc
                ) VALUES (?, 1, ?, ?)
                ON CONFLICT(battle_record_id) DO UPDATE SET
                    is_active = 1,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (record_id, now, now),
            )
            connection.execute(
                "DELETE FROM battle_character_build_edit WHERE battle_record_id = ?",
                (record_id,),
            )
            for profile in normalized:
                self._insert_battle_build_edit_profile(connection, record_id, profile)
            connection.commit()
        except (sqlite3.Error, UserDataError, UserDataValidationError):
            connection.rollback()
            raise
        result = self.load_battle_build_edit(record_id)
        if result is None:
            raise UserDataError("战报角色修改副本保存后无法读取")
        return result

    def set_battle_build_edit_active(
        self,
        battle_record_id: int,
        active: bool,
    ) -> dict[str, Any]:
        record_id = _integer(battle_record_id, "battle_record_id", minimum=1)
        connection = self._db()
        cursor = connection.execute(
            """
            UPDATE battle_build_edit
            SET is_active = ?, updated_at_utc = ?
            WHERE battle_record_id = ?
            """,
            (int(bool(active)), _utc_now(), record_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise UserDataValidationError("当前战报还没有角色修改副本")
        connection.commit()
        result = self.load_battle_build_edit(record_id)
        if result is None:
            raise UserDataError("战报角色修改副本状态更新后无法读取")
        return result

    @staticmethod
    def _normalize_battle_build_edit_profile(
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        profile = dict(value)
        character_id = _integer(profile.get("character_id"), "character_id", minimum=1)
        skills = profile.get("skill_levels") or {}
        if not isinstance(skills, Mapping):
            raise UserDataValidationError(f"角色 {character_id} 的技能等级必须是对象")
        normalized_skills = {
            str(skill_id).strip(): _integer(level, "skill_level", minimum=1)
            for skill_id, level in skills.items()
            if str(skill_id).strip()
        }
        if any(level > 11 for level in normalized_skills.values()):
            raise UserDataValidationError("技能基础等级不能高于 11")
        selected_skill_id = str(profile.get("selected_skill_id") or "").strip() or None
        if selected_skill_id and selected_skill_id not in normalized_skills:
            raise UserDataValidationError("selected_skill_id 必须存在于 skill_levels")
        awakenings = profile.get("selected_awaken_effect_ids") or ()
        if isinstance(awakenings, (str, bytes)) or not isinstance(awakenings, Sequence):
            raise UserDataValidationError("觉醒选择必须是列表")
        selected_awakenings = tuple(
            dict.fromkeys(
                str(effect_id).strip()
                for effect_id in awakenings
                if str(effect_id).strip()
            )
        )
        if len(selected_awakenings) > 6:
            raise UserDataValidationError("觉醒选择不能超过 6 项")
        character_level = _integer(profile.get("character_level"), "character_level", minimum=1)
        breakthrough_stage = _integer(
            profile.get("breakthrough_stage"), "breakthrough_stage", minimum=0
        )
        if character_level > 80 or breakthrough_stage > 6:
            raise UserDataValidationError("角色等级或突破阶段超出范围")
        fork_id = str(profile.get("fork_id") or "").strip() or None
        if fork_id is None:
            fork_level = None
            fork_refinement_level = None
        else:
            fork_level = _integer(profile.get("fork_level"), "fork_level", minimum=1)
            fork_refinement_level = _integer(
                profile.get("fork_refinement_level"),
                "fork_refinement_level",
                minimum=1,
            )
            if fork_level > 80 or fork_refinement_level > 5:
                raise UserDataValidationError("弧盘等级或精炼等级超出范围")
        profile.update({
            "character_id": character_id,
            "profile_source": "user_edited_snapshot",
            "character_level": character_level,
            "breakthrough_stage": breakthrough_stage,
            "awakening_level": len(selected_awakenings),
            "selected_awaken_effect_ids": list(selected_awakenings),
            "awakening_selection_initialized": True,
            "likeability_level_10_enabled": bool(
                profile.get("likeability_level_10_enabled")
            ),
            "fork_id": fork_id,
            "fork_level": fork_level,
            "fork_refinement_level": fork_refinement_level,
            "selected_skill_id": selected_skill_id,
            "skill_levels": normalized_skills,
            "ordinal": _integer(profile.get("ordinal", 0), "ordinal", minimum=0),
        })
        stat_overrides = profile.get("battle_stat_overrides") or {}
        if not isinstance(stat_overrides, Mapping):
            raise UserDataValidationError("战报边际属性调整必须是对象")
        normalized_overrides = {}
        for property_id, raw_value in stat_overrides.items():
            key = str(property_id).strip()
            if not key:
                continue
            try:
                number = float(raw_value)
            except (TypeError, ValueError) as error:
                raise UserDataValidationError("战报边际属性调整必须是有限数值") from error
            if not math.isfinite(number):
                raise UserDataValidationError("战报边际属性调整必须是有限数值")
            normalized_overrides[key] = number
        profile["battle_stat_overrides"] = normalized_overrides
        if "equipment_override" in profile:
            equipment = profile.get("equipment_override")
            if isinstance(equipment, (str, bytes)) or not isinstance(
                equipment, Sequence
            ):
                raise UserDataValidationError("战报配装覆盖必须是列表")
            normalized_equipment = [
                BattleBuildEditDaoMixin._normalize_equipment_override_item(item)
                for item in equipment
            ]
            uids = [
                (int(item["uid_slot"]), int(item["uid_serial"]))
                for item in normalized_equipment
            ]
            if len(set(uids)) != len(uids):
                raise UserDataValidationError("战报配装副本包含重复装备 UID")
            profile["equipment_override"] = normalized_equipment
            profile["equipment_context_key"] = str(
                profile.get("equipment_context_key") or "edited"
            )
            profile["equipment_context_title"] = str(
                profile.get("equipment_context_title") or "修改副本配装"
            )
            profile["equipment_source_kind"] = str(
                profile.get("equipment_source_kind") or "edited_copy"
            )
        return profile

    @staticmethod
    def _normalize_equipment_override_item(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise UserDataValidationError("战报配装覆盖包含无效装备")
        item = dict(value)
        uid_slot = _integer(item.get("uid_slot"), "uid_slot", minimum=0)
        uid_serial = _integer(item.get("uid_serial"), "uid_serial", minimum=0)
        if uid_slot <= 0 or uid_serial <= 0:
            raise UserDataValidationError("虚拟补位不能写入战报配装副本")
        kind = str(item.get("kind") or "").strip()
        if kind not in {"core", "module"}:
            raise UserDataValidationError("战报配装类型必须是 core 或 module")
        item_id = str(item.get("item_id") or "").strip()
        if not item_id:
            raise UserDataValidationError("战报配装缺少 item_id")
        stats = item.get("stats") or ()
        if isinstance(stats, (str, bytes)) or not isinstance(stats, Sequence):
            raise UserDataValidationError("战报配装词条必须是列表")
        normalized_stats = []
        for ordinal, raw in enumerate(stats):
            if not isinstance(raw, Mapping):
                raise UserDataValidationError("战报配装包含无效词条")
            stat_group = str(raw.get("stat_group") or "").strip()
            property_id = str(raw.get("property_id") or "").strip()
            value_number = float(raw.get("value") or 0.0)
            if stat_group not in {"main", "sub"} or not property_id:
                raise UserDataValidationError("战报配装词条缺少分组或属性 ID")
            if not math.isfinite(value_number):
                raise UserDataValidationError("战报配装词条数值必须有限")
            normalized_stats.append(
                {
                    "stat_group": stat_group,
                    "ordinal": ordinal,
                    "property_id": property_id,
                    "value": value_number,
                    "is_percent": bool(
                        raw.get("is_percent", raw.get("percent", False))
                    ),
                    "names": dict(raw.get("names") or {}),
                }
            )
        item.update(
            {
                "uid_slot": uid_slot,
                "uid_serial": uid_serial,
                "kind": kind,
                "item_id": item_id,
                "grid_count": _integer(
                    item.get("grid_count", 0), "grid_count", minimum=0
                ),
                "locked": bool(item.get("locked")),
                "names": dict(item.get("names") or {}),
                "suit_names": dict(item.get("suit_names") or {}),
                "stats": normalized_stats,
            }
        )
        return item

    @staticmethod
    def _insert_battle_build_edit_profile(
        connection: sqlite3.Connection,
        record_id: int,
        profile: Mapping[str, Any],
    ) -> None:
        character_id = int(profile["character_id"])
        connection.execute(
            """
            INSERT INTO battle_character_build_edit(
                battle_record_id, character_id, observed_name, profile_source,
                character_level, breakthrough_stage, awakening_level,
                likeability_level_10_enabled, fork_id, fork_level,
                fork_refinement_level, selected_skill_id, ordinal,
                raw_profile_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                character_id,
                str(profile.get("observed_name") or "") or None,
                "user_edited_snapshot",
                int(profile["character_level"]),
                int(profile["breakthrough_stage"]),
                int(profile["awakening_level"]),
                int(bool(profile["likeability_level_10_enabled"])),
                str(profile.get("fork_id") or "") or None,
                profile.get("fork_level"),
                profile.get("fork_refinement_level"),
                str(profile.get("selected_skill_id") or "") or None,
                int(profile["ordinal"]),
                _json_object(profile, "战报角色修改副本"),
            ),
        )
        connection.executemany(
            """
            INSERT INTO battle_character_skill_edit(
                battle_record_id, character_id, skill_id, skill_level
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (record_id, character_id, skill_id, int(level))
                for skill_id, level in sorted(profile["skill_levels"].items())
            ],
        )
        connection.executemany(
            """
            INSERT INTO battle_character_awaken_edit(
                battle_record_id, character_id, effect_id, ordinal
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (record_id, character_id, effect_id, ordinal)
                for ordinal, effect_id in enumerate(
                    profile["selected_awaken_effect_ids"]
                )
            ],
        )
