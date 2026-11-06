# 编排账号队伍管理，所有提交复核冻结账号和代次，角色目录只提供显示与身份校验。
from contextlib import contextmanager
from pathlib import Path

from src.domain.teams.models import Team, TeamDraft, TeamRole
from src.services.official_role.page import load_official_role_index
from src.storage.sqlite.user_data_dao import UserDataDao


class TeamSessionExpired(RuntimeError):
    pass


class TeamService:
    def __init__(self, *, account_id, database_path, static_database_path, asset_root,
                 context_is_current, role_loader=None):
        self.account_id = account_id
        self.database_path = Path(database_path)
        self.static_database_path = static_database_path
        self.asset_root = asset_root
        self._is_current = context_is_current
        self._role_loader = role_loader

    def checkpoint(self):
        if not self._is_current():
            raise TeamSessionExpired("账号已切换，请重新打开队伍")

    @contextmanager
    def _database(self, *, write=False):
        self.checkpoint()
        with UserDataDao(self.database_path) as dao:
            with dao.write_transaction() if write else dao.read_consistent_state():
                if dao.profile()["account_id"] != self.account_id:
                    raise TeamSessionExpired("队伍与当前账号不一致")
                yield dao
                # 包括提交前代次复核；异常使本次外层事务整体回滚。
                self.checkpoint()

    def list_teams(self):
        with self._database() as dao:
            return dao.list_teams()

    def roles(self) -> tuple[TeamRole, ...]:
        self.checkpoint()
        if self._role_loader:
            result = tuple(self._role_loader())
        else:
            rows = load_official_role_index(self.database_path, static_database_path=self.static_database_path,
                                            asset_root=self.asset_root)
            result = tuple(TeamRole(int(row["character_id"]), str(row["name_zh"]),
                                    str(row.get("icon_path") or ""), icon_note=str(row.get('icon_note') or '')) for row in rows)
            with self._database() as dao:
                known = {role.character_id for role in result}
                result += tuple(TeamRole(int(row["character_id"]), row["name_zh"], custom=True)
                                for row in dao.list_custom_characters() if row["character_id"] not in known)
        self.checkpoint()
        return result

    def save(self, draft: TeamDraft, *, existing: Team | None = None):
        draft = draft.validated()
        allowed = {role.character_id for role in self.roles()}
        with self._database(write=True) as dao:
            # 历史成员暂时不在目录时仍保留身份，允许改名或主动移除，不静默丢失。
            previous = next((row for row in dao.list_teams() if existing and row.team_id == existing.team_id), None)
            if previous:
                allowed.update(previous.character_ids)
            if not set(draft.character_ids) <= allowed:
                raise ValueError("所选角色已不在当前目录，请重新选择")
            return dao.save_team(draft, existing=existing)

    def delete(self, team: Team):
        with self._database(write=True) as dao:
            dao.delete_team(team)
