# 冻结队伍增强的轻量持久结果与提交身份；本队负收益合法，B0 不伪造零提升。
from dataclasses import dataclass
import json
import hashlib
from math import isclose, isfinite

from src.domain.teams.models import Team
from src.domain.teams.overview import SourceMode, TeamSourceConfig
from src.domain.teams.report_matching import ReportRange


PROVENANCE_KEYS = frozenset({"dataset_id", "static_sha256", "engine_version", "model_id", "component_executable_sha256", "component_manifest_sha256"})
SEARCH_KEYS = frozenset({"termination", "overall_termination", "neighborhood_complete", "blueprint_complete",
                         "enumeration_complete", "quantification_complete", "blueprint_enumeration_complete", "search_mode",
                         "fallback_status", "reason"})


def nonnegative(value):
    try:
        return type(value) in (int, float) and isfinite(value) and value >= 0
    except OverflowError:
        return False


def _validate_team_result_payload(payload):
    """正式 v1 白名单不接受路径、逐击、装备列表或完整分析响应。"""
    required = {"version", "team_revision", "character_ids", "source_mode", "source_revision", "source_bindings",
                "scope", "anchor_mode", "result_kind", "baseline_value", "candidate_value", "relative_gain",
                "coverage", "search", "provenance", "source_input_digest", "baseline_build_sha256"}
    if not isinstance(payload, dict) or set(payload) != required or type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("队伍增强结果格式不支持")
    ids = payload["character_ids"]
    if (not isinstance(ids, list) or len(ids) != 4 or len(set(ids)) != 4
            or any(type(value) is not int or value <= 0 for value in ids)):
        raise ValueError("队伍结果必须保存四名正式成员")
    if type(payload["team_revision"]) is not int or payload["team_revision"] < 1:
        raise ValueError("队伍结果修订无效")
    # 旧 current 只作为不可改写的历史来源证据读取，不恢复其运行时自动选槽分支。
    historical_mode = payload["source_mode"]
    if historical_mode == "current":
        if payload["source_bindings"]:
            raise ValueError("旧自动来源历史不应包含槽位绑定")
        historical_mode = SourceMode.CALC
    TeamSourceConfig("validated", historical_mode, payload["source_revision"],
                     tuple(tuple(pair) for pair in payload["source_bindings"])).validated()
    if not {pair[0] for pair in payload["source_bindings"]} <= set(ids):
        raise ValueError("历史来源绑定包含冻结队伍外成员")
    ReportRange(**payload["scope"])
    if payload["anchor_mode"] not in {"original", "effective"}:
        raise ValueError("队伍结果的 A 来源无效")
    before, after, ratio = (payload[key] for key in ("baseline_value", "candidate_value", "relative_gain"))
    if payload["result_kind"] == "baseline":
        if before is not None and not nonnegative(before) or after is not None and not nonnegative(after) or ratio is not None:
            raise ValueError("基础配装不附带未证实提升")
    elif payload["result_kind"] == "optimized":
        if (not nonnegative(before) or before <= 0 or not nonnegative(after)
                or type(ratio) not in (int, float) or not isfinite(ratio)
                or not isclose(ratio, after/before-1, rel_tol=1e-10, abs_tol=1e-10)):
            raise ValueError("队伍增强值与本队 B0 不一致")
    else:
        raise ValueError("队伍增强结果类型无效")
    coverage = payload["coverage"]
    if coverage is not None:
        keys = {"policy", "basis_damage", "quantified_damage", "estimated_damage", "excluded_damage"}
        if (not isinstance(coverage, dict) or set(coverage) != keys or coverage["policy"] != "available_fixed_domain_v1"
                or not all(nonnegative(coverage[key]) for key in keys-{"policy"}) or coverage["basis_damage"] <= 0
                or not isclose(coverage["quantified_damage"]+coverage["estimated_damage"]+coverage["excluded_damage"],
                               coverage["basis_damage"], rel_tol=1e-9, abs_tol=1e-9)):
            raise ValueError("队伍增强覆盖率不完整")
    if not isinstance(payload["search"], dict) or set(payload["search"]) - SEARCH_KEYS:
        raise ValueError("队伍搜索状态格式无效")
    for key, value in payload["search"].items():
        if key.endswith("complete"):
            if value is not None and type(value) is not bool:
                raise ValueError("队伍搜索完整性状态无效")
        elif value is not None and (not isinstance(value, str) or len(value) > 160):
            raise ValueError("队伍搜索停止状态无效")
    if not isinstance(payload["provenance"], dict) or set(payload["provenance"]) - PROVENANCE_KEYS:
        raise ValueError("队伍结果来源格式无效")
    for key, value in payload["provenance"].items():
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError("队伍结果来源身份缺失")
        if key.endswith("sha256") and not valid_digest(value):
            raise ValueError("队伍组件哈希无效")
    for key in ("source_input_digest", "baseline_build_sha256"):
        if payload[key] is not None and not valid_digest(payload[key]):
            raise ValueError("队伍冻结摘要无效")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > 32768:
        raise ValueError("队伍简报结果超过轻量限制")
    return encoded


def validate_team_result_payload(payload):
    try:
        return _validate_team_result_payload(payload)
    except (TypeError, KeyError, OverflowError) as error:
        raise ValueError("队伍增强结果的数据类型或字段无效") from error


def valid_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def saved_plan_fingerprint(plan):
    value = {key: plan.get(key) for key in ("character_id", "source_snapshot_id", "status", "score", "assignments")}
    value["assignment_scores"] = (plan.get("payload") or {}).get("assignment_scores")
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TeamResultCommitEntry:
    team: Team
    source: TeamSourceConfig
    roles: tuple[tuple[str, int], ...]
    payload_json: str


@dataclass(frozen=True)
class TeamResultCommit:
    run_id: str
    account_id: str
    input_digest: str
    entries: tuple[TeamResultCommitEntry, ...]
    joint_summary_json: str | None = None


@dataclass(frozen=True)
class SavedTeamEnhancement:
    result_id: str
    run_id: str
    team_id: str
    created_at_utc: str
    payload_json: str
    members: tuple[dict, ...]
    association: tuple[str, ...] = ()
    joint_summary_json: str | None = None
    joint_results: tuple = ()

    @property
    def payload(self):
        return json.loads(self.payload_json)


def validate_joint_summary(value, team_ids):
    required = {"version", "team_ids", "normalized_baseline", "normalized_candidate", "relative_gain"}
    if (not isinstance(value, dict) or set(value) != required or type(value["version"]) is not int or value["version"] != 1
            or value["team_ids"] != list(team_ids) or len(team_ids) != 2 or len(set(team_ids)) != 2):
        raise ValueError("联合增强身份格式无效")
    before, after, ratio = (value[key] for key in ("normalized_baseline", "normalized_candidate", "relative_gain"))
    if before is None and after is None and ratio is None:
        return
    if (type(before) not in (int, float) or before != 1 or not nonnegative(after) or type(ratio) not in (int, float) or not isfinite(ratio)
            or not isclose(ratio, after-1, rel_tol=1e-10, abs_tol=1e-10)):
        raise ValueError("联合指标须为本次 B0 归一指数，不是单队伤害")
