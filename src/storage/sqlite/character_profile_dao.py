# 管理角色权重、额外形状和角色配置的 SQLite 持久化。
from __future__ import annotations

import sqlite3
import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal, overload

from .user_data_support import (
    UserDataError,
    UserDataValidationError,
    _integer,
    _plain_object,
    _utc_now,
)
from .protocols import UserDataDaoMixinHost


class CharacterProfileDaoMixin(UserDataDaoMixinHost):
    @staticmethod
    @overload
    def _preference_text(
        value: Any,
        label: str,
        *,
        required: Literal[True],
    ) -> str: ...

    @staticmethod
    @overload
    def _preference_text(
        value: Any,
        label: str,
        *,
        required: Literal[False] = False,
    ) -> str | None: ...

    @staticmethod
    def _preference_text(
        value: Any,
        label: str,
        *,
        required: bool = False,
    ) -> str | None:
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
    def _validated_character_weight_rows(
        cls, properties: Any,
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
        self._preference_text(source_kind, "source_kind", required=True)
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

    def reset_character_weight_preferences_to_default(
        self,
        character_id: int,
        *,
        properties: Sequence[Mapping[str, Any]],
        source_dataset_id: str,
    ) -> dict[str, Any]:
        """Replace one account override with the current refreshable default."""

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        rows = self._validated_character_weight_rows(properties)
        dataset_id = self._preference_text(
            source_dataset_id, "source_dataset_id", required=True,
        )
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _utc_now()
            connection.execute(
                """INSERT INTO character_weight_preference_seed(
                       character_id, source_dataset_id, source_kind,
                       seeded_at_utc, updated_at_utc
                   ) VALUES (?, ?, 'default', ?, ?)
                   ON CONFLICT(character_id) DO UPDATE SET
                       source_dataset_id = excluded.source_dataset_id,
                       source_kind = 'default',
                       seeded_at_utc = excluded.seeded_at_utc,
                       updated_at_utc = excluded.updated_at_utc""",
                (raw_character_id, dataset_id, now, now),
            )
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
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法重置角色词条权重") from exc
        except BaseException:
            connection.rollback()
            raise
        result = self.get_character_weight_preferences(raw_character_id)
        assert result is not None
        return result

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
                   is_active, likeability_level_10_enabled,
                   awakening_selection_initialized,
                   created_at_utc, updated_at_utc
            FROM character_profile WHERE character_id = ?
            """,
            (raw_character_id,),
        )
        if profile is None:
            return None
        profile["is_active"] = bool(profile["is_active"])
        profile["likeability_level_10_enabled"] = bool(
            profile["likeability_level_10_enabled"]
        )
        profile["awakening_selection_initialized"] = bool(
            profile["awakening_selection_initialized"]
        )
        profile["selected_awaken_effect_ids"] = [
            str(row["effect_id"])
            for row in self._rows(
                """
                SELECT effect_id FROM character_profile_awaken_effect
                WHERE character_id = ? ORDER BY ordinal
                """,
                (raw_character_id,),
            )
        ]
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

    def reset_character_profile(self, character_id: int) -> bool:
        """删除单个账号养成指针，使角色页回落到公共模板。

        仅操作 ``character_profile``；外键会清理对应的技能等级，额外形状
        公共配置、账号基础权重、库存及配装方案均不在此重置范围内。
        """

        raw_character_id = _integer(character_id, "character_id", minimum=1)
        connection = self._db()
        try:
            cursor = connection.execute(
                "DELETE FROM character_profile WHERE character_id = ?",
                (raw_character_id,),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法重置角色养成指针") from exc
        return bool(cursor.rowcount)

    def reset_all_character_profiles(self) -> int:
        """删除当前账号全部角色养成指针，保留额外形状与基础权重。"""

        connection = self._db()
        try:
            cursor = connection.execute("DELETE FROM character_profile")
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法重置全部角色养成指针") from exc
        return int(cursor.rowcount)

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
        likeability_level_10_enabled: bool = False,
        selected_awaken_effect_ids: Sequence[str] | None = None,
        awakening_selection_initialized: bool = False,
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
        normalized_awaken_effects: list[str] = []
        for effect_id in selected_awaken_effect_ids or ():
            normalized = self._preference_text(
                effect_id,
                "awaken_effect_id",
                required=True,
            )
            if normalized in normalized_awaken_effects:
                raise UserDataValidationError("selected_awaken_effect_ids 不能重复")
            normalized_awaken_effects.append(normalized)
        selection_initialized = bool(awakening_selection_initialized)
        if selection_initialized and len(normalized_awaken_effects) != raw_awakening:
            raise UserDataValidationError("觉醒等级必须等于已选择的普通觉醒数量")

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
                    is_active, likeability_level_10_enabled,
                    awakening_selection_initialized,
                    created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    likeability_level_10_enabled =
                        excluded.likeability_level_10_enabled,
                    awakening_selection_initialized =
                        excluded.awakening_selection_initialized,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    raw_character_id, raw_level, raw_breakthrough, raw_awakening,
                    raw_fork_id, raw_fork_level, raw_refinement,
                    raw_selected_skill, raw_ordinal, int(bool(is_active)),
                    int(bool(likeability_level_10_enabled)),
                    int(selection_initialized), now, now,
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
            connection.execute(
                "DELETE FROM character_profile_awaken_effect WHERE character_id = ?",
                (raw_character_id,),
            )
            connection.executemany(
                """
                INSERT INTO character_profile_awaken_effect(
                    character_id, effect_id, ordinal
                ) VALUES (?, ?, ?)
                """,
                [
                    (raw_character_id, effect_id, effect_ordinal)
                    for effect_ordinal, effect_id in enumerate(
                        normalized_awaken_effects
                    )
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
