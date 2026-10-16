# 验证轴批量正式协议的共同锚点、未知四态、冻结身份与数值接纳门禁。
from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from tests.battle_report.analysis.axis_evaluation_fixture import wire_request, wire_response
from src.integrations.battle_axis_evaluation import evaluate_battle_axis
from src.integrations.nte_analysis_core import NativeAnalysisError


NTE_TEST_TIER = "core"


def client(response, *, available=True):
    return Mock(supports_battle_axis_evaluation=available, dataset_version="fixture-static", engine_version="0.3.0",
                _run=Mock(return_value=json.dumps(response, allow_nan=False).encode("utf8")))


def test_batch_keeps_full_values_separate_from_source_coverage_and_drops_unknown_fields():
    request, response = wire_request(), wire_response()
    response["scope"].update(excluded_unscoped_event_count=1, settlement_profile_character_count=4)
    response["unchecked"] = {"private": "unused"}
    response["candidates"][0]["known_projection"] = 99999
    transport = client(response)
    result = evaluate_battle_axis(transport, request)
    assert result["scope"]["source_axis_complete"] is False
    assert result["scope"]["native_transport_complete"] is True
    assert result["scope"]["excluded_unscoped_event_count"] == 1
    assert result["scope"]["settlement_profile_character_count"] == 4
    assert result["candidates"][0]["delta_from_baseline"] == 15.0
    assert "unchecked" not in result and "known_projection" not in result["candidates"][0]
    assert transport._run.call_count == 1
    encoded = json.loads(transport._run.call_args.args[0])
    assert encoded["batch_kind"] == "battle_axis_evaluate_v1" and encoded["request"] == request


@pytest.mark.parametrize("location,key,value", [
    (("scope",), "excluded_unscoped_event_count", -1),
    (("scope",), "settlement_profile_character_count", True),
    ((), "dataset_version", "different"), ((), "version", True), ((), "model_id", "other"), ((), "error", {"code": "failed"}),
    (("input_identity",), "request_id", "stale"), (("frozen_identity",), "baseline_sha256", None),
    (("scope",), "selected_character_id", 1001), (("scope",), "anchor_mode", "effective"),
    (("scope",), "native_transport_complete", False), (("scope",), "outgoing_event_count", True),
    (("baseline",), "candidate_value", True), (("candidates", 0), "candidate_id", "wrong"),
    (("candidates", 0), "anchor_value", 99.0), (("candidates", 0), "delta_from_baseline", 25.0),
    (("candidates", 0, "quantification"), "fully_quantified_damage", 99.0),
    (("candidates", 0, "quantification"), "quantified_increment", 1.0),
])
def test_forged_response_facts_are_rejected(location, key, value):
    response = wire_response()
    target = response
    for part in location:
        target = target[part]
    target[key] = value
    with pytest.raises(NativeAnalysisError):
        evaluate_battle_axis(client(response), wire_request())


def test_partial_b0_does_not_promote_aggregate_increment_to_a_full_candidate():
    response = wire_response()
    gap = {"code": "fixture_missing", "dimension_id": "scaling", "dependency_scope": "character_only",
           "property_ids": ["AtkUp"], "explanation": "公开夹具缺少缩放证据"}
    baseline = response["baseline"]
    baseline.update(status="partial", candidate_value=None, delta_from_anchor=None, delta_from_baseline=None)
    baseline["quantification"].update(status="partial", fully_quantified_damage=80.0, unavailable_damage=20.0,
                                     quantified_increment=8.0, gaps=[gap])
    baseline["gaps"] = [gap]
    candidate = response["candidates"][0]
    candidate.update(status="unavailable", anchor_value=None, candidate_value=None, delta_from_anchor=None,
                     delta_from_baseline=None, quantification=None, gaps=[{"code": "axis_baseline_unquantified"}])
    result = evaluate_battle_axis(client(response), wire_request())
    assert result["baseline"]["candidate_value"] is None
    assert result["baseline"]["quantification"]["quantified_increment"] == 8.0
    assert result["candidates"][0]["candidate_value"] is None
    forged = deepcopy(response)
    forged["candidates"][0]["candidate_value"] = 108.0
    with pytest.raises(NativeAnalysisError):
        evaluate_battle_axis(client(forged), wire_request())


def test_record_not_ready_retains_null_scope_and_hashes():
    response = wire_response()
    response["scope"] = None
    response["frozen_identity"].update(anchor_sha256=None, baseline_sha256=None, axis_sha256=None)
    for row in [response["baseline"], *response["candidates"]]:
        row.update(status="unavailable", anchor_value=None, candidate_value=None, delta_from_anchor=None,
                   delta_from_baseline=None, quantification=None, gaps=[{"code": "axis_record_missing"}])
    result = evaluate_battle_axis(client(response), wire_request())
    assert result["scope"] is None and result["baseline"]["candidate_value"] is None


def test_capability_and_malformed_input_stop_before_dispatch():
    transport = client(wire_response(), available=False)
    with pytest.raises(NativeAnalysisError, match="轴批量"):
        evaluate_battle_axis(transport, wire_request())
    transport._run.assert_not_called()
    request = wire_request()
    request["battle_request"]["marginal_candidate"] = {"change": "not-selector"}
    transport = client(wire_response())
    with pytest.raises(NativeAnalysisError):
        evaluate_battle_axis(transport, request)
    transport._run.assert_not_called()
    request = wire_request()
    request["candidates"][0]["equipment"][0]["uid_serial"] = 0
    with pytest.raises(NativeAnalysisError):
        evaluate_battle_axis(transport, request)
    transport._run.assert_not_called()


def test_negative_candidate_delta_is_an_evaluation_fact_not_an_improvement():
    response = wire_response()
    row = response["candidates"][0]
    row.update(candidate_value=105.0, delta_from_anchor=5.0, delta_from_baseline=-5.0)
    row["quantification"]["quantified_increment"] = 5.0
    result = evaluate_battle_axis(client(response), wire_request())
    assert result["candidates"][0]["delta_from_baseline"] == -5.0


@pytest.mark.parametrize("quality", ["orange", "Gold", "purple", "Purple", "blue", "Blue"])
def test_actual_native_quality_and_module_without_suit_are_not_replaced_with_fixture_metadata(quality):
    request = wire_request()
    for build in [*request["baseline_equipment_by_character"], *request["candidates"]]:
        for item in build["equipment"]:
            item["quality"] = quality
            if item["kind"] == "module":
                item["suit_id"] = None
    transport = client(wire_response(request))
    result = evaluate_battle_axis(transport, request)
    assert result["baseline"]["status"] == "complete"
    emitted = json.loads(transport._run.call_args.args[0])["request"]["baseline_equipment_by_character"][0]["equipment"]
    assert emitted[0]["quality"] == quality and emitted[1]["suit_id"] is None


def test_unknown_quality_and_wrong_explicit_range_are_rejected():
    request = wire_request()
    request["candidates"][0]["equipment"][0]["quality"] = "unknown"
    transport = client(wire_response())
    with pytest.raises(NativeAnalysisError):
        evaluate_battle_axis(transport, request)
    transport._run.assert_not_called()
    request = wire_request()
    request["battle_request"].update(start_us=500_000, end_us=1_000_000)
    with pytest.raises(NativeAnalysisError):
        evaluate_battle_axis(client(wire_response(request)), request)
