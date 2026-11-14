# 在worker中只读核对冻结结果与现有输入/清单；不探测原战报、不启动组件进程、不搜索新分母。
from concurrent.futures import CancelledError
from dataclasses import asdict
import json

from src.domain.localization.catalog import localized_text
from src.domain.teams.graduation.axis_graduation_validity import AxisResultValidity
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.services.allocation.inputs.context_builder import read_allocation_scoring_rules


def check_result_validity(inputs, team, *, axis, target, rules, client_factory, scoring_rules_path,
                          preview=None, record=None, cancelled=lambda: False, text_catalog=None):
    inputs.checkpoint(cancelled)
    if (preview is None) == (record is None):
        raise ValueError("有效性核对需要一份预览或已保存结果")
    if preview is not None:
        body, response = preview.request.payload, preview.response
        if (preview.request.current.account_id, preview.request.current.generation) != (
                inputs.account_id, inputs.generation):
            raise CancelledError("待核对预览不属于当前账号代次")
    else:
        value = record.to_payload()
        body, response = value["request"], value["response"]
    if body["current"]["account_id"] != inputs.account_id:
        raise CancelledError("待核对结果不属于当前账号")
    reasons = []
    benchmark, current, component = "matching", "unverified", "ready"
    scope = response["axis_snapshot"]["scope"]
    expected_scope = {key: axis.selector_fields().get(key) for key in scope}
    if (body["current"]["team_id"] != team.team_id or tuple(body["current"]["character_ids"]) != team.character_ids
            or scope != expected_scope or body["target"] != target.to_payload()
            or json.dumps(body["rules"], sort_keys=True) != json.dumps(asdict(rules), sort_keys=True)):
        benchmark = "stale"
        reasons.append(localized_text(text_catalog, "teams.axis_graduation.validity.reason.benchmark_changed",
                                         "队伍、参考轴、目标或词条规则与该冻结结果不同；显式重建基准"))
    # 规则文件仅走已有公共读取口径；模型/静态变化不解释成当前装备变化。
    try:
        scoring = read_allocation_scoring_rules(scoring_rules_path)
        if (scoring.source_sha256, scoring.rule_sha256) != (rules.scoring_source_sha256, rules.scoring_rule_sha256):
            benchmark = "stale"
            reasons.append(localized_text(text_catalog, "teams.axis_graduation.validity.reason.scoring_changed",
                                         "当前评分规则身份已变化；旧分母保留为历史"))
    except (OSError, ValueError, RuntimeError) as error:
        if benchmark != "stale":
            benchmark = "unverified"
        reasons.append(localized_text(text_catalog, "teams.axis_graduation.validity.reason.scoring_unverified",
                                         "评分规则尚未核实：{error}", error=str(error)))
    inputs.checkpoint(cancelled)
    try:
        actual = inputs.freeze_current(team, cancelled=cancelled)
        if (actual.dataset_id, actual.static_schema, actual.static_sha256) != (
                rules.dataset_id, rules.static_schema, rules.static_sha256):
            benchmark = "stale"
            reasons.append(localized_text(text_catalog, "teams.axis_graduation.validity.reason.static_changed",
                                         "完整主静态身份已变化；须使用新目标和新基准"))
        current = "matching" if actual.sha256 == body["current"]["source_sha256"] else "stale"
        if current == "stale":
            reasons.append(localized_text(text_catalog, "teams.axis_graduation.validity.reason.current_changed",
                                         "当前养成、家具、所选配装或来源已变化；旧评价不代表当前，可显式更新分子"))
    except (OSError, ValueError, RuntimeError) as error:
        reasons.append(localized_text(text_catalog, "teams.axis_graduation.validity.reason.source_unverified",
                                         "当前来源尚未核实：{error}", error=str(error)))
    inputs.checkpoint(cancelled)
    def component_cancelled():
        try:
            inputs.checkpoint(cancelled)
            return False
        except CancelledError:
            return True
    try:
        # 工厂只验证清单/执行物哈希并构造client；这里不调用任何EXE/页面/握手方法。
        client = client_factory(static_database_path=inputs.static_database_path, cancelled=component_cancelled)
        if client is not None and (client.engine_version != response["engine_version"]
              or client.component_provenance != body["component_provenance"]
              or client.dataset_version != body["current"]["dataset_id"]):
            component, benchmark = "changed", "stale"
            reasons.append(localized_text(text_catalog, "teams.axis_graduation.validity.reason.component_changed",
                                         "分析组件身份已变化；旧基准不重新签名，须显式重建"))
        elif client is None or not getattr(client, "supports_team_axis_graduation_current", False):
            component = "pending"
            reasons.append(localized_text(text_catalog, "teams.axis_graduation.validity.reason.component_pending",
                                         "当前组件缺少毕业评价能力；历史仍可读取"))
    except NativeAnalysisError as error:
        inputs.checkpoint(cancelled)
        component = "pending"
        reasons.append(localized_text(text_catalog, "teams.axis_graduation.validity.reason.component_unverified",
                                         "组件尚未核实：{error}", error=str(error)))
    inputs.checkpoint(cancelled)
    return AxisResultValidity(benchmark, current, component, tuple(reasons))
