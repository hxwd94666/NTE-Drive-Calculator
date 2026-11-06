# 队伍来源配置由同一账号事务校验成员、槽位及修订，失败或撤权保留旧配置。
from src.domain.teams.overview import SourceMode, TeamSourceConfig
from src.storage.sqlite.protocols import UserDataDaoMixinHost


class TeamOverviewDaoMixin(UserDataDaoMixinHost):
    def validate_team_sources(self):
        for team in self.list_teams():
            config = self.get_team_source(team.team_id).validated()
            if not {key for key, _ in config.bindings} <= set(team.character_ids):
                raise ValueError("队伍来源绑定含当前队伍外角色")
            for character_id, slot_id in config.bindings:
                slot = self.get_loadout_slot(slot_id)
                if slot is not None and slot["character_id"] != character_id:
                    raise ValueError("队伍来源槽位与账号角色归属不一致")

    def get_team_source(self, team_id):
        row = self._one("SELECT * FROM team_overview_source WHERE team_id=?", (team_id,))
        if row is None:
            raise ValueError("队伍来源已删除或不属于当前账号")
        bindings = self._rows("SELECT character_id, slot_id FROM team_overview_binding WHERE team_id=? ORDER BY character_id", (team_id,))
        return TeamSourceConfig(team_id, SourceMode(row["mode"]), row["revision"],
                                tuple((item["character_id"], item["slot_id"]) for item in bindings))

    def save_team_source(self, team, expected, *, mode, bindings=(), checkpoint=lambda: None):
        requested = TeamSourceConfig(team.team_id, mode, expected.revision, tuple(bindings)).validated()
        with self.write_transaction() as db:
            if team not in self.list_teams():
                raise ValueError("队伍已变化，请重新打开来源配置")
            current = self.get_team_source(team.team_id)
            if current != expected:
                raise ValueError("来源配置已变化，请刷新后重试")
            if not {key for key, _ in requested.bindings} <= set(team.character_ids):
                raise ValueError("槽位绑定包含队伍外成员")
            for character_id, slot_id in requested.bindings:
                slot = self.get_loadout_slot(slot_id)
                if slot is None or slot["is_archived"] or slot["character_id"] != character_id:
                    raise ValueError("所选槽位已失效或所属角色不符")
            if current.mode == requested.mode and current.bindings == requested.bindings:
                checkpoint()
                return current
            db.execute("UPDATE team_overview_source SET mode=?, revision=revision+1 WHERE team_id=?",
                       (requested.mode, team.team_id))
            db.execute("DELETE FROM team_overview_binding WHERE team_id=?", (team.team_id,))
            db.executemany("INSERT INTO team_overview_binding VALUES(?,?,?)",
                           [(team.team_id, key, slot) for key, slot in requested.bindings])
            checkpoint()
            return self.get_team_source(team.team_id)
