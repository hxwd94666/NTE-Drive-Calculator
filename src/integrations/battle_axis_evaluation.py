# 校验冻结战报轴批量评价协议，只接纳正式完整目标，不求和展示投影或另算伤害。
from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
from pathlib import Path
from typing import Any

from src.domain.battle_report.counterfactual.quantification import BattleDamageQuantification, BattleQuantificationGap
from src.integrations.nte_analysis_core import MAX_BYTES, REQUEST_SCHEMA, RESPONSE_SCHEMA, NativeAnalysisError, _json_object


BATCH_KIND = "battle_axis_evaluate_v1"
MODEL_ID = "frozen_axis_expected_v1"
SCOPE_KIND = "all_frozen_axis_events_v1"
CRITICAL_POLICY = "expected_with_frozen_observed_triggers"
IDENTITY_KEYS = ("facts_sha256", "anchor_sha256", "baseline_sha256", "axis_sha256")
SELECTOR_KEYS = frozenset({"account_id", "generation", "battle_record_id", "user_database_path",
                           "static_database_path", "semantics_path", "start_us", "end_us", "detail_scope", "detail_level"})
REQUEST_KEYS = frozenset({"version", "input_identity", "battle_request", "anchor_mode", "selected_character_id",
                          "equipment_value_mode", "baseline_equipment_by_character", "candidates"})
SCOPE_KEYS = ("kind", "selected_character_id", "anchor_mode", "range_start_us", "range_end_us",
              "outgoing_event_count", "includes_linked_team_effects", "event_domain", "source_axis_complete",
              "capture_state", "native_transport_complete", "source_coverage")
QUANTIFICATION_KEYS = ("status", "basis_damage", "fully_quantified_damage", "partially_quantified_damage",
                       "unavailable_damage", "proven_unchanged_damage", "quantified_increment", "gaps")
ROW_KEYS = ("candidate_id", "status", "model_id", "unit", "scope", "anchor_value", "candidate_value",
            "delta_from_anchor", "delta_from_baseline", "quantification", "gaps")
COMPLETE = frozenset({"complete", "not_applicable"})
STATUSES = COMPLETE | {"partial", "unavailable"}


def _error() -> NativeAnalysisError:
    return NativeAnalysisError("战报轴批量评价协议或冻结事实无效")


def _digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value.encode("utf8")) <= 256


def _integer(value: Any, minimum=0, maximum=2**63 - 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _number(value: Any, *, signed=False) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value) and (signed or value >= 0.0)
    except OverflowError:
        return False


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-7)


def validate_axis_equipment(items: Any, *, complete=True) -> set[tuple[int, int]]:
    if (not isinstance(items, list) or not (2 <= len(items) <= 17 if complete else 1 <= len(items) <= 100_000)
            or any(not isinstance(item, dict) for item in items)
            or complete and sum(item.get("kind") == "core" for item in items) != 1):
        raise _error()
    uids = set()
    for item in items:
        uid = (item.get("uid_slot"), item.get("uid_serial"))
        if (item.get("kind") not in {"core", "module"} or not _integer(uid[0]) or not _integer(uid[1], 1)
                or uid in uids or not isinstance(item.get("quality"), str)
                or item["quality"].strip().casefold() not in {"gold", "orange", "purple", "blue"}
                or not _integer(item.get("level")) or not _integer(item.get("max_level"), item["level"])
                or type(item.get("locked")) is not bool
                or not _text(item.get("item_id"))
                or item["kind"] == "core" and not _text(item.get("suit_id"))
                or item["kind"] == "module" and item.get("suit_id") is not None and not _text(item["suit_id"])):
            raise _error()
        if item["kind"] == "module" and (not _text(item.get("geometry")) or not _integer(item.get("grid_count"), 1, 15)):
            raise _error()
        stats = item.get("stats")
        if not isinstance(stats, list) or not 1 <= len(stats) <= 32:
            raise _error()
        for stat in stats:
            if (not isinstance(stat, dict) or stat.get("stat_group") not in {"main", "sub"}
                    or not _text(stat.get("property_id")) or not _number(stat.get("value"), signed=True)
                    or type(stat.get("is_percent")) is not bool):
                raise _error()
        if item["kind"] == "core" and not any(stat["stat_group"] == "main" for stat in stats):
            raise _error()
        uids.add(uid)
    return uids


def validate_axis_selector(selector):
    if not isinstance(selector, dict) or not set(selector) <= SELECTOR_KEYS:
        raise _error()
    if (not _text(selector.get("account_id")) or not _integer(selector.get("generation"))
            or not _integer(selector.get("battle_record_id"), 1)):
        raise _error()
    for key in ("user_database_path", "static_database_path", "semantics_path"):
        if not isinstance(selector.get(key), str) or not Path(selector[key]).is_absolute():
            raise _error()
    for key in ("start_us", "end_us"):
        if selector.get(key) is not None and not _integer(selector[key]):
            raise _error()
    if (selector.get("start_us") is not None and selector.get("end_us") is not None
            and selector["end_us"] <= selector["start_us"]):
        raise _error()
    if selector.get("detail_scope") is not None and not _text(selector["detail_scope"]):
        raise _error()
    if selector.get("detail_level", "hit") != "hit":
        raise _error()


def validate_axis_request(request: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the public DTO before dispatch; Rust still owns semantic equipment/axis checks."""
    try:
        if set(request) != REQUEST_KEYS or type(request["version"]) is not int or request["version"] != 1:
            raise _error()
        identity = request["input_identity"]
        if (not isinstance(identity, dict) or set(identity) != {"request_id", "input_sha256"}
                or not _text(identity["request_id"]) or not _digest(identity["input_sha256"])):
            raise _error()
        if (request["anchor_mode"] not in {"original", "effective"}
                or not _integer(request["selected_character_id"], 1) or request["equipment_value_mode"] != "actual_level"):
            raise _error()
        validate_axis_selector(request["battle_request"])
        builds, candidates = request["baseline_equipment_by_character"], request["candidates"]
        if not isinstance(builds, list) or not 1 <= len(builds) <= 16 or not isinstance(candidates, list) or len(candidates) > 256:
            raise _error()
        characters, occupied = set(), set()
        for build in builds:
            if (not isinstance(build, dict) or set(build) != {"character_id", "equipment"}
                    or not _integer(build["character_id"], 1) or build["character_id"] in characters):
                raise _error()
            uids = validate_axis_equipment(build["equipment"])
            if uids & occupied:
                raise _error()
            characters.add(build["character_id"])
            occupied.update(uids)
        if request["selected_character_id"] not in characters:
            raise _error()
        identifiers = []
        for candidate in candidates:
            if (not isinstance(candidate, dict) or set(candidate) != {"candidate_id", "equipment"}
                    or not _text(candidate["candidate_id"]) or candidate["candidate_id"] in identifiers
                    or candidate["candidate_id"] == "baseline"):
                raise _error()
            validate_axis_equipment(candidate["equipment"])
            identifiers.append(candidate["candidate_id"])
        return tuple(identifiers)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise _error() from None


def _gaps(value: Any, *, full=False) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _error()
    result = []
    for gap in value:
        if not isinstance(gap, dict) or not _text(gap.get("code")):
            raise _error()
        keys = ("code", "dimension_id", "dependency_scope", "property_ids", "explanation")
        if full or any(key in gap for key in keys[1:]):
            if (not all(isinstance(gap.get(key), str) for key in ("dimension_id", "explanation"))
                    or gap.get("dependency_scope") not in {"character_only", "target_sensitive", "mechanic_specific"}
                    or not isinstance(gap.get("property_ids"), list) or not all(_text(v) for v in gap["property_ids"])):
                raise _error()
            result.append({key: gap[key] for key in keys})
        else:
            result.append({"code": gap["code"]})
    return result


def _quantification(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not set(QUANTIFICATION_KEYS) <= value.keys() or value["status"] not in STATUSES:
        raise _error()
    if (not all(_number(value[key]) for key in QUANTIFICATION_KEYS[1:6])
            or value["quantified_increment"] is not None and not _number(value["quantified_increment"], signed=True)):
        raise _error()
    gaps = _gaps(value["gaps"], full=True)
    quantification = {key: value[key] for key in QUANTIFICATION_KEYS}
    quantification["gaps"] = gaps
    try:
        # 公共量化值对象负责四态与桶守恒；适配层不引入第二份目标或公式。
        BattleDamageQuantification(**{**quantification, "gaps": tuple(BattleQuantificationGap(
            **{**gap, "property_ids": tuple(gap["property_ids"])}) for gap in gaps)})
    except (TypeError, ValueError, OverflowError):
        raise _error() from None
    return quantification


def _row(value: Any, identifier: str) -> dict[str, Any]:
    if (not isinstance(value, dict) or not set(ROW_KEYS) <= value.keys() or value["candidate_id"] != identifier
            or value["status"] not in STATUSES or value["model_id"] != MODEL_ID
            or value["unit"] != "reported_weight_expected_damage" or value["scope"] != SCOPE_KIND):
        raise _error()
    result = {key: value[key] for key in ROW_KEYS}
    result["gaps"] = _gaps(value["gaps"])
    result["quantification"] = quantification = _quantification(value["quantification"])
    anchor, candidate = value["anchor_value"], value["candidate_value"]
    if anchor is not None and not _number(anchor):
        raise _error()
    if quantification is not None:
        if (result["gaps"] != quantification["gaps"]
                or anchor is None or not _close(anchor, quantification["basis_damage"])):
            raise _error()
    elif value["status"] != "unavailable" or not result["gaps"]:
        raise _error()
    if value["status"] in COMPLETE:
        if (quantification is None or quantification["status"] != value["status"]
                or not _number(anchor) or not _number(candidate)
                or not all(_number(value[key], signed=True) for key in ("delta_from_anchor", "delta_from_baseline"))
                or not _close(candidate - anchor, value["delta_from_anchor"])
                or not _close(value["delta_from_anchor"], quantification["quantified_increment"])):
            raise _error()
    elif any(value[key] is not None for key in ("candidate_value", "delta_from_anchor", "delta_from_baseline")):
        raise _error()
    if value["status"] == "partial" and (quantification is None or quantification["status"] != "partial"):
        raise _error()
    return result


def _scope(value: Any, request: Mapping[str, Any]) -> dict[str, Any] | None:
    if value is None:
        return None
    group = "selected_character_id" not in request
    keys = (set(SCOPE_KEYS) - {"selected_character_id"}) | {"selected_character_ids"} if group else set(SCOPE_KEYS)
    if (not isinstance(value, dict) or not keys <= value.keys() or value["kind"] != SCOPE_KIND
            or value["event_domain"] != "saved_frozen_observed_events"
            or not group and (value["selected_character_id"] != request["selected_character_id"] or type(value["selected_character_id"]) is not int)
            or group and (not isinstance(value["selected_character_ids"], list)
                or any(not _integer(v, 1) for v in value["selected_character_ids"])
                or sorted(value["selected_character_ids"]) != sorted(request["role_character_ids"].values()))
            or value["anchor_mode"] != request["anchor_mode"]
            or value["includes_linked_team_effects"] is not True or value["capture_state"] != "finalized"
            or type(value["source_axis_complete"]) is not bool
            or value["native_transport_complete"] is not None and type(value["native_transport_complete"]) is not bool
            or value["source_coverage"] is not None and not isinstance(value["source_coverage"], str)
            or not all(_integer(value[key]) for key in ("range_start_us", "range_end_us", "outgoing_event_count"))
            or value["range_end_us"] < value["range_start_us"]):
        raise _error()
    for selector_key, scope_key in (("start_us", "range_start_us"), ("end_us", "range_end_us")):
        expected = request["battle_request"].get(selector_key)
        if expected is not None and value[scope_key] != expected:
            raise _error()
    # 可选来源计数只解释本次目标域的筛选和冻结兜底，不改变原战报完整性。
    for key in ("excluded_unscoped_event_count", "settlement_profile_character_count"):
        if key in value:
            if not _integer(value[key]):
                raise _error()
            keys.add(key)
    return {key: value[key] for key in keys}


def decode_axis_response_facts(result: Mapping[str, Any], request: Mapping[str, Any], identifiers: tuple[str, ...]) -> dict[str, Any]:
    """Share frozen identity/scope/quantification acceptance with native search, without re-evaluation."""
    if not _digest(result.get("source_input_digest")):
        raise _error()
    identity = result.get("frozen_identity")
    if (not isinstance(identity, dict) or not set(IDENTITY_KEYS) <= identity.keys()
            or not _digest(identity["facts_sha256"])
            or any(identity[key] is not None and not _digest(identity[key]) for key in IDENTITY_KEYS)):
        raise _error()
    scope = _scope(result.get("scope"), request)
    baseline = _row(result.get("baseline"), "baseline")
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != len(identifiers):
        raise _error()
    rows = [_row(row, identifier) for row, identifier in zip(candidates, identifiers)]
    for row in [baseline, *rows]:
        if row["anchor_value"] is not None and baseline["anchor_value"] is not None:
            if not _close(row["anchor_value"], baseline["anchor_value"]):
                raise _error()
        if row["status"] in COMPLETE:
            if (scope is None or scope["native_transport_complete"] is False
                    or any(identity[key] is None for key in IDENTITY_KEYS)
                    or baseline["status"] not in COMPLETE
                    or not _close(row["candidate_value"] - baseline["candidate_value"], row["delta_from_baseline"])):
                raise _error()
    # 只输出已校验字段；known/heuristic/原击展示金额均不进入正式目标。
    return {"source_input_digest": result["source_input_digest"],
            "frozen_identity": {key: identity[key] for key in IDENTITY_KEYS}, "scope": scope, "baseline": baseline, "candidates": rows}


def _response(result: dict[str, Any], client, request, identifiers) -> dict[str, Any]:
    if (result.get("schema_version") != RESPONSE_SCHEMA or result.get("engine_version") != client.engine_version
            or result.get("dataset_version") != client.dataset_version or result.get("batch_kind") != BATCH_KIND
            or type(result.get("version")) is not int or result["version"] != 1
            or result.get("input_identity") != request["input_identity"] or result.get("model_id") != MODEL_ID
            or result.get("equipment_value_mode") != "actual_level" or result.get("critical_policy") != CRITICAL_POLICY
            or "error" in result):
        raise _error()
    return {"schema_version": RESPONSE_SCHEMA, "engine_version": client.engine_version,
            "dataset_version": client.dataset_version, "batch_kind": BATCH_KIND, "version": 1,
            "input_identity": dict(request["input_identity"]), "model_id": MODEL_ID,
            "equipment_value_mode": "actual_level", "critical_policy": CRITICAL_POLICY,
            **decode_axis_response_facts(result, request, identifiers)}


def evaluate_battle_axis(client, request: Mapping[str, Any], *, checkpoint: Callable[[], None] | None = None) -> dict[str, Any]:
    """One child evaluates a complete frozen B0 and every requested candidate against the same A."""
    client._checkpoint(checkpoint)
    if not getattr(client, "supports_battle_axis_evaluation", False):
        raise NativeAnalysisError("战报轴批量评价组件未就绪，请更新配套分析组件")
    identifiers = validate_axis_request(request)
    try:
        payload = json.dumps({"schema_version": REQUEST_SCHEMA, "batch_kind": BATCH_KIND,
                              "dataset_version": client.dataset_version, "request": request},
                             ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    except (TypeError, ValueError):
        raise _error() from None
    if len(payload) > MAX_BYTES:
        raise NativeAnalysisError("战报轴批量评价输入超过大小限制")
    # 对真正派发的字节再次校验并持有独立映射，不追随调用方在子进程执行期的编辑。
    request = json.loads(payload)["request"]
    identifiers = validate_axis_request(request)
    result = _json_object(client._run(payload, checkpoint=checkpoint))
    try:
        validated = _response(result, client, request, identifiers)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise _error() from None
    client._checkpoint(checkpoint)
    return validated
