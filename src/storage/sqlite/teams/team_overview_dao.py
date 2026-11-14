# 队伍来源配置由同一账号事务校验成员、槽位及修订，失败或撤权保留旧配置。
from src.domain.teams.overview import SourceMode, TeamSourceConfig
from src.domain.teams.source_selection import with_single_slot_defaults
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
        config = TeamSourceConfig(team_id, SourceMode(row["mode"]), row["revision"],
                                  tuple((item["character_id"], item["slot_id"]) for item in bindings))
        if config.mode == SourceMode.GAME:
            return config
        # 同账号、同队成员的可见槽位；默认值属于读取投影，不创建绑定或推进修订。
        slots = self._rows("SELECT s.* FROM role_loadout_slot s JOIN team_member m "
                           "ON m.character_id=s.character_id WHERE m.team_id=? AND s.is_archived=0", (team_id,))
        return with_single_slot_defaults(config, slots)

    def save_team_source(self, team, expected, *, mode, bindings=(), checkpoint=lambda: None):
        requested = TeamSourceConfig(team.team_id, mode, expected.revision, tuple(bindings)).validated()
        explicit_bindings = requested.bindings
        with self.write_transaction() as db:
            if team not in self.list_teams():
                raise ValueError("队伍已变化，请重新打开来源配置")
            current = self.get_team_source(team.team_id)
            if current != expected:
                raise ValueError("来源配置已变化，请刷新后重试")
            if not {key for key, _ in requested.bindings} <= set(team.character_ids):
                raise ValueError("槽位绑定包含队伍外成员")
            requested = with_single_slot_defaults(requested, (
                slot for slot in self.list_visible_loadout_slots()
                if slot["character_id"] in team.character_ids))
            for character_id, slot_id in requested.bindings:
                slot = self.get_loadout_slot(slot_id)
                if slot is None or slot["is_archived"] or slot["character_id"] != character_id:
                    raise ValueError("所选槽位已失效或所属角色不符")
            if current.mode == requested.mode and current.bindings == requested.bindings:
                stored = tuple((item["character_id"], item["slot_id"]) for item in self._rows(
                    "SELECT character_id, slot_id FROM team_overview_binding WHERE team_id=? ORDER BY character_id",
                    (team.team_id,)))
                # 显式保存绑定必须保留稳定身份；隐式默认的读取与空草稿不补写数据库。
                if not explicit_bindings or stored == requested.bindings:
                    checkpoint()
                    return current
            db.execute("UPDATE team_overview_source SET mode=?, revision=revision+1 WHERE team_id=?",
                       (requested.mode, team.team_id))
            db.execute("DELETE FROM team_overview_binding WHERE team_id=?", (team.team_id,))
            db.executemany("INSERT INTO team_overview_binding VALUES(?,?,?)",
                           [(team.team_id, key, slot) for key, slot in requested.bindings])
            checkpoint()
            return self.get_team_source(team.team_id)
