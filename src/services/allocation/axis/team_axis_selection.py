# 冻结最多两支互斥队伍及各自战报选择，队伍名仅展示，修订和正式成员身份参与复核。
from dataclasses import dataclass, replace

from src.domain.teams.models import Team
from src.services.allocation.axis.axis_selection import AxisAllocationSelection
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True)
class TeamAxisBinding:
    team: Team
    roles: tuple[str, ...]
    axis: AxisAllocationSelection


@dataclass(frozen=True)
class TeamAxisSelection:
    teams: tuple[TeamAxisBinding, ...]
    search_mode: str = "fast"

    @property
    def roles(self):
        return tuple(role for team in self.teams for role in team.roles)


def validate_team_group(teams):
    if not 1 <= len(teams) <= 2:
        raise ValueError("请选择一支或两支队伍")
    if len({team.team_id for team in teams}) != len(teams):
        raise ValueError("不能重复选择同一支队伍")
    used = set()
    for team in teams:
        if len(team.character_ids) != 4 or len(set(team.character_ids)) != 4:
            raise ValueError(f"请先在队伍菜单补齐“{team.name}”的四名成员")  # ui-text: internal
        if used.intersection(team.character_ids):
            raise ValueError("两支队伍包含重复角色，请选择人物互斥的队伍")
        used.update(team.character_ids)


def load_team_choices(service, role_names):
    choices = service.choices(role_names)
    with UserDataDao(service.dependencies.user_database_path) as dao:
        if dao.profile()["account_id"] != service.dependencies.account_id:
            raise ValueError("账号已切换，请重新配置战报增强")
        teams = dao.list_teams()
    service.checkpoint()
    return teams, choices


def select_team_axes(service, choices, configured):
    """configured 为 (队伍, 战报 ID, 半场, 原始/编辑配置)，不隐式选取最新战报。"""
    validate_team_group([row[0] for row in configured])
    by_id = {}
    for role, character_id in choices.role_character_ids:
        by_id.setdefault(character_id, []).append(role)
    bindings = []
    for team, record_id, scope, anchor, *bounds in configured:
        if any(len(by_id.get(key, ())) != 1 for key in team.character_ids):
            raise ValueError(f"“{team.name}”有成员未在当前计算角色目录中唯一匹配，请检查角色配置")  # ui-text: internal
        roles = tuple(by_id[key][0] for key in team.character_ids)
        local_choices = replace(choices, selected_roles=roles)
        axis = service.select(local_choices, battle_record_id=record_id, selected_role="__all_groups__",
                              detail_scope=scope, anchor_mode=anchor,
                              start_us=bounds[0] if scope == "range" and bounds else None,
                              end_us=bounds[1] if scope == "range" and len(bounds) > 1 else None)
        bindings.append(TeamAxisBinding(team, roles, axis))
    selection = TeamAxisSelection(tuple(bindings))
    verify_team_selection(selection, service.dependencies.user_database_path)
    service.checkpoint()
    return selection


def verify_team_selection(selection, database_path):
    validate_team_group([binding.team for binding in selection.teams])
    with UserDataDao(database_path) as dao:
        current = {team.team_id: team for team in dao.list_teams()}
        for binding in selection.teams:
            if (dao.profile()["account_id"] != binding.axis.account_id
                    or binding.axis.user_database_path.resolve() != dao.database_path.resolve()
                    or current.get(binding.team.team_id) != binding.team):
                raise ValueError("队伍或账号已变更，请重新配置战报增强")
