# 在单一事务中稀疏更新原生角色养成，保留未观测字段与已有子表。
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import sqlite3
import json
from typing import Any

from .protocols import UserDataDaoMixinHost
from .user_data_support import UserDataError, UserDataValidationError, _utc_now, _valid_breakthrough_stage_for_level


GROWTH_FIELDS = ("character_level", "breakthrough_stage")


def normalize_native_profile_patches(profiles: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    if isinstance(profiles, (str, bytes)) or not isinstance(profiles, Sequence):
        raise UserDataValidationError("正式角色状态必须是角色列表")
    patches = []
    seen = set()
    for row in profiles:
        if not isinstance(row, Mapping):
            raise UserDataValidationError("正式角色状态条目无效")
        character_id = row.get("character_id")
        if type(character_id) is not int or character_id <= 0 or character_id in seen:
            raise UserDataValidationError("角色状态必须使用唯一的正式 character_id")
        seen.add(character_id)
        patch = {"character_id": character_id}
        for name, low, high in (("character_level", 1, 80), ("breakthrough_stage", 0, 6)):
            value = row.get(name)
            if value is None:
                continue
            if type(value) is not int or not low <= value <= high:
                raise UserDataValidationError(f"角色状态 {name} 超出合法范围")
            patch[name] = value
        if "likeability_levels" in row:
            levels = row["likeability_levels"]
            if not isinstance(levels, Mapping) or len(levels) > 512 or any(
                not isinstance(key, str) or not key or len(key) > 128
                or type(value) is not int or not 0 <= value <= 100 for key, value in levels.items()
            ):
                raise UserDataValidationError("原生好感度等级集合无效")
            patch["likeability_levels"] = dict(levels)
        skills = row.get("skill_levels")
        if skills is not None:
            if not isinstance(skills, Mapping) or len(skills) > 64 or any(
                not isinstance(key, str) or not key or len(key) > 128
                or type(value) is not int or not 1 <= value <= 15 for key, value in skills.items()
            ):
                raise UserDataValidationError("原生技能等级无效")
            if skills:
                patch["skill_levels"] = dict(skills)
        likeability = row.get("likeability_level_10_enabled")
        if likeability is not None:
            if type(likeability) is not bool:
                raise UserDataValidationError("原生好感度状态必须是布尔值")
            patch["likeability_level_10_enabled"] = likeability
        if row.get("fork_observed") is True:
            if "fork_id" not in row:
                raise UserDataValidationError("原生弧盘缺少身份")
            fork_id = row["fork_id"]
            if fork_id is not None and (not isinstance(fork_id, str) or not fork_id or len(fork_id) > 128):
                raise UserDataValidationError("原生弧盘身份无效")
            patch.update(fork_observed=True, fork_id=fork_id)
            if fork_id is None:
                patch.update(fork_level=None, fork_breakthrough_stage=None, fork_refinement_level=None)
            else:
                for name, high in (("fork_level", 80), ("fork_breakthrough_stage", 6), ("fork_refinement_level", 5)):
                    value = row.get(name)
                    if type(value) is not int or not (0 if name == "fork_breakthrough_stage" else 1) <= value <= high:
                        raise UserDataValidationError("原生弧盘养成不完整或无效")
                    patch[name] = value
                if not _valid_breakthrough_stage_for_level(patch["fork_level"], patch["fork_breakthrough_stage"]):
                    raise UserDataValidationError("原生弧盘等级与突破不匹配")
        if len(patch) > 1:
            patches.append(patch)
    return tuple(patches)


class NativeCharacterProfileDaoMixin(UserDataDaoMixinHost):
    @staticmethod
    def _decode_native_observation(row):
        if row is None:
            return None
        row = dict(row)
        cultivation = json.loads(row.pop("cultivation_json", "{}"))
        return {**row, **cultivation}

    def list_native_character_profile_observations(self) -> list[dict[str, Any]]:
        return [self._decode_native_observation(row) for row in self._rows(
            "SELECT character_id, character_level, breakthrough_stage, cultivation_json, updated_at_utc "
            "FROM character_profile_observation ORDER BY character_id"
        )]

    def get_native_character_profile_observation(self, character_id: int) -> dict[str, Any] | None:
        return self._decode_native_observation(self._one(
            "SELECT character_id, character_level, breakthrough_stage, cultivation_json, updated_at_utc "
            "FROM character_profile_observation WHERE character_id = ?", (character_id,),
        ))

    def patch_native_character_profiles(
        self, profiles: Sequence[Mapping[str, Any]], *,
        growth_defaults: Mapping[int, Mapping[str, int]], check: Callable[[], None],
    ) -> int:
        patches = normalize_native_profile_patches(profiles)
        if any("likeability_levels" in patch for patch in patches):
            raise UserDataValidationError("好感度尚未通过正式角色目录关联")
        check()
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for patch in patches:
                check()
                character_id = patch["character_id"]
                existing = self._one(
                    "SELECT character_level, breakthrough_stage FROM character_profile WHERE character_id = ?",
                    (character_id,),
                )
                observation = self.get_native_character_profile_observation(character_id) or {}
                baseline = dict(growth_defaults[character_id])
                if existing:
                    baseline.update(existing)
                baseline.update({name: observation[name] for name in GROWTH_FIELDS if observation.get(name) is not None})
                baseline.update({name: patch[name] for name in GROWTH_FIELDS if name in patch})
                level, stage = baseline.get("character_level"), baseline.get("breakthrough_stage")
                if type(level) is not int or type(stage) is not int or not _valid_breakthrough_stage_for_level(level, stage):
                    raise UserDataValidationError("角色等级与突破阶段的最终组合不匹配；未知阶段不会根据等级推测")
                now = _utc_now()
                cultivation = {key: observation[key] for key in (
                    "skill_levels", "likeability_level_10_enabled", "fork_observed", "fork_id",
                    "fork_level", "fork_breakthrough_stage", "fork_refinement_level",
                ) if key in observation}
                for key, value in patch.items():
                    if key in GROWTH_FIELDS or key == "character_id":
                        continue
                    cultivation[key] = ({**cultivation.get(key, {}), **value} if key == "skill_levels" else value)
                if cultivation.get("fork_observed") and cultivation.get("fork_id") is None:
                    for key in ("fork_level", "fork_breakthrough_stage", "fork_refinement_level"):
                        cultivation[key] = None
                connection.execute(
                    """INSERT INTO character_profile_observation(
                           character_id, character_level, breakthrough_stage, updated_at_utc, cultivation_json
                       ) VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(character_id) DO UPDATE SET
                           character_level = COALESCE(excluded.character_level, character_profile_observation.character_level),
                           breakthrough_stage = COALESCE(excluded.breakthrough_stage, character_profile_observation.breakthrough_stage),
                           updated_at_utc = excluded.updated_at_utc, cultivation_json = excluded.cultivation_json""",
                    (character_id, patch.get("character_level"), patch.get("breakthrough_stage"), now,
                     json.dumps(cultivation, ensure_ascii=False)),
                )
                if existing:
                    connection.execute(
                        """UPDATE character_profile SET character_level = COALESCE(?, character_level),
                               breakthrough_stage = COALESCE(?, breakthrough_stage), updated_at_utc = ?
                           WHERE character_id = ?""",
                        (patch.get("character_level"), patch.get("breakthrough_stage"), now, character_id),
                    )
                    keys = [key for key in ("likeability_level_10_enabled", "fork_id", "fork_level", "fork_breakthrough_stage", "fork_refinement_level") if key in patch]
                    if keys:
                        connection.execute("UPDATE character_profile SET " + ", ".join(key + " = ?" for key in keys)
                                           + " WHERE character_id = ?", (*[patch[key] for key in keys], character_id))
                    for skill_id, level in patch.get("skill_levels", {}).items():
                        connection.execute(
                            "INSERT INTO character_profile_skill(character_id, skill_id, skill_level) VALUES (?, ?, ?) "
                            "ON CONFLICT(character_id, skill_id) DO UPDATE SET skill_level = excluded.skill_level",
                            (character_id, skill_id, level),
                        )
            check()
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise UserDataError("无法保存原生角色状态") from exc
        except BaseException:
            connection.rollback()
            raise
        return len(patches)
