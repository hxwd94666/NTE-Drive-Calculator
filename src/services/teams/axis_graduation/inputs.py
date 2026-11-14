# 在当前账号一致读中冻结实际养成和所选来源，不读取个人毕业模板、不以满级默认值补未知。
from concurrent.futures import CancelledError
from contextlib import contextmanager
import json
from math import isfinite
from pathlib import Path

from src.domain.official_role.likeability import likeability_unavailable
from src.domain.official_role.models import WORLD_BONUS_SETTING_KEY, WorldBonusSettings
from src.domain.teams.graduation.axis_graduation_inputs import (
    PROFILE_FIELDS,
    WORLD_BONUS_FIELDS,
    CurrentBuild,
    CurrentMemberBuild,
)
from src.integrations.battle_analysis.battle_axis_evaluation import validate_axis_equipment
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.battle_report.analysis.axis_evaluation import freeze_actual_axis_equipment
from src.services.official_role.profile_data.native_profiles import project_native_role_profile
from src.services.teams.source_inputs import freeze_team_sources
from src.storage.sqlite.user_data_dao import UserDataDao


REQUIRED_PROFILE_FIELDS = (
    "character_level", "breakthrough_stage", "awakening_level",
    "likeability_level_10_enabled", "fork_id", "skill_levels",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _profile_fields(value):
    # DAO 的时间、排序、激活及显示状态不进入伤害内容身份，也不把整份账号行发给组件。
    return {key: item for key, item in (value or {}).items() if key in PROFILE_FIELDS - {"field_sources", "skill_level_sources"}}


def _profile_inputs(dao, static, character_id):
    account = _profile_fields(dao.get_character_profile(character_id))
    native = _profile_fields(dao.get_native_character_profile_observation(character_id))
    growth = {key: native.get(key) if native.get(key) is not None else account.get(key)
              for key in ("character_level", "breakthrough_stage")}
    effective = None
    if all(value is not None for value in growth.values()):
        # 只传实际已知字段；稀疏观测的公共覆盖规则仍由角色公共投影拥有。
        effective = project_native_role_profile(account, native, persisted=bool(account))
        skills = effective.get("skill_levels") or {}
        if skills:
            effective["skill_level_sources"] = {
                key: "native_observed" if key in (native.get("skill_levels") or {}) else "account" for key in skills
            }
            if len(set(effective["skill_level_sources"].values())) > 1:
                effective["field_sources"]["skill_levels"] = "mixed"
    known = dict(account)
    for key, value in native.items():
        if key.startswith("fork_") and native.get("fork_observed") is not True:
            continue
        if value is not None or key == "fork_id" and native.get("fork_observed") is True:
            known[key] = value
    if effective is not None:
        known = effective
    missing = [key for key in REQUIRED_PROFILE_FIELDS
               if key not in known or key != "fork_id" and known[key] is None]
    if not known.get("skill_levels") and "skill_levels" not in missing:
        missing.append("skill_levels")
    if known.get("fork_id") is not None:
        missing += [key for key in ("fork_level", "fork_breakthrough_stage", "fork_refinement_level")
                    if known.get(key) is None]
    gaps = []
    if known.get("awakening_level", 0) > 0 and known.get("awakening_selection_initialized") is not True:
        missing.append("selected_awaken_effect_ids")
    character = static.get_character(character_id)
    if character is None:
        gaps.append("character_model_missing")
    else:
        if effective is not None and not any(
            row["level"] == growth["character_level"] and row["breakthrough_stage"] == growth["breakthrough_stage"]
            for row in static.list_character_panel_growth(character_id)
        ):
            gaps.append("growth_model_missing")
        skills = {row["skill_id"] for row in static.list_character_skills(character_id)}
        if set(known.get("skill_levels") or {}) - skills:
            gaps.append("skill_identity_unknown")
        if known.get("likeability_level_10_enabled") is True and likeability_unavailable(
            static.get_character_likeability_bonus(character_id)
        ):
            gaps.append("likeability_model_missing")
    return {"account": account, "native": native, "effective": effective}, tuple(missing), gaps


def _world_inputs(dao):
    raw = dao.list_application_setting_copies().get(WORLD_BONUS_SETTING_KEY)
    if raw is None:
        return None, ("world_bonus_unconfirmed",)
    if not isinstance(raw, dict):
        return None, ("world_bonus_invalid",)
    value = {key: raw[key] for key in WORLD_BONUS_FIELDS if key in raw}
    if {"yaodao_attack_add", "quantao_crit_damage"} <= value.keys():
        # 旧账号已显式确认两项；新增防御家具按产品默认满值，不改旧冻结正文。
        value.setdefault("kaijia_defense_add", 30.0)
    if not value:
        return None, ("world_bonus_unconfirmed",)
    try:
        if any(type(number) not in (int, float) or not isfinite(number) or number < 0 for number in value.values()):
            return None, ("world_bonus_invalid",)
        if set(value) != WORLD_BONUS_FIELDS:
            return value, ("world_bonus_unconfirmed",)
        # 保留旧字段的完整性校验；只有本轮新增的防御允许使用明确的满值默认。
        WorldBonusSettings(**value).validate()
    except (ValueError, TypeError, OverflowError):
        return None, ("world_bonus_invalid",)
    return value, ()


class TeamAxisCurrentInputService:
    def __init__(self, *, account_id, generation, database_path, static_database_path, context_is_current):
        self.account_id, self.generation = account_id, generation
        self.database_path = Path(database_path)
        self.static_database_path = static_database_path
        self._current = context_is_current

    def checkpoint(self, cancelled=lambda: False):
        if cancelled() or not self._current():
            raise CancelledError("当前构筑读取已取消或账号代次已变化")

    @contextmanager
    def _database(self, cancelled):
        self.checkpoint(cancelled)
        if not self.database_path.is_file():
            raise ValueError("当前账号库不存在，不创建替代数据")
        with UserDataDao(self.database_path) as dao:
            with dao.read_consistent_state():
                if dao.profile()["account_id"] != self.account_id:
                    raise CancelledError("当前构筑与账号身份不一致")
                yield dao
                self.checkpoint(cancelled)

    def freeze_current(self, team, *, cancelled=lambda: False):
        check = lambda: self.checkpoint(cancelled)
        check()
        if self.static_database_path is None:
            raise ValueError("当前轴伤输入缺少完整主静态身份")
        with static_catalog_read_unit(self.static_database_path) as unit:
            if unit.release.scope != "game":
                raise ValueError("当前轴伤输入不使用独立图鉴替代主静态")
            with self._database(cancelled) as dao:
                if team not in dao.list_teams():
                    raise ValueError("队伍已变化，请重新冻结当前构筑")
                overview, = freeze_team_sources(dao, unit.dao, (team,), checkpoint=check)
                members = []
                for member in overview.members:
                    check()
                    evidence, missing, gaps = _profile_inputs(dao, unit.dao, member.character_id)
                    try:
                        equipment = freeze_actual_axis_equipment({"items": member.items}, checkpoint=check)
                        if equipment:
                            validate_axis_equipment(equipment, complete=False)
                            for item in equipment:
                                item.pop("names", None)
                                item.pop("suit_names", None)
                        else:
                            gaps.append("equipment_unconfirmed")
                    except (ValueError, NativeAnalysisError):
                        equipment = []
                        gaps.append("equipment_values_invalid")
                    members.append(CurrentMemberBuild(
                        member.character_id, member.slot_id, member.plan_id, member.snapshot_id, member.fingerprint,
                        _json(evidence), _json(equipment), missing, tuple(gaps), member.issues,
                    ))
                world, gaps = _world_inputs(dao)
                result = CurrentBuild(
                    self.account_id, self.generation, team, overview.source,
                    unit.release.dataset_id, unit.release.schema_version, unit.release.sha256.lower(),
                    tuple(members), _json(world), gaps, overview.issues,
                )
            check()
        check()
        return result

    def verify_current(self, frozen, *, cancelled=lambda: False):
        self.checkpoint(cancelled)
        if (not isinstance(frozen, CurrentBuild)
                or (frozen.account_id, frozen.generation) != (self.account_id, self.generation)):
            raise CancelledError("当前构筑不属于本账号代次")
        current = self.freeze_current(frozen.team, cancelled=cancelled)
        if current.sha256 != frozen.sha256:
            raise ValueError("当前构筑或来源已变化，请重新评价；已保存理论基准不自动改写")
