# 以现有严格线协议校验可迁移历史，不打开文件、不派发历史请求，也不重算伤害公式。
import json
from pathlib import Path

from src.domain.teams.graduation.axis_graduation_record import FORMAT, SavedAxisResult
from src.domain.teams.report_matching import ReportRange
from src.integrations.battle_analysis.graduation.team_axis_graduation import _rules
from src.integrations.battle_analysis.graduation.team_axis_graduation import validate_response


def validate_saved_axis_result(record):
    if not isinstance(record, SavedAxisResult):
        raise ValueError("历史结果需要规范冻结正文")
    value = record.to_payload()
    request, response = value["request"], value["response"]
    # 只为纯结构校验补齐选择器字段；占位路径绝不返回给执行入口或读取文件。
    selector = request["battle_request"]
    selector.update(generation=0, **{key: str(Path("/__axis_history_validation__", name).absolute()) for key, name in (
        ("user_database_path", "user.sqlite3"), ("static_database_path", "static.sqlite3"), ("semantics_path", "semantics.json"))})
    request["current"]["generation"] = 0
    if request["operation"] != "theoretical_benchmark":
        request.update(reference=response["benchmark_reference"], axis_snapshot=response["axis_snapshot"])
    # 原始请求哈希作为审计事实保留；移除会话字段后的存储正文另有自己的内容SHA。
    validate_response(response, request, dataset_version=response["dataset_version"],
        engine_version=response["engine_version"], request_sha256=response["request_sha256"])
    return record


def archive_axis_result(request, response):
    # 先完整验证协议再构造可迁移正文；执行请求的确切字节哈希由预览owner另行核对。
    validate_response(response, request, dataset_version=response["dataset_version"],
        engine_version=response["engine_version"], request_sha256=response["request_sha256"])
    request = json.loads(json.dumps(request, ensure_ascii=False, allow_nan=False))
    request["battle_request"] = {k: v for k, v in request["battle_request"].items()
                                 if k not in {"generation", "user_database_path", "static_database_path", "semantics_path"}}
    request["current"].pop("generation")
    request.pop("reference", None)
    request.pop("axis_snapshot", None)
    return validate_saved_axis_result(SavedAxisResult(json.dumps(
        {"format": FORMAT, "request": request, "response": response}, ensure_ascii=False, allow_nan=False)))


def saved_axis_context(record):
    """还原正式目标、规则与范围；当前实际构筑须在后续显式评价时重新冻结。"""
    validate_saved_axis_result(record)
    value = record.to_payload()
    scope = value["response"]["axis_snapshot"]["scope"]
    kind = "range" if scope["start_us"] is not None or scope["end_us"] is not None else scope["detail_scope"] or "all"
    return (ReportRange(scope["battle_record_id"], kind, scope["start_us"], scope["end_us"]),
            record.target, _rules(value["request"]["rules"]))
