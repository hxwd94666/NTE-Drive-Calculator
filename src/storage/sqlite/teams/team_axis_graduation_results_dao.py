# 在同一账号事务保存目标、基准、首评价和显式选择；历史不引用可删除的战报或库存行。
from src.domain.teams.graduation.axis_graduation_record import SavedAxisResult, SavedAxisSelection
from src.domain.teams.models import Team
from src.storage.sqlite.protocols import UserDataDaoMixinHost
from src.storage.sqlite.teams.team_axis_graduation_dao import _page
from src.storage.sqlite.user_data_support import _utc_now


def _revision(value):
    if type(value) is not int or value < 0:
        raise ValueError("轴伤保存的预期修订无效")


class TeamAxisGraduationResultsDaoMixin(UserDataDaoMixinHost):
    def _axis_result(self, row):
        if row is None:
            return None
        result = SavedAxisResult(row["payload_json"])
        if (result.sha256 != row["content_sha256"] or result.benchmark_id != row["benchmark_id"]
                or result.target.team_id != row["team_id"] or result.account_id != self.profile()["account_id"]):
            raise ValueError("轴伤历史内容摘要或账号关联已变化")
        if "evaluation_id" in row:
            if result.evaluation_id != row["evaluation_id"] or result.operation != row["operation"]:
                raise ValueError("轴伤评价身份或操作不一致")
        elif result.operation != "theoretical_benchmark" or (result.target.target_id, result.target.revision) != (
                row["target_id"], row["target_revision"]):
            raise ValueError("轴伤基准关联目标不一致")
        saved_target = self.get_team_axis_target(result.target.team_id, revision=result.target.revision)
        if result.target != saved_target:
            raise ValueError("轴伤历史引用的目标正文不一致")
        return result

    def get_team_axis_benchmark(self, team_id, benchmark_id):
        return self._axis_result(self._one("SELECT * FROM team_axis_graduation_benchmark WHERE team_id=? AND benchmark_id=?",
                                          (team_id, benchmark_id)))

    def get_team_axis_evaluation(self, team_id, evaluation_id):
        row = self._one("SELECT e.evaluation_id,e.benchmark_id,e.team_id,e.operation,e.content_sha256,"
                        "COALESCE(e.payload_json,b.payload_json) AS payload_json "
                        "FROM team_axis_graduation_evaluation e JOIN team_axis_graduation_benchmark b USING(benchmark_id,team_id) "
                        "WHERE e.team_id=? AND e.evaluation_id=?", (team_id, evaluation_id))
        result = self._axis_result(row)
        if result is not None:
            benchmark = self.get_team_axis_benchmark(team_id, result.benchmark_id)
            self._same_benchmark(benchmark, result)
        return result

    @staticmethod
    def _same_benchmark(benchmark, result):
        if benchmark is None:
            raise ValueError("当前评价引用的基准尚未保存")
        one, two = benchmark.to_payload(), result.to_payload()
        if (benchmark.reference != result.reference
                or one["response"]["axis_snapshot"] != two["response"]["axis_snapshot"]
                or one["request"]["target"] != two["request"]["target"]
                or one["request"]["rules"] != two["request"]["rules"]):
            raise ValueError("当前评价不可替换原分母、冻结轴或规则")

    def list_team_axis_benchmarks(self, team_id, *, offset=0, limit=20):
        _page(offset, limit)
        return tuple(self._axis_result(row) for row in self._rows(
            "SELECT * FROM team_axis_graduation_benchmark WHERE team_id=? ORDER BY created_at_utc DESC,rowid DESC LIMIT ? OFFSET ?",
            (team_id, limit, offset)))

    def list_team_axis_evaluations(self, team_id, benchmark_id, *, offset=0, limit=20):
        _page(offset, limit)
        rows = self._rows("SELECT evaluation_id FROM team_axis_graduation_evaluation WHERE team_id=? AND benchmark_id=? "
                          "ORDER BY created_at_utc DESC,rowid DESC LIMIT ? OFFSET ?", (team_id, benchmark_id, limit, offset))
        return tuple(self.get_team_axis_evaluation(team_id, row["evaluation_id"]) for row in rows)

    def get_team_axis_selection(self, team_id):
        row = self._one("SELECT * FROM team_axis_graduation_selection WHERE team_id=?", (team_id,))
        return SavedAxisSelection(**row) if row else None

    def _axis_selection_guard(self, team, expected_revision):
        _revision(expected_revision)
        if not isinstance(team, Team) or team not in self.list_teams():
            raise ValueError("队伍已变化，请刷新后保存轴伤结果")
        selected = self.get_team_axis_selection(team.team_id)
        if (selected.revision if selected else 0) != expected_revision:
            raise ValueError("选用基准已被更新，请刷新后重试")

    def _axis_select(self, team_id, result, revision):
        self._db().execute("INSERT INTO team_axis_graduation_selection(team_id,benchmark_id,evaluation_id,revision) VALUES(?,?,?,?) "
                           "ON CONFLICT(team_id) DO UPDATE SET benchmark_id=excluded.benchmark_id,evaluation_id=excluded.evaluation_id,revision=excluded.revision",
                           (team_id, result.benchmark_id, result.evaluation_id, revision))

    def save_team_axis_result(self, team, result, *, expected_target_revision, expected_selection_revision,
                              select=True, checkpoint=lambda: None):
        if not isinstance(result, SavedAxisResult) or type(select) is not bool:
            raise ValueError("保存轴伤结果需要规范正文和明确选择意图")
        target = result.target
        _revision(expected_target_revision)
        if (not isinstance(team, Team) or target.team_id != team.team_id or target.character_ids != team.character_ids
                or result.account_id != self.profile()["account_id"]):
            raise ValueError("保存结果不属于本账号队伍")
        checkpoint()
        with self.write_transaction() as db:
            self._axis_selection_guard(team, expected_selection_revision)
            latest = self.get_team_axis_target(team.team_id)
            if (latest.revision if latest else 0) != expected_target_revision:
                raise ValueError("毕业目标已变化，请刷新后保存")
            if latest != target:
                if result.operation != "theoretical_benchmark":
                    raise ValueError("当前评价不能创建或切换目标版本")
                self.save_team_axis_target(team, target, expected_revision=expected_target_revision, checkpoint=checkpoint)
            # 内容寻址行绝不覆盖；重复显式保存只复用同一事实。选择仍走独立CAS。
            benchmark = self.get_team_axis_benchmark(team.team_id, result.benchmark_id)
            now = _utc_now()
            if result.operation == "theoretical_benchmark":
                if benchmark is None:
                    db.execute("INSERT INTO team_axis_graduation_benchmark VALUES(?,?,?,?,?,?,?)", (
                        result.benchmark_id, team.team_id, target.target_id, target.revision, result.sha256, result.body_json, now))
                elif benchmark != result:
                    raise ValueError("同一基准身份已有不同冻结正文")
            else:
                self._same_benchmark(benchmark, result)
            existing = self.get_team_axis_evaluation(team.team_id, result.evaluation_id)
            if existing is None:
                # 首评价直接复用基准正文，不在同一事务内复制整份轴快照。
                raw = None if result.operation == "theoretical_benchmark" else result.body_json
                db.execute("INSERT INTO team_axis_graduation_evaluation VALUES(?,?,?,?,?,?,?)", (
                    result.evaluation_id, result.benchmark_id, team.team_id, result.operation, result.sha256, raw, now))
            elif existing != result:
                raise ValueError("同一评价身份已有不同冻结正文")
            if select:
                self._axis_select(team.team_id, result, expected_selection_revision + 1)
            # 写锁期间再次复核账号代次、当前来源及组件；失败连同新目标、基准、评价和指针回滚。
            checkpoint()
        return result

    def select_team_axis_result(self, team, evaluation_id, *, expected_revision, checkpoint=lambda: None):
        checkpoint()
        with self.write_transaction():
            self._axis_selection_guard(team, expected_revision)
            result = self.get_team_axis_evaluation(team.team_id, evaluation_id)
            if result is None or result.target.character_ids != team.character_ids:
                raise ValueError("所选历史评价不属于当前队伍")
            self._axis_select(team.team_id, result, expected_revision + 1)
            checkpoint()
        return self.get_team_axis_selection(team.team_id)

    def iter_team_axis_results(self):
        """逐行验证关联后返回；导入应用边界继续做正式协议语义校验，不启动分析。"""
        for row in self._db().execute("SELECT team_id,benchmark_id FROM team_axis_graduation_benchmark ORDER BY rowid"):
            yield self.get_team_axis_benchmark(row[0], row[1])
        for row in self._db().execute("SELECT team_id,evaluation_id FROM team_axis_graduation_evaluation ORDER BY rowid"):
            yield self.get_team_axis_evaluation(row[0], row[1])
