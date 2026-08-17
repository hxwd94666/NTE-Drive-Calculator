# 管理配装优化档案及版本的 SQLite 访问方法。
from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from .user_data_support import (
    ALLOCATION_STRATEGIES,
    SUIT_REQUIREMENT_MODES,
    UserDataError,
    UserDataValidationError,
    _integer,
    _plain_object,
    _utc_now,
)

from src.storage.sqlite.character_profile_dao import CharacterProfileDaoMixin

class OptimizationProfileDaoMixin(CharacterProfileDaoMixin):
    _GRADE_LIMITS = frozenset({"D", "C", "B", "A", "S", "SS", "SSS", "ACE"})

    @classmethod
    def _validated_optimization_characters(
        cls, characters: Any,
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

            blacklist_source = row.get("substat_blacklist", [])
            if not isinstance(blacklist_source, Sequence) or isinstance(blacklist_source, (str, bytes)):
                raise UserDataValidationError(f"characters[{index}].substat_blacklist 必须是列表")
            blacklist: list[str] = []
            for property_id in blacklist_source:
                normalized_property_id = cls._preference_text(
                    property_id, "blacklist property_id", required=True
                )
                if normalized_property_id in blacklist:
                    raise UserDataValidationError("substat_blacklist 不能包含重复 property_id")
                blacklist.append(normalized_property_id)

            equal_priority = row.get("equal_priority", False)
            ignore_grade_limit = row.get("ignore_grade_limit", False)
            blacklist_zero_weight = row.get("blacklist_zero_weight", False)
            if not isinstance(equal_priority, bool):
                raise UserDataValidationError(
                    f"characters[{index}].equal_priority 必须是布尔值"
                )
            if not isinstance(ignore_grade_limit, bool):
                raise UserDataValidationError(
                    f"characters[{index}].ignore_grade_limit 必须是布尔值"
                )
            if not isinstance(blacklist_zero_weight, bool):
                raise UserDataValidationError(
                    f"characters[{index}].blacklist_zero_weight 必须是布尔值"
                )
            min_grade_limit = str(row.get("min_grade_limit") or "A").upper()
            if min_grade_limit not in cls._GRADE_LIMITS:
                raise UserDataValidationError(
                    f"characters[{index}].min_grade_limit 无效"
                )
            raw_crit_threshold = row.get("crit_threshold")
            crit_threshold = (
                cls._preference_number(
                    raw_crit_threshold,
                    f"characters[{index}].crit_threshold",
                )
                if raw_crit_threshold is not None
                else None
            )
            if crit_threshold is not None and not 0.0 <= crit_threshold <= 100.0:
                raise UserDataValidationError(
                    f"characters[{index}].crit_threshold 必须在 0 到 100 之间"
                )

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
                "substat_blacklist": blacklist,
                "blacklist_zero_weight": blacklist_zero_weight,
                "equal_priority": equal_priority,
                "ignore_grade_limit": ignore_grade_limit,
                "min_grade_limit": min_grade_limit,
                "crit_threshold": crit_threshold,
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
            character["substat_blacklist"] = [
                row["property_id"]
                for row in self._rows(
                    """SELECT property_id FROM optimization_preference_substat_blacklist
                       WHERE profile_version_id = ? AND character_id = ? ORDER BY ordinal""",
                    (profile_version_id, character_id),
                )
            ]
            behavior = self._one(
                """SELECT equal_priority, ignore_grade_limit, blacklist_zero_weight, min_grade_limit,
                          crit_threshold
                   FROM optimization_preference_substat_behavior
                   WHERE profile_version_id = ? AND character_id = ?""",
                (profile_version_id, character_id),
            )
            character["equal_priority"] = bool(
                behavior["equal_priority"] if behavior is not None else False
            )
            character["ignore_grade_limit"] = bool(
                behavior["ignore_grade_limit"]
                if behavior is not None
                else character["substat_priorities"]
            )
            character["blacklist_zero_weight"] = bool(
                behavior["blacklist_zero_weight"] if behavior is not None else False
            )
            character["min_grade_limit"] = (
                str(behavior["min_grade_limit"]) if behavior is not None else "A"
            )
            character["crit_threshold"] = (
                behavior["crit_threshold"] if behavior is not None else None
            )
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
        # 2.0 移除了驱动优先。旧档案在下一次保存时平滑转为语义最接近的
        # 全局最优，避免历史 SQLite 记录阻塞权重页面加载。
        if strategy == "drive_priority":
            strategy = "global_optimal"
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
        if cursor.lastrowid is None:
            raise UserDataError("创建优化档案版本后未返回 profile_version_id")
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
                """INSERT INTO optimization_preference_substat_blacklist(
                       profile_version_id, character_id, property_id, ordinal
                   ) VALUES (?, ?, ?, ?)""",
                [(profile_version_id, character["character_id"], property_id, ordinal)
                 for ordinal, property_id in enumerate(character["substat_blacklist"])],
            )
            connection.execute(
                """INSERT INTO optimization_preference_substat_behavior(
                       profile_version_id, character_id, equal_priority,
                       ignore_grade_limit, blacklist_zero_weight, min_grade_limit, crit_threshold
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    profile_version_id,
                    character["character_id"],
                    int(character["equal_priority"]),
                    int(character["ignore_grade_limit"]),
                    int(character["blacklist_zero_weight"]),
                    character["min_grade_limit"],
                    character["crit_threshold"],
                ),
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
            if cursor.lastrowid is None:
                raise UserDataError("创建优化档案后未返回 profile_id")
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
