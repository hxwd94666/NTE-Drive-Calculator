# 验证当前更新固定旧分母、不含搜索预算，组件/范围/目标及跨语言重放证据不匹配时停止。
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.domain.teams.graduation.axis_graduation_snapshot import freeze_axis_context_snapshot
from src.domain.teams.graduation.axis_graduation_reference import freeze_benchmark_reference, seal_benchmark_reference
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.integrations.battle_analysis.graduation.team_axis_graduation import validate_response
from src.services.teams.axis_graduation.requests import build_current_request
from tests.teams.graduation.test_team_axis_graduation_requests import prepared
from tests.teams.graduation.test_team_axis_graduation_wire import Client
from tests.teams.graduation.test_team_axis_graduation_wire import wire_fixture
from tests.teams.graduation.test_team_axis_graduation_wire import response_fixture


NTE_TEST_TIER = "core"


def current_fixture():
    benchmark = wire_fixture()
    result = response_fixture(benchmark, "6"*64)
    request = deepcopy(benchmark)
    request.pop("budget")
    request.pop("seeds")
    request.update(operation="current_evaluation", request_id="synthetic-current")
    request["reference"] = result["benchmark_reference"]
    request["axis_snapshot"] = result["axis_snapshot"]
    return request, result


def current_response(request, old_result, request_hash):
    result = {key: deepcopy(value) for key, value in old_result.items()
              if key not in {"theory", "reference_verification_replays"}}
    result.update(operation=request["operation"], request_id=request["request_id"], request_sha256=request_hash,
                  source_input_digest=request["source_input_digest"], benchmark_reference=request["reference"],
                  axis_snapshot=deepcopy(request["axis_snapshot"]))
    result["current"]["source_sha256"] = request["current"]["source_sha256"]
    result["current"]["target_qualification"]["current_source_sha256"] = request["current"]["source_sha256"]
    p = request["reference"]["payload"]
    result["reference_verification"] = {"status": "replayed_unchanged", "reference_damage": p["reference_damage"],
        "scoring_domain_sha256": p["scoring_domain_sha256"], "replay_input_sha256": p["replay_input_sha256"],
        "search_evaluations": 0, "reference_replays": 1}
    return result


class CurrentClient(Client):
    supports_team_axis_graduation_benchmark = False

    def _run(self, payload, *, checkpoint):
        self.calls += 1
        request = json.loads(payload)["request"]
        assert "seeds" not in request and "budget" not in request
        _, old = current_fixture()
        result = current_response(request, old, hashlib.sha256(payload).hexdigest())
        if self.mutate:
            self.mutate(result)
        return json.dumps(result).encode("utf8")


def test_current_only_capability_reuses_immutable_reference_without_search_fields():
    request, old = current_fixture()
    result = CurrentClient().evaluate_team_axis_graduation(request)
    assert result["benchmark_reference"] == old["benchmark_reference"]
    assert result["reference_verification"]["search_evaluations"] == 0
    assert result["reference_verification"]["reference_replays"] == 1
    assert result["current_ratio_percent"] is None and "theory" not in result


@pytest.mark.parametrize("mutate", [
    lambda r: r["reference_verification"].update(reference_damage=999),
    lambda r: r["reference_verification"].update(replay_input_sha256="0"*64),
    lambda r: r["reference_verification"].update(search_evaluations=1),
    lambda r: r["reference_verification"].update(reference_replays=True),
    lambda r: r["benchmark_reference"]["payload"].update(reference_damage=201),
    lambda r: r.update(current_ratio_percent=70),
])
def test_current_response_must_not_rebase_or_claim_search_or_unverified_ratio(mutate):
    request, _ = current_fixture()
    with pytest.raises(NativeAnalysisError):
        CurrentClient(mutate).evaluate_team_axis_graduation(request)


def test_changed_component_reference_or_scope_is_rejected_before_dispatch():
    request, _ = current_fixture()
    for mode in ("component", "scope", "search"):
        value = deepcopy(request)
        if mode == "component":
            value["component_provenance"]["component_executable_sha256"] = "0"*64
        elif mode == "scope":
            value["battle_request"]["end_us"] = 100
        else:
            value["budget"] = {"max_evaluations": 1, "max_millis": 1000}
        client = CurrentClient()
        with pytest.raises(NativeAnalysisError):
            client.evaluate_team_axis_graduation(value)
        assert client.calls == 0


def test_request_factory_freezes_old_reference_without_restarting_theory():
    _, args = prepared()
    baseline = response_fixture(wire_fixture(), "6"*64)
    reference = freeze_benchmark_reference(baseline["benchmark_reference"])
    values = {key: args[key] for key in ("frozen", "target", "rules", "battle_selector", "source_input_digest", "request_id", "component_provenance")}
    request = build_current_request(reference=reference, axis_snapshot=freeze_axis_context_snapshot(baseline["axis_snapshot"]), **values)
    assert request["reference"] == reference.to_payload()
    assert "budget" not in request and "seeds" not in request
    assert request["current"]["source_sha256"] == args["frozen"].sha256
    with pytest.raises(ValueError):
        build_current_request(reference=reference.to_payload(), axis_snapshot=freeze_axis_context_snapshot(baseline["axis_snapshot"]), **values)


def test_rehashed_fake_denominator_is_not_accepted_as_an_unchanged_current_response():
    request, _ = current_fixture()
    def change(result):
        payload = deepcopy(result["benchmark_reference"]["payload"])
        payload["reference_damage"] = 999
        result["benchmark_reference"] = seal_benchmark_reference(payload).to_payload()
    with pytest.raises(NativeAnalysisError):
        CurrentClient(change).evaluate_team_axis_graduation(request)


def test_current_unknown_stays_unknown_while_the_reference_is_retained():
    request, _ = current_fixture()
    def unknown(result):
        result["current"]["axis_evaluation"].update(status="unavailable", damage=None, global_gap_codes=["model_missing"])
    result = CurrentClient(unknown).evaluate_team_axis_graduation(request)
    assert result["current"]["axis_evaluation"]["damage"] is None
    assert result["benchmark_reference"] == request["reference"]


def test_controlled_rust_current_response_passes_calc_identity_and_reference_checks():
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_CURRENT_WIRE_RESULT")
    if not path:
        pytest.skip("explicit synthetic Rust current-response fixture required")
    value = json.loads(Path(path).read_text("utf8"))
    assert value["fixture_kind"] == "synthetic_graduation_current_wire_v1"
    raw = value["request_bytes"].encode("utf8")
    envelope = json.loads(raw)
    result = validate_response(value["response"], envelope["request"], dataset_version=envelope["dataset_version"],
                               engine_version="0.3.8", request_sha256=hashlib.sha256(raw).hexdigest())
    assert result["benchmark_reference"] == value["benchmark_response"]["benchmark_reference"]
    assert result["reference_verification"]["search_evaluations"] == 0 and result["current_ratio_percent"] is None

def test_same_account_new_generation_keeps_reference_but_still_binds_current_selector():
    from dataclasses import replace
    frozen, args = prepared()
    baseline = response_fixture(wire_fixture(), "6"*64)
    reference = freeze_benchmark_reference(baseline["benchmark_reference"])
    values = {key: args[key] for key in ("target", "rules", "battle_selector", "source_input_digest", "request_id", "component_provenance")}
    values["battle_selector"] = {**values["battle_selector"], "generation": 7}
    request = build_current_request(frozen=replace(frozen, generation=7), reference=reference, axis_snapshot=freeze_axis_context_snapshot(baseline["axis_snapshot"]), **values)
    assert request["current"]["generation"] == 7 and request["reference"] == reference.to_payload()
    assert "generation" not in request["reference"]["payload"]
    values["battle_selector"]["generation"] = 8
    with pytest.raises(NativeAnalysisError):
        build_current_request(frozen=replace(frozen, generation=7), reference=reference, axis_snapshot=freeze_axis_context_snapshot(baseline["axis_snapshot"]), **values)