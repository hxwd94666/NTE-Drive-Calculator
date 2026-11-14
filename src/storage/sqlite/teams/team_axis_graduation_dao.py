# 账号目标只追加不可变版本；事务内复核队伍和目标修订，失败/取消不留下新版本。
import json

from src.domain.teams.graduation.axis_graduation_target import GraduationTarget, decode_graduation_target
from src.domain.teams.models import Team
from src.storage.sqlite.protocols import UserDataDaoMixinHost
from src.storage.sqlite.user_data_support import _utc_now


def _page(offset, limit):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("目标历史分页范围无效")


class TeamAxisGraduationDaoMixin(UserDataDaoMixinHost):
    @staticmethod
    def _axis_target(row):
        if row is None:
            return None
        target = decode_graduation_target(row["payload_json"])
        if (row["format_version"] != 1
                or (target.target_id, target.team_id, target.revision, target.sha256)
                != (row["target_id"], row["team_id"], row["revision"], row["target_sha256"])):
            raise ValueError("保存的毕业目标正文或身份已变化")
        return target

    def get_team_axis_target(self, team_id, *, revision=None):
        """不指定修订只取最新目标版本，不选择或重解释任何理论基准。"""
        if revision is not None and (type(revision) is not int or revision <= 0):
            raise ValueError("目标修订无效")
        if revision is None:
            row = self._one("SELECT * FROM team_axis_graduation_target WHERE team_id=? ORDER BY revision DESC LIMIT 1", (team_id,))
        else:
            row = self._one("SELECT * FROM team_axis_graduation_target WHERE team_id=? AND revision=?", (team_id, revision))
        return self._axis_target(row)

    def list_team_axis_targets(self, team_id, *, offset=0, limit=20):
        _page(offset, limit)
        rows = self._rows("SELECT * FROM team_axis_graduation_target WHERE team_id=? ORDER BY revision DESC LIMIT ? OFFSET ?",
                          (team_id, limit, offset))
        return tuple(self._axis_target(row) for row in rows)

    def save_team_axis_target(self, team, target, *, expected_revision, checkpoint=lambda: None):
        if not isinstance(team, Team) or not isinstance(target, GraduationTarget):
            raise ValueError("目标保存需要正式队伍和规范目标")
        if type(expected_revision) is not int or expected_revision < 0 or target.revision != expected_revision + 1:
            raise ValueError("目标新修订必须紧随已确认版本")
        if target.team_id != team.team_id or target.character_ids != team.character_ids:
            raise ValueError("目标成员与当前队伍不一致")
        payload = json.dumps(target.to_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        # 写入/读取都走同一typed契约；该校验只证明正文规范，不伪装为轴可执行性或静态模型完备。
        if decode_graduation_target(payload) != target:
            raise ValueError("目标规范化结果不一致")
        checkpoint()
        with self.write_transaction() as db:
            if team not in self.list_teams():
                raise ValueError("队伍已变化，请刷新后保存目标")
            previous = self.get_team_axis_target(team.team_id)
            if (previous.revision if previous else 0) != expected_revision:
                raise ValueError("毕业目标已被更新，请刷新后重试")
            if previous is not None and previous.target_id != target.target_id:
                raise ValueError("同队伍目标修订必须保留稳定目标身份")
            db.execute("INSERT INTO team_axis_graduation_target(target_id,team_id,revision,format_version,target_sha256,payload_json,created_at_utc) "
                       "VALUES(?,?,?,1,?,?,?)", (target.target_id, target.team_id, target.revision, target.sha256, payload, _utc_now()))
            # 取消/账号撤权在提交前再次复核；外层业务事务存在时只释放保存点，不提前提交。
            checkpoint()
        return target

    def validate_team_axis_targets(self):
        """导入/导出逐行校验内容身份和连续修订，不一次载入全部目标正文。"""
        current_team = None
        previous_id, previous_revision = None, 0
        for row in self._db().execute("SELECT * FROM team_axis_graduation_target ORDER BY team_id, revision"):
            target = self._axis_target(dict(row))
            if target.team_id != current_team:
                current_team, previous_id, previous_revision = target.team_id, target.target_id, 0
            if target.target_id != previous_id or target.revision != previous_revision + 1:
                raise ValueError("账号目标历史存在身份冲突或修订缺口")
            previous_revision = target.revision
