# 传输正式冻结队伍预检，只核对身份与集合分区，不从伤害记录推测队伍。
from copy import deepcopy
import json

from src.integrations.battle_axis_evaluation import validate_axis_selector
from src.integrations.nte_analysis_core import MAX_BYTES, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object


CAPABILITY = "battle_axis_team_v1"
REASONS = frozenset({
    "axis_team_attempt_missing", "axis_team_context_incomplete", "axis_team_members_incomplete",
    "axis_team_identity_conflict", "axis_team_changed", "axis_team_events_missing",
    "axis_team_event_identity_invalid", "axis_team_scope_conflict", "axis_selected_outside_team",
})


def _ids(value):
    return (isinstance(value, list) and all(type(i) is int and i > 0 for i in value)
            and value == sorted(set(value)))


def resolve_axis_team(client, selector, selected_character_ids, *, checkpoint=None):
    """ready仅证明T/S身份；固定装备、养成与机制须由正式优化作业另验。"""
    client._checkpoint(checkpoint)
    if not client.supports_battle_axis_team:
        raise NativeAnalysisError("分析组件缺少正式队伍预检能力，请更新配套组件")
    try:
        validate_axis_selector(selector)
        selected = list(selected_character_ids)
        if (not 1 <= len(selected) <= 64 or any(type(i) is not int or i <= 0 for i in selected)
                or len(set(selected)) != len(selected) or selector.get("selected_character_id") is not None):
            raise ValueError("selection")
        start, end, scope = (selector.get(key) for key in ("start_us", "end_us", "detail_scope"))
        if scope not in {None, "first", "second"} or ((start is None) != (end is None)) or (start is not None and scope is not None):
            raise ValueError("scope")
        selected.sort()
        payload = {"schema_version": REQUEST_SCHEMA, "dataset_version": client.dataset_version,
                   "batch_kind": CAPABILITY, "request": {"version": 1, "battle_request": selector,
                                                        "selected_character_ids": selected}}
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError("size")
    except (ValueError, TypeError) as error:
        raise NativeAnalysisError("正式队伍预检输入无效") from error
    result = _json_object(client._run(encoded, checkpoint=checkpoint))
    client._checkpoint(checkpoint)
    validate_team_response(client, result, selector, selected, CAPABILITY)
    return {key: deepcopy(result[key]) for key in (
        "ready", "reason", "basis", "team_character_ids", "selected_character_ids", "fixed_character_ids",
        "outside_character_ids", "scopes", "source_input_digest", "start_us", "end_us", "detail_scope")}


def validate_team_response(client, result, selector, selected, capability):
    """两个预检入口共享相同的T/S/R及来源响应边界。"""
    scope = selector.get("detail_scope")
    try:
        if (result["schema_version"] != RESPONSE_SCHEMA or result["engine_version"] != client.engine_version
                or result["dataset_version"] != client.dataset_version or result["batch_kind"] != capability
                or type(result["version"]) is not int or result["version"] != 1
                or type(result["ready"]) is not bool or result["basis"] != "frozen_native_team_v1"
                or result["reason"] not in REASONS | {None}):
            raise ValueError("identity")
        for key in ("team_character_ids", "selected_character_ids", "fixed_character_ids", "outside_character_ids"):
            if not _ids(result[key]):
                raise ValueError("ids")
        if result["selected_character_ids"] != selected:
            raise ValueError("selection changed")
        if any(result[key] != selector.get(key) or type(result[key]) is not type(selector.get(key))
               for key in ("start_us", "end_us", "detail_scope")):
            raise ValueError("range changed")
        team, fixed, outside = (set(result[key]) for key in ("team_character_ids", "fixed_character_ids", "outside_character_ids"))
        scopes, digest = result["scopes"], result["source_input_digest"]
        if (not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                or not isinstance(scopes, list) or any(s not in {"combat", "upper", "lower"} for s in scopes)
                or scopes != sorted(set(scopes))):
            raise ValueError("scope identity")
        if team:
            if (len(team) != 4 or not scopes or fixed != team - set(selected) or outside != set(selected) - team
                    or result["ready"] != (not outside)
                    or result["reason"] != ("axis_selected_outside_team" if outside else None)
                    or scope == "first" and scopes != ["upper"] or scope == "second" and scopes != ["lower"]):
                raise ValueError("team partition")
        elif fixed or outside or scopes or result["ready"] or result["reason"] in {None, "axis_selected_outside_team"}:
            raise ValueError("gap masquerades as team")
    except (KeyError, TypeError, ValueError) as error:
        raise NativeAnalysisError("正式队伍预检响应与冻结选择不一致") from error
