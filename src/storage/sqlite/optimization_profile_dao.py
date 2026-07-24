# 管理配装优化档案及版本的 SQLite 访问方法。
from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .user_data_support import (
    ALLOCATION_STRATEGIES,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SNAPSHOT_RETENTION_COUNT,
    SNAPSHOT_SOURCES,
    SUIT_REQUIREMENT_MODES,
    SYNC_METHODS,
    USER_MIGRATIONS,
    SCHEMA_VERSION,
    UserDataError,
    UserDataValidationError,
    _decoded,
    _integer,
    _json,
    _mark_duplicate_modules,
    _plain_object,
    _utc_now,
)

class OptimizationProfileDaoMixin:
    @staticmethod
    def _preference_text(value: Any, label: str, *, required: bool = False) -> str | None:
        if value is None and not required:
            return None
        text = str(value or "").strip()
        if not text:
            if required:
                raise UserDataValidationError(f"{label} 不能为空")
            return None
        return text

    @staticmethod
    def _preference_number(value: Any, label: str) -> float:
        if isinstance(value, bool):
            raise UserDataValidationError(f"{label} 必须是有限数值")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise UserDataValidationError(f"{label} 必须是有限数值") from exc
        if not math.isfinite(number):
            raise UserDataValidationError(f"{label} 必须是有限数值")
        return number

    @classmethod
    def _validated_optimization_characters(
        cls, characters: Sequence[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        if not isinstance(characters, Sequence) or isinstance(characters, (str, bytes)):
            raise UserDataValidationError("characters 必须是角色偏好列表")
        normalized: list[dict[str, Any]] = []
        seen_character_ids: set[int] = set()
        seen_ordinals: set[int] = set()
        for index, value in enumerate(characters):
            row = _plain_object(value, f"characters[{index}]")
            character_id = _integer(row.get("character_id"), f"characters[{index}].character_id", minimum=1)
            ordinal = _integer(row.get("ordinal", index), f"characters[{index}].ordinal", minimum=0)
            priority_group = _integer(
                row.get("priority_group", 0), f"characters[{index}].priority_group", minimum=0
            )
            if character_id in seen_character_ids:
                raise UserDataValidationError("characters 不能包含重复 character_id")
            if ordinal in seen_ordinals:
                raise UserDataValidationError("characters 不能包含重复 ordinal")
            seen_character_ids.add(character_id)
            seen_ordinals.add(ordinal)
            suit_requirement_mode = str(row.get("suit_requirement_mode", "none")).strip()
            if suit_requirement_mode not in SUIT_REQUIREMENT_MODES:
                raise UserDataValidationError("suit_requirement_mode 必须是 none、two_piece 或 four_piece")
            target_suit_id = cls._preference_text(row.get("target_suit_id"), "target_suit_id")
            if suit_requirement_mode != "none" and target_suit_id is None:
                raise UserDataValidationError(
                    "two_piece 或 four_piece 套装约束必须提供 target_suit_id"
                )
            weights_source = row.get("property_weights", {})
            if not isinstance(weights_source, Mapping):
                raise UserDataValidationError(f"characters[{index}].property_weights 必须是对象")
            weights: dict[str, float] = {}
            for property_id, weight in weights_source.items():
                normalized_property_id = cls._preference_text(property_id, "property_id", required=True)
                weights[normalized_property_id] = cls._preference_number(weight, f"{normalized_property_id} weight")

            priorities_source = row.get("substat_priorities", [])
            if not isinstance(priorities_source, Sequence) or isinstance(priorities_source, (str, bytes)):
                raise UserDataValidationError(f"characters[{index}].substat_priorities 必须是列表")
            priorities: list[str] = []
            for property_id in priorities_source:
                normalized_property_id = cls._preference_text(property_id, "substat property_id", required=True)
                if normalized_property_id in priorities:
                    raise UserDataValidationError("substat_priorities 不能包含重复 property_id")
                priorities.append(normalized_property_id)

            limits_source = row.get("property_limits", {})
            if not isinstance(limits_source, Mapping):
                raise UserDataValidationError(f"characters[{index}].property_limits 必须是对象")
            limits: dict[str, dict[str, float | None]] = {}
            for property_id, bounds in limits_source.items():
                normalized_property_id = cls._preference_text(property_id, "property_id", required=True)
                bound_row = _plain_object(bounds, f"{normalized_property_id} limit")
                minimum = bound_row.get("minimum")
                maximum = bound_row.get("maximum")
                if minimum is None and maximum is None:
                    raise UserDataValidationError(f"{normalized_property_id} 至少需要 minimum 或 maximum")
                minimum_number = cls._preference_number(minimum, f"{normalized_property_id} minimum") if minimum is not None else None
                maximum_number = cls._preference_number(maximum, f"{normalized_property_id} maximum") if maximum is not None else None
                if minimum_number is not None and maximum_number is not None and minimum_number > maximum_number:
                    raise UserDataValidationError(f"{normalized_property_id} minimum 不能大于 maximum")
                limits[normalized_property_id] = {"minimum": minimum_number, "maximum": maximum_number}

            normalized.append({
                "character_id": character_id,
                "ordinal": ordinal,
                "priority_group": priority_group,
                "target_suit_id": target_suit_id,
                "suit_requirement_mode": suit_requirement_mode,
                "core_main_property_id": cls._preference_text(row.get("core_main_property_id"), "core_main_property_id"),
                "property_weights": weights,
                "substat_priorities": priorities,
                "property_limits": limits,
            })
        return sorted(normalized, key=lambda row: row["ordinal"])

    def _optimization_version(self, profile_version_id: int) -> dict[str, Any] | None:
        version = self._one(
            """
            SELECT profile_version_id, profile_id, version_number, allocation_strategy, created_at_utc
            FROM optimization_preference_version WHERE profile_version_id = ?
            """,
            (profile_version_id,),
        )
        if version is None:
            return None
        characters = self._rows(
            """
            SELECT character_id, ordinal, priority_group, target_suit_id,
                   suit_requirement_mode, core_main_property_id
            FROM optimization_preference_character
            WHERE profile_version_id = ? ORDER BY ordinal
            """,
            (profile_version_id,),
        )
        for character in characters:
            character_id = character["character_id"]
            character["property_weights"] = {
                row["property_id"]: row["weight"]
                for row in self._rows(
                    """SELECT property_id, weight FROM optimization_preference_property_weight
                       WHERE profile_version_id = ? AND character_id = ? ORDER BY property_id""",
                    (profile_version_id, character_id),
                )
            }
            character["substat_priorities"] = [
                row["property_id"]
                for row in self._rows(
                    """SELECT property_id FROM optimization_preference_substat_priority
                       WHERE profile_version_id = ? AND character_id = ? ORDER BY ordinal""",
                    (profile_version_id, character_id),
                )
            ]
            character["property_limits"] = {
                row["property_id"]: {"minimum": row["minimum_value"], "maximum": row["maximum_value"]}
                for row in self._rows(
                    """SELECT property_id, minimum_value, maximum_value FROM optimization_preference_property_limit
                       WHERE profile_version_id = ? AND character_id = ? ORDER BY property_id""",
                    (profile_version_id, character_id),
                )
            }
        version["characters"] = characters
        return version

    @staticmethod
    def _optimization_strategy(value: Any) -> str:
        strategy = str(value).strip()
        if strategy not in ALLOCATION_STRATEGIES:
            raise UserDataValidationError("allocation_strategy 无效")
        return strategy

    @staticmethod
    def _insert_optimization_profile_version(
        connection: sqlite3.Connection,
        profile_id: int,
        allocation_strategy: str,
        characters: Sequence[Mapping[str, Any]],
    ) -> int:
        """在调用方事务内追加一个不可变偏好版本。"""

        next_version = int(connection.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 AS version_number FROM optimization_preference_version WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()["version_number"])
        cursor = connection.execute(
            """INSERT INTO optimization_preference_version(profile_id, version_number, allocation_strategy, created_at_utc)
               VALUES (?, ?, ?, ?)""",
            (profile_id, next_version, allocation_strategy, _utc_now()),
        )
        profile_version_id = int(cursor.lastrowid)
        for character in characters:
            connection.execute(
                """INSERT INTO optimization_preference_character(
                       profile_version_id, character_id, ordinal, priority_group,
                       target_suit_id, suit_requirement_mode, core_main_property_id
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile_version_id, character["character_id"], character["ordinal"],
                    character["priority_group"], character["target_suit_id"],
                    character["suit_requirement_mode"], character["core_main_property_id"],
                ),
            )
            connection.executemany(
                """INSERT INTO optimization_preference_property_weight(
                       profile_version_id, character_id, property_id, weight
                   ) VALUES (?, ?, ?, ?)""",
                [(profile_version_id, character["character_id"], property_id, weight)
                 for property_id, weight in character["property_weights"].items()],
            )
            connection.executemany(
                """INSERT INTO optimization_preference_substat_priority(
                       profile_version_id, character_id, property_id, ordinal
                   ) VALUES (?, ?, ?, ?)""",
                [(profile_version_id, character["character_id"], property_id, ordinal)
                 for ordinal, property_id in enumerate(character["substat_priorities"])],
            )
            connection.executemany(
                """INSERT INTO optimization_preference_property_limit(
                       profile_version_id, character_id, property_id, minimum_value, maximum_value
                   ) VALUES (?, ?, ?, ?, ?)""",
                [(profile_version_id, character["character_id"], property_id, limit["minimum"], limit["maximum"])
                 for property_id, limit in character["property_limits"].items()],
            )
        return profile_version_id

    def create_optimization_profile(
        self,
        name: str,
        *,
        allocation_strategy: str,
        characters: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """创建优化偏好档案及其不可变的第一个版本。"""

        profile_name = self._preference_text(name, "name", required=True)
        strategy = self._optimization_strategy(allocation_strategy)
        normalized_characters = self._validated_optimization_characters(characters)
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_now()
            cursor = connection.execute(
                """INSERT INTO optimization_preference_profile(name, is_active, created_at_utc, updated_at_utc)
                   VALUES (?, 1, ?, ?)""",
                (profile_name, now, now),
            )
            profile_id = int(cursor.lastrowid)
            self._insert_optimization_profile_version(
                connection, profile_id, strategy, normalized_characters
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise UserDataValidationError(f"优化偏好档案已存在：{profile_name}") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法创建优化偏好档案") from exc
        except BaseException:
            connection.rollback()
            raise
        profile = self.get_optimization_profile(profile_id)
        assert profile is not None
        return profile

    def create_optimization_profile_version(
        self,
        profile_id: int,
        *,
        allocation_strategy: str,
        characters: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """以新版本保存偏好；既有版本永不被编辑覆盖。"""

        raw_profile_id = _integer(profile_id, "profile_id", minimum=1)
        strategy = self._optimization_strategy(allocation_strategy)
        normalized_characters = self._validated_optimization_characters(characters)
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            profile = connection.execute(
                "SELECT profile_id FROM optimization_preference_profile WHERE profile_id = ? AND is_active = 1",
                (raw_profile_id,),
            ).fetchone()
            if profile is None:
                raise UserDataValidationError("优化偏好档案不存在或已停用")
            profile_version_id = self._insert_optimization_profile_version(
                connection, raw_profile_id, strategy, normalized_characters
            )
            connection.execute(
                "UPDATE optimization_preference_profile SET updated_at_utc = ? WHERE profile_id = ?",
                (_utc_now(), raw_profile_id),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法保存优化偏好版本") from exc
        except BaseException:
            connection.rollback()
            raise
        version = self._optimization_version(profile_version_id)
        assert version is not None
        return version

    def get_optimization_profile(
        self, profile_id: int, *, version_number: int | None = None
    ) -> dict[str, Any] | None:
        """读取档案和指定或最新的不可变偏好版本。"""

        raw_profile_id = _integer(profile_id, "profile_id", minimum=1)
        profile = self._one(
            """SELECT profile_id, name, is_active, created_at_utc, updated_at_utc
               FROM optimization_preference_profile WHERE profile_id = ?""",
            (raw_profile_id,),
        )
        if profile is None:
            return None
        profile["is_active"] = bool(profile["is_active"])
        if version_number is None:
            version_row = self._one(
                """SELECT profile_version_id FROM optimization_preference_version
                   WHERE profile_id = ? ORDER BY version_number DESC LIMIT 1""",
                (raw_profile_id,),
            )
        else:
            raw_version_number = _integer(version_number, "version_number", minimum=1)
            version_row = self._one(
                """SELECT profile_version_id FROM optimization_preference_version
                   WHERE profile_id = ? AND version_number = ?""",
                (raw_profile_id, raw_version_number),
            )
        profile["version"] = self._optimization_version(version_row["profile_version_id"]) if version_row else None
        return profile

    def list_optimization_profiles(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        """按最近更新顺序列出账号自己的优化偏好档案。"""

        rows = self._rows(
            """SELECT profile_id FROM optimization_preference_profile
               WHERE is_active = 1 OR ? ORDER BY updated_at_utc DESC, profile_id DESC""",
            (int(include_inactive),),
        )
        return [profile for row in rows if (profile := self.get_optimization_profile(row["profile_id"])) is not None]

    def deactivate_optimization_profile(self, profile_id: int) -> bool:
        """停用档案但保留所有版本，避免历史计算失去可追溯的偏好引用。"""

        raw_profile_id = _integer(profile_id, "profile_id", minimum=1)
        cursor = self._db().execute(
            """UPDATE optimization_preference_profile SET is_active = 0, updated_at_utc = ?
               WHERE profile_id = ? AND is_active = 1""",
            (_utc_now(), raw_profile_id),
        )
        self._db().commit()
        return cursor.rowcount > 0

    @classmethod
    def _validated_character_weight_rows(
        cls, properties: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(properties, Sequence) or isinstance(properties, (str, bytes)):
            raise UserDataValidationError("角色词条权重必须是列表")
        normalized = []
        seen_ids: set[str] = set()
        for ordinal, source in enumerate(properties):
            row = _plain_object(source, f"properties[{ordinal}]")
            property_id = cls._preference_text(
                row.get("property_id"), "property_id", required=True
            )
            if property_id in seen_ids:
                raise UserDataValidationError("角色词条权重不能包含重复 property_id")
            seen_ids.add(property_id)
            weight = cls._preference_number(row.get("weight", 0), f"{property_id} weight")
            main_weight = cls._preference_number(
                row.get("main_weight", 0), f"{property_id} main_weight"
            )
            if weight < 0 or main_weight < 0:
                raise UserDataValidationError("角色词条权重不能小于 0")
            normalized.append({
                "property_id": property_id,
                "weight": weight,
                "main_weight": main_weight,
                "ordinal": ordinal,
            })
        return normalized

    def get_character_weight_preferences(self, character_id: int) -> dict[str, Any] | None:
        """读取账号从静态推荐复制后可独立编辑的角色权重。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        seed = self._one(
            """SELECT character_id, source_dataset_id, source_kind,
                      seeded_at_utc, updated_at_utc
               FROM character_weight_preference_seed WHERE character_id = ?""",
            (raw_character_id,),
        )
        if seed is None:
            return None
        properties = self._rows(
            """SELECT property_id, weight, main_weight, ordinal
               FROM character_weight_preference_property
               WHERE character_id = ? ORDER BY ordinal""",
            (raw_character_id,),
        )
        seed["properties"] = properties
        seed["property_weights"] = {
            row["property_id"]: float(row["weight"])
            for row in properties if float(row["weight"]) > 0
        }
        seed["main_property_weights"] = {
            row["property_id"]: float(row["main_weight"])
            for row in properties if float(row["main_weight"]) > 0
        }
        return seed

    def seed_character_weight_preferences(
        self,
        character_id: int,
        *,
        properties: Sequence[Mapping[str, Any]],
        source_dataset_id: str,
        source_kind: str,
    ) -> dict[str, Any]:
        """首次复制静态推荐；已存在的账号编辑永不被新版静态库覆盖。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        rows = self._validated_character_weight_rows(properties)
        dataset_id = self._preference_text(
            source_dataset_id, "source_dataset_id", required=True
        )
        normalized_source_kind = self._preference_text(
            source_kind, "source_kind", required=True
        )
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT 1 FROM character_weight_preference_seed WHERE character_id = ?",
                (raw_character_id,),
            ).fetchone()
            if existing is None:
                now = _utc_now()
                connection.execute(
                    """INSERT INTO character_weight_preference_seed(
                           character_id, source_dataset_id, source_kind,
                           seeded_at_utc, updated_at_utc
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (raw_character_id, dataset_id, normalized_source_kind, now, now),
                )
                connection.executemany(
                    """INSERT INTO character_weight_preference_property(
                           character_id, property_id, weight, main_weight, ordinal
                       ) VALUES (?, ?, ?, ?, ?)""",
                    [
                        (
                            raw_character_id, row["property_id"], row["weight"],
                            row["main_weight"], row["ordinal"],
                        )
                        for row in rows
                    ],
                )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法初始化角色词条权重") from exc
        except BaseException:
            connection.rollback()
            raise
        result = self.get_character_weight_preferences(raw_character_id)
        assert result is not None
        return result

    def save_character_weight_preferences(
        self,
        character_id: int,
        *,
        properties: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """保存账号角色权重，不修改静态推荐或既有计算版本。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        rows = self._validated_character_weight_rows(properties)
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM character_weight_preference_seed WHERE character_id = ?",
                (raw_character_id,),
            ).fetchone() is None:
                raise UserDataValidationError("角色词条权重尚未从静态库初始化")
            connection.execute(
                "DELETE FROM character_weight_preference_property WHERE character_id = ?",
                (raw_character_id,),
            )
            connection.executemany(
                """INSERT INTO character_weight_preference_property(
                       character_id, property_id, weight, main_weight, ordinal
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        raw_character_id, row["property_id"], row["weight"],
                        row["main_weight"], row["ordinal"],
                    )
                    for row in rows
                ],
            )
            connection.execute(
                """UPDATE character_weight_preference_seed
                   SET source_kind = 'account', updated_at_utc = ?
                   WHERE character_id = ?""",
                (_utc_now(), raw_character_id),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法保存角色词条权重") from exc
        except BaseException:
            connection.rollback()
            raise
        result = self.get_character_weight_preferences(raw_character_id)
        assert result is not None
        return result

    def refresh_unmodified_character_weight_preferences(
        self,
        character_id: int,
        *,
        properties: Sequence[Mapping[str, Any]],
        source_dataset_id: str,
        source_kind: str,
    ) -> dict[str, Any] | None:
        """Refresh a never-edited default copy without overwriting account edits."""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        rows = self._validated_character_weight_rows(properties)
        dataset_id = self._preference_text(
            source_dataset_id, "source_dataset_id", required=True
        )
        normalized_source_kind = self._preference_text(
            source_kind, "source_kind", required=True
        )
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            seed = connection.execute(
                """SELECT source_kind, seeded_at_utc, updated_at_utc
                   FROM character_weight_preference_seed
                   WHERE character_id = ?""",
                (raw_character_id,),
            ).fetchone()
            if (
                seed is None
                or str(seed["source_kind"]) != "default"
                or str(seed["seeded_at_utc"]) != str(seed["updated_at_utc"])
            ):
                connection.rollback()
                return None
            connection.execute(
                "DELETE FROM character_weight_preference_property WHERE character_id = ?",
                (raw_character_id,),
            )
            connection.executemany(
                """INSERT INTO character_weight_preference_property(
                       character_id, property_id, weight, main_weight, ordinal
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (
                        raw_character_id, row["property_id"], row["weight"],
                        row["main_weight"], row["ordinal"],
                    )
                    for row in rows
                ],
            )
            now = _utc_now()
            connection.execute(
                """UPDATE character_weight_preference_seed
                   SET source_dataset_id = ?, source_kind = 'default',
                       seeded_at_utc = ?, updated_at_utc = ?
                   WHERE character_id = ?""",
                (dataset_id, now, now, raw_character_id),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法刷新未修改的角色词条权重") from exc
        except BaseException:
            connection.rollback()
            raise
        return self.get_character_weight_preferences(raw_character_id)

    def get_character_shape_bonus_preferences(
        self, character_id: int,
    ) -> dict[str, Any] | None:
        """读取账号对官方额外形状标签和加成的覆写。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        record = self._one(
            """SELECT character_id, shape_label, updated_at_utc
               FROM character_shape_bonus_preference WHERE character_id = ?""",
            (raw_character_id,),
        )
        if record is None:
            return None
        properties = self._rows(
            """SELECT property_id, display_value, ordinal
               FROM character_shape_bonus_preference_property
               WHERE character_id = ? ORDER BY ordinal""",
            (raw_character_id,),
        )
        record["properties"] = properties
        record["property_values"] = {
            str(row["property_id"]): float(row["display_value"])
            for row in properties
        }
        return record

    def save_character_shape_bonus_preferences(
        self,
        character_id: int,
        *,
        shape_label: str,
        property_values: Mapping[str, Any],
    ) -> dict[str, Any]:
        """保存账号角色的额外形状覆写，不改动发行版静态数据。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        label = str(shape_label or "").strip()
        if len(label) > 100:
            raise UserDataValidationError("额外形状标签不能超过 100 个字符")
        if not isinstance(property_values, Mapping):
            raise UserDataValidationError("额外形状加成必须是对象")
        rows = []
        for ordinal, (raw_property_id, raw_value) in enumerate(property_values.items()):
            property_id = str(raw_property_id or "").strip()
            if not property_id:
                raise UserDataValidationError("额外形状加成 property_id 不能为空")
            value = self._preference_number(raw_value, "额外形状加成数值")
            if value < 0:
                raise UserDataValidationError("额外形状加成数值不能小于 0")
            rows.append((raw_character_id, property_id, value, ordinal))
        if len({row[1] for row in rows}) != len(rows):
            raise UserDataValidationError("额外形状加成不能包含重复属性")
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO character_shape_bonus_preference(
                    character_id, shape_label, updated_at_utc
                ) VALUES (?, ?, ?)
                ON CONFLICT(character_id) DO UPDATE SET
                    shape_label = excluded.shape_label,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (raw_character_id, label, _utc_now()),
            )
            connection.execute(
                "DELETE FROM character_shape_bonus_preference_property WHERE character_id = ?",
                (raw_character_id,),
            )
            connection.executemany(
                """
                INSERT INTO character_shape_bonus_preference_property(
                    character_id, property_id, display_value, ordinal
                ) VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法保存角色额外形状加成") from exc
        except BaseException:
            connection.rollback()
            raise
        result = self.get_character_shape_bonus_preferences(raw_character_id)
        assert result is not None
        return result

    def get_character_profile(self, character_id: int) -> dict[str, Any] | None:
        """读取一个只含官方 ID 指针和账号养成状态的角色档案。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        profile = self._one(
            """
            SELECT character_id, character_level, breakthrough_stage,
                   awakening_level, fork_id, fork_level,
                   fork_refinement_level, selected_skill_id, ordinal,
                   is_active, created_at_utc, updated_at_utc
            FROM character_profile WHERE character_id = ?
            """,
            (raw_character_id,),
        )
        if profile is None:
            return None
        profile["is_active"] = bool(profile["is_active"])
        profile["skill_levels"] = {
            row["skill_id"]: int(row["skill_level"])
            for row in self._rows(
                """SELECT skill_id, skill_level FROM character_profile_skill
                   WHERE character_id = ? ORDER BY skill_id""",
                (raw_character_id,),
            )
        }
        return profile

    def list_character_profiles(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        """按用户角色页顺序列出账号角色指针。"""

        rows = self._rows(
            """SELECT character_id FROM character_profile
               WHERE is_active = 1 OR ? ORDER BY ordinal, character_id""",
            (int(include_inactive),),
        )
        return [
            profile
            for row in rows
            if (profile := self.get_character_profile(row["character_id"])) is not None
        ]

    def save_character_profile(
        self,
        *,
        character_id: int,
        character_level: int,
        breakthrough_stage: int,
        awakening_level: int,
        fork_id: str | None,
        fork_level: int | None,
        fork_refinement_level: int | None,
        selected_skill_id: str | None = None,
        skill_levels: Mapping[str, int] | None = None,
        ordinal: int = 0,
        is_active: bool = True,
    ) -> dict[str, Any]:
        """原子保存角色指针；角色、弧盘和技能详情仍由官方静态库解析。"""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        raw_level = _integer(character_level, "character_level", minimum=1)
        if raw_level > 80:
            raise UserDataValidationError("character_level 不能大于 80")
        raw_breakthrough = _integer(breakthrough_stage, "breakthrough_stage", minimum=0)
        if raw_breakthrough > 6:
            raise UserDataValidationError("breakthrough_stage 不能大于 6")
        raw_awakening = _integer(awakening_level, "awakening_level", minimum=0)
        if raw_awakening > 6:
            raise UserDataValidationError("awakening_level 不能大于 6")
        raw_ordinal = _integer(ordinal, "ordinal", minimum=0)
        raw_fork_id = self._preference_text(fork_id, "fork_id")
        if raw_fork_id is None:
            raw_fork_level = None
            raw_refinement = None
        else:
            raw_fork_level = _integer(fork_level, "fork_level", minimum=1)
            if raw_fork_level > 80:
                raise UserDataValidationError("fork_level 不能大于 80")
            raw_refinement = _integer(
                fork_refinement_level, "fork_refinement_level", minimum=1
            )
            if raw_refinement > 5:
                raise UserDataValidationError("fork_refinement_level 不能大于 5")
        raw_selected_skill = self._preference_text(selected_skill_id, "selected_skill_id")
        normalized_skills: dict[str, int] = {}
        for skill_id, skill_level in dict(skill_levels or {}).items():
            raw_skill_id = self._preference_text(skill_id, "skill_id", required=True)
            normalized_skills[raw_skill_id] = _integer(
                skill_level, f"{raw_skill_id} skill_level", minimum=1
            )
        if raw_selected_skill and raw_selected_skill not in normalized_skills:
            raise UserDataValidationError("selected_skill_id 必须存在于 skill_levels")

        connection = self._db()
        now = _utc_now()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO character_profile(
                    character_id, character_level, breakthrough_stage,
                    awakening_level, fork_id, fork_level,
                    fork_refinement_level, selected_skill_id, ordinal,
                    is_active, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(character_id) DO UPDATE SET
                    character_level = excluded.character_level,
                    breakthrough_stage = excluded.breakthrough_stage,
                    awakening_level = excluded.awakening_level,
                    fork_id = excluded.fork_id,
                    fork_level = excluded.fork_level,
                    fork_refinement_level = excluded.fork_refinement_level,
                    selected_skill_id = excluded.selected_skill_id,
                    ordinal = excluded.ordinal,
                    is_active = excluded.is_active,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    raw_character_id, raw_level, raw_breakthrough, raw_awakening,
                    raw_fork_id, raw_fork_level, raw_refinement,
                    raw_selected_skill, raw_ordinal, int(bool(is_active)), now, now,
                ),
            )
            connection.execute(
                "DELETE FROM character_profile_skill WHERE character_id = ?",
                (raw_character_id,),
            )
            connection.executemany(
                """INSERT INTO character_profile_skill(character_id, skill_id, skill_level)
                   VALUES (?, ?, ?)""",
                [
                    (raw_character_id, skill_id, skill_level)
                    for skill_id, skill_level in normalized_skills.items()
                ],
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise UserDataValidationError("角色页顺序或养成指针无效") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法保存角色养成指针") from exc
        profile = self.get_character_profile(raw_character_id)
        assert profile is not None
        return profile
