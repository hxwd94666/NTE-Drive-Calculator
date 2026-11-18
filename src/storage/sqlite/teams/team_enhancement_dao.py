# 在方案保存事务内写轻量队伍结果，作业去重和成员方案身份都由账号 DAO 核验。
import json
from uuid import uuid4
from src.domain.equipment.loadouts.virtual_equipment import is_virtual_equipment_assignment

from src.domain.teams.enhancement import SavedTeamEnhancement, saved_plan_fingerprint, valid_digest, validate_joint_summary, validate_team_result_payload
from src.storage.sqlite.protocols import UserDataDaoMixinHost
from src.storage.sqlite.user_data_support import _utc_now


class TeamEnhancementDaoMixin(UserDataDaoMixinHost):
    def completed_team_run(self, run_id, input_digest):
        row = self._one("SELECT * FROM team_enhancement_run WHERE run_id=?", (run_id,))
        if row is None:
            return None
        if row["input_digest"] != input_digest:
            raise ValueError("作业身份已有不同的已保存结果，请重新计算")
        return tuple(json.loads(row["plan_ids_json"]))

    def assert_team_result_context(self, commit, *, sources=None):
        # sources 为本次写事务开始时读到的来源投影；同一事务写入方案后，单槽隐式默认绑定会随之出现，
        # 不能把自身写入误判为来源变化。未提供时读取当前投影。
        if self.profile()["account_id"] != commit.account_id:
            raise ValueError("队伍增强与当前账号不一致")
        teams = self.list_teams()
        if (not 1 <= len(commit.entries) <= 2 or len({entry.team.team_id for entry in commit.entries}) != len(commit.entries)
                or not valid_digest(commit.input_digest) or not isinstance(commit.run_id, str) or len(commit.run_id) != 32):
            raise ValueError("队伍增强提交身份无效")
        used = set()
        if len(commit.entries) == 2:
            validate_joint_summary(json.loads(commit.joint_summary_json or "null"), [entry.team.team_id for entry in commit.entries])
        elif commit.joint_summary_json is not None:
            raise ValueError("单队结果不能携带联合指标")
        for entry in commit.entries:
            current = sources[entry.team.team_id] if sources is not None else self.get_team_source(entry.team.team_id)
            if entry.team not in teams or current != entry.source:
                raise ValueError("队伍或简报来源已变化，请重新计算")
            payload = json.loads(entry.payload_json)
            validate_team_result_payload(payload)
            if (set(entry.team.character_ids) != set(payload["character_ids"])
                    or payload["team_revision"] != entry.team.revision
                    or set(key for _, key in entry.roles) != set(entry.team.character_ids)
                    or len(entry.roles) != 4 or used.intersection(entry.team.character_ids)
                    or payload["source_mode"] != entry.source.mode
                    or payload["source_revision"] != entry.source.revision
                    or payload["source_bindings"] != [list(pair) for pair in entry.source.bindings]):
                raise ValueError("队伍增强成员或来源绑定不完整")
            used.update(entry.team.character_ids)

    def save_team_enhancement_run(self, commit, plan_ids, *, sources=None):
        db = self._db()
        if not db.in_transaction:
            raise RuntimeError("队伍增强结果必须与方案处于同一保存事务")
        self.assert_team_result_context(commit, sources=sources)
        if self.completed_team_run(commit.run_id, commit.input_digest) is not None:
            raise ValueError("本次增强已经保存，请核对作业记录")
        plans = {plan["character_id"]: plan for plan in self.get_loadout_plans(plan_ids)}
        if len(plans) != len(plan_ids):
            raise ValueError("同次保存存在重复角色，队伍结果不能绑定不明确方案")
        db.execute("INSERT INTO team_enhancement_run VALUES(?,1,?,?,?,?,?)",
                   (commit.run_id, commit.input_digest, _utc_now(), int(len(commit.entries) == 2), json.dumps(list(plan_ids)), commit.joint_summary_json))
        for entry in commit.entries:
            if not set(entry.team.character_ids) <= plans.keys():
                raise ValueError("整队增强须完整保存四人成员，缺失成员不使用其他当前方案补齐")
            result_id = uuid4().hex
            db.execute("INSERT INTO team_enhancement_result VALUES(?,?,?,?)",
                       (result_id, commit.run_id, entry.team.team_id, entry.payload_json))
            for character_id in entry.team.character_ids:
                plan = plans[character_id]
                assignments = plan.get("assignments") or ()
                if (plan.get("status") != "ready" or not any(row["kind"] == "module" for row in assignments)
                        or sum(row["kind"] == "core" for row in assignments) != 1
                        or any(is_virtual_equipment_assignment(row.get("raw_assignment") or row) for row in assignments)):
                    raise ValueError("队伍增强缺少完整真实成员配装")
                slot = self.get_loadout_slot(plan["slot_id"])
                if slot is None or slot["current_plan_id"] != plan["plan_id"] or slot["character_id"] != character_id:
                    raise ValueError("队伍结果对应的最终方案身份未确认")
                db.execute("INSERT INTO team_enhancement_member VALUES(?,?,?,?,?,?)",
                    (result_id, character_id, plan["slot_id"], plan["plan_id"], plan["source_snapshot_id"], saved_plan_fingerprint(plan)))

    def list_team_enhancements(self, team_id, *, limit=20, offset=0, character_ids=None, run_id=None):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("增强历史分页范围无效")
        clause, parameters = "", [team_id]
        if run_id is not None:
            clause += " AND r.run_id=?"
            parameters.append(run_id)
        if character_ids is not None:
            ids = tuple(character_ids)
            if len(ids) != 4:
                return ()
            clause += " AND NOT EXISTS(SELECT 1 FROM json_each(json_extract(r.payload_json,'$.character_ids')) WHERE value NOT IN (?,?,?,?))"
            parameters.extend(ids)
        parameters.extend((limit, offset))
        rows = self._rows("SELECT r.*, run.created_at_utc, run.joint_summary_json FROM team_enhancement_result r "
                          "JOIN team_enhancement_run run USING(run_id) WHERE r.team_id=? " + clause +
                          " ORDER BY run.created_at_utc DESC, r.result_id DESC LIMIT ? OFFSET ?", parameters)
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            validate_team_result_payload(payload)
            members = tuple(self._rows("SELECT character_id,slot_id,plan_id,source_snapshot_id,fingerprint "
                "FROM team_enhancement_member WHERE result_id=? ORDER BY character_id", (row["result_id"],)))
            if len(members) != 4 or {member["character_id"] for member in members} != set(payload["character_ids"]):
                raise ValueError("队伍增强历史的成员投影不完整")
            for member in members:
                plan = self.get_loadout_plan(member["plan_id"])
                slot = self.get_loadout_slot(member["slot_id"])
                if (plan is not None and (plan["character_id"] != member["character_id"]
                        or plan["slot_id"] != member["slot_id"] or plan["source_snapshot_id"] != member["source_snapshot_id"])
                        or slot is not None and slot["character_id"] != member["character_id"]):
                    raise ValueError("增强历史的方案或槽位引用归属不一致")
            result.append(SavedTeamEnhancement(row["result_id"], row["run_id"], row["team_id"], row["created_at_utc"], row["payload_json"], members,
                                               joint_summary_json=row["joint_summary_json"]))
        return tuple(result)

    def team_run_results(self, run_id):
        rows = self._rows("SELECT team_id FROM team_enhancement_result WHERE run_id=? ORDER BY team_id", (run_id,))
        result = []
        for row in rows:
            result.extend(self.list_team_enhancements(row["team_id"], limit=1, run_id=run_id))
        return tuple(result)

    def validate_team_enhancement_data(self):
        """账号包验证使用；历史引用缺失合法，冻结正文和格式不完整则拒绝安装。"""
        for row in self._rows("SELECT DISTINCT team_id FROM team_enhancement_result"):
            offset = 0
            while True:
                page = self.list_team_enhancements(row["team_id"], limit=100, offset=offset)
                if len(page) < 100:
                    break
                offset += 100
        for row in self._rows("SELECT * FROM team_enhancement_run"):
            if row["format_version"] != 1 or not valid_digest(row["input_digest"]):
                raise ValueError("队伍增强作业格式无效")
            ids = json.loads(row["plan_ids_json"])
            if not isinstance(ids, list) or not ids or any(type(value) is not int or value <= 0 for value in ids) or len(set(ids)) != len(ids):
                raise ValueError("队伍增强作业的方案身份无效")
            results = self._rows("SELECT team_id,result_id FROM team_enhancement_result WHERE run_id=?", (row["run_id"],))
            if not results or len(results) > (2 if row["joint"] else 1):
                raise ValueError("队伍增强作业缺少对应结果或结果数量异常")
            if row["joint"]:
                summary = json.loads(row["joint_summary_json"] or "null")
                validate_joint_summary(summary, summary.get("team_ids", ()) if isinstance(summary, dict) else ())
                if not {result["team_id"] for result in results} <= set(summary["team_ids"]):
                    raise ValueError("联合指标引用了其他作业的队伍结果")
            elif row["joint_summary_json"] is not None:
                raise ValueError("单队作业含不一致的联合指标")
