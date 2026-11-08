# 在同一账号事务内保存队伍及成员，修订冲突或成员写入失败均保留旧事实。
from uuid import uuid4

from src.domain.teams.models import Team, TeamDraft
from src.storage.sqlite.protocols import UserDataDaoMixinHost
from src.storage.sqlite.user_data_support import _utc_now


class TeamsDaoMixin(UserDataDaoMixinHost):
    def list_teams(self) -> tuple[Team, ...]:
        # 一个查询读取名称与成员，避免两次查询之间混入其他连接的修改。
        rows = self._rows("SELECT t.*, m.character_id FROM team t LEFT JOIN team_member m "
                          "ON m.team_id = t.team_id ORDER BY t.created_at_utc, t.team_id, m.ordinal")
        teams = {}
        for row in rows:
            entry = teams.setdefault(row["team_id"], [row["name"], row["revision"], []])
            if row["character_id"] is not None:
                entry[2].append(row["character_id"])
        return tuple(Team(key, name, tuple(ids), revision) for key, (name, revision, ids) in teams.items())

    def save_team(self, draft: TeamDraft, *, existing: Team | None = None) -> Team:
        draft = draft.validated()
        team_id = existing.team_id if existing else uuid4().hex
        now = _utc_now()
        with self.write_transaction() as db:
            if existing is None:
                revision = 1
                db.execute("INSERT INTO team VALUES (?, ?, ?, ?, ?)", (team_id, draft.name, revision, now, now))
                db.execute("INSERT INTO team_overview_source VALUES(?, 'slots', 1)", (team_id,))
            else:
                revision = existing.revision + 1
                cursor = db.execute("UPDATE team SET name=?, revision=?, updated_at_utc=? "
                                    "WHERE team_id=? AND revision=?",
                                    (draft.name, revision, now, team_id, existing.revision))
                if cursor.rowcount != 1:
                    raise ValueError("队伍已变更或已删除，请刷新后重试")
                db.execute("DELETE FROM team_member WHERE team_id=?", (team_id,))
            db.executemany("INSERT INTO team_member VALUES (?, ?, ?)",
                           [(team_id, index, character_id) for index, character_id in enumerate(draft.character_ids)])
            # 改名或重排不改来源；移除成员仅清除其绑定，不按同名角色／槽位补齐。
            removed = db.execute("DELETE FROM team_overview_binding WHERE team_id=? AND character_id NOT IN "
                                 "(SELECT character_id FROM team_member WHERE team_id=?)", (team_id, team_id))
            if removed.rowcount:
                db.execute("UPDATE team_overview_source SET revision=revision+1 WHERE team_id=?", (team_id,))
        return Team(team_id, draft.name, draft.character_ids, revision)

    def delete_team(self, team: Team) -> None:
        with self.write_transaction() as db:
            cursor = db.execute("DELETE FROM team WHERE team_id=? AND revision=?", (team.team_id, team.revision))
            if cursor.rowcount != 1:
                raise ValueError("队伍已变更或已删除，请刷新后重试")
            # 只回收没有其他队伍结果引用的轻量作业，不影响方案、战报或联合另一队。
            db.execute("DELETE FROM team_enhancement_run WHERE NOT EXISTS "
                       "(SELECT 1 FROM team_enhancement_result r WHERE r.run_id=team_enhancement_run.run_id)")
