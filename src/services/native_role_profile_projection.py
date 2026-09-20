# 将稀疏原生养成叠加到角色模板或账号配置并明确逐字段来源。
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_support import UserDataValidationError, _valid_breakthrough_stage_for_level


def load_template_growth_defaults(
    character_ids: tuple[int, ...], *, static_database_path: str | Path,
) -> dict[int, dict[str, int]]:
    defaults = {}
    if not character_ids:
        return defaults
    with StaticGameDataDao(static_database_path) as dao:
        for character_id in character_ids:
            if dao.get_character(character_id) is None:
                raise UserDataValidationError("角色状态包含当前正式静态目录中不存在的角色")
            rows = dao.list_character_panel_growth(character_id)
            if not rows:
                raise UserDataValidationError("正式角色模板缺少成长数据，无法核对稀疏角色状态")
            growth = max(rows, key=lambda row: (int(row["level"]), int(row["breakthrough_stage"])))
            defaults[character_id] = {"character_level": int(growth["level"]), "breakthrough_stage": int(growth["breakthrough_stage"])}
    return defaults


def project_native_role_profile(
    base: Mapping[str, Any], observation: Mapping[str, Any] | None, *, persisted: bool,
) -> dict[str, Any]:
    profile = dict(base)
    sources = {name: "account" if persisted else "template" for name in base
               if name not in {"persisted", "field_sources", "created_at_utc", "updated_at_utc"}}
    for name in ("character_level", "breakthrough_stage"):
        value = (observation or {}).get(name)
        if value is not None:
            profile[name] = value
            sources[name] = "native_observed"
    observation = observation or {}
    if observation.get("awakening_level") is not None:
        profile["awakening_level"] = observation["awakening_level"]
        sources["awakening_level"] = "native_observed"
    if observation.get("awakening_selection_initialized") is True:
        profile["selected_awaken_effect_ids"] = list(observation["selected_awaken_effect_ids"])
        profile["awakening_selection_initialized"] = True
        sources["selected_awaken_effect_ids"] = "native_observed"
        sources["awakening_selection_initialized"] = "native_observed"
    for name in ("likeability_level_10_enabled", "fork_id", "fork_level", "fork_breakthrough_stage", "fork_refinement_level"):
        if name in observation and (not name.startswith("fork_") or observation.get("fork_observed") is True):
            profile[name] = observation[name]
            sources[name] = "native_observed"
    if observation.get("skill_levels"):
        profile["skill_levels"] = {**profile.get("skill_levels", {}), **observation["skill_levels"]}
        sources["skill_levels"] = "native_observed"
    if observation and not _valid_breakthrough_stage_for_level(profile["character_level"], profile["breakthrough_stage"]):
        raise UserDataValidationError("角色原生观测与当前模板的等级、突破组合不匹配")
    profile["field_sources"] = sources
    return profile


def validate_native_cultivation(patches, *, static_database_path):
    """Verify formal identities against the frozen shipped catalog before writes."""
    if not any(patch.get("skill_levels") or patch.get("fork_observed") or "likeability_levels" in patch
               or "likeability_level_10_enabled" in patch or patch.get("awakening_selection_initialized") for patch in patches):
        return
    with StaticGameDataDao(static_database_path) as dao:
        fork_ids = {row["fork_id"] for row in dao.list_forks()} if any(patch.get("fork_id") for patch in patches) else set()
        for patch in patches:
            validate_native_cultivation_patch(patch, dao, fork_ids)


def validate_native_cultivation_patch(patch, dao, fork_ids):
    """核对一个独立字段组；批量严格校验与角色稀疏同步共用此规则。"""
    resolve_native_likeability(patch, dao)
    if patch.get("awakening_selection_initialized"):
        effects = {row["effect_id"] for row in dao.list_character_awaken_effects(patch["character_id"])
                   if row["awaken_type"] == "Awaken_Effect"}
        if any(value not in effects for value in patch["selected_awaken_effect_ids"]):
            raise UserDataValidationError("原生觉醒效果不在当前角色的官方目录中")
    # Each static row is the cost of advancing from that level to the
    # next one, as in the role editor; the last attainable level is +1.
    skills = {row["skill_id"]: max((int(level["level"]) for level in row["levels"]), default=0) + 1
              for row in dao.list_character_skills(patch["character_id"])} if patch.get("skill_levels") else {}
    if any(key not in skills or not 1 <= level <= skills[key] for key, level in patch.get("skill_levels", {}).items()):
        raise UserDataValidationError("原生技能身份或基础等级不在当前官方目录中")
    fork_id = patch.get("fork_id")
    if patch.get("fork_observed") and fork_id is not None:
        if fork_id not in fork_ids:
            raise UserDataValidationError("原生弧盘身份不在当前官方目录中")
    if "likeability_level_10_enabled" in patch:
        bonus = dao.get_character_likeability_bonus(patch["character_id"])
        if bonus is None:
            patch["likeability_level_10_enabled"] = False
        elif int(bonus["required_level"]) != 10:
            raise UserDataValidationError("原生好感度门槛与当前官方目录不匹配")


def resolve_native_likeability(profile, static_dao):
    """Resolve the observed collection through the official source identity."""
    levels = profile.pop("likeability_levels", None)
    if levels is None:
        return
    identity = static_dao.get_character_likeability_identity(profile["character_id"])
    profile["likeability_level_10_enabled"] = bool(
        identity and levels.get(identity["likeability_id"], 0) >= int(identity["required_level"])
    )
