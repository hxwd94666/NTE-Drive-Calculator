# 用Rust受控真实响应验证建议身份、完整变化、收益和缺口；不重跑未受影响的理论搜索专项。
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.domain.teams.graduation.axis_graduation_snapshot import freeze_axis_context_snapshot
from src.domain.teams.graduation.axis_graduation_reference import freeze_benchmark_reference
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError, NteAnalysisCoreClient
from src.integrations.battle_analysis.graduation.team_axis_graduation import validate_request, validate_response
from src.services.teams.axis_graduation.requests import build_advice_request
from tests.teams.graduation.test_team_axis_graduation_requests import prepared
from tests.teams.graduation.test_team_axis_graduation_wire import Client
from tests.teams.graduation.test_team_axis_graduation_wire import response_fixture
from tests.teams.graduation.test_team_axis_graduation_wire import wire_fixture


NTE_TEST_TIER = "core"


@pytest.fixture(scope="module")
def exported():
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_ADVICE_WIRE_RESULT")
    if not path:
        pytest.skip("explicit synthetic Rust advice-response fixture required")
    value = json.loads(Path(path).read_text("utf8"))
    assert value["fixture_kind"] == "synthetic_graduation_advice_wire_v1"
    return value


def validate(exported, result=None):
    raw = exported["request_bytes"].encode("utf8")
    envelope = json.loads(raw)
    return validate_response(result if result is not None else deepcopy(exported["response"]), envelope["request"],
        dataset_version=envelope["dataset_version"], engine_version="0.3.8", request_sha256=hashlib.sha256(raw).hexdigest())


def test_actual_rust_advice_has_positive_team_gain_and_unchanged_reference_without_search(exported):
    result = validate(exported)
    assert result["recommendations"]["evaluated_candidates"] == 5
    assert result["recommendations"]["items"][0]["impact"]["damage_delta"] > 0
    assert result["reference_verification"]["search_evaluations"] == 0
    assert result["current_ratio_percent"] is None
    assert all(i["impact"]["graduation_percentage_points"] is None for i in result["recommendations"]["items"])


@pytest.mark.parametrize("mutate", [
    lambda a: a.update(scope="complete_gear_advice"),
    lambda a: a.update(preset_id="missing"),
    lambda a: a.update(candidate_limit=True),
    lambda a: a.update(omitted_candidates=1),
    lambda a: a.update(stop_reason="globally_optimal"),
    lambda a: a["items"][0].update(kind="equip_real_inventory"),
    lambda a: a["items"][0]["impact"].update(damage_delta=999999),
    lambda a: a["items"][0]["impact"].update(relative_gain=True),
    lambda a: a["items"][0]["impact"].update(graduation_percentage_points=10),
    lambda a: a["items"][0]["changes"][0].update(character_id=99999),
    lambda a: a["items"][0]["changes"][0].update(field="equipment"),
    lambda a: a["items"][0]["changes"][0].update(value=True),
    lambda a: a["items"][0]["changes"].append(deepcopy(a["items"][0]["changes"][0])),
    lambda a: a["items"][0]["evaluation"].update(source_sha256="0"*64),
    lambda a: a["items"][0]["evaluation"]["axis_evaluation"].update(scoring_domain_sha256="0"*64),
    lambda a: a["items"].reverse(),
])
def test_wrong_advice_domain_counts_fields_changes_or_gain_are_rejected(exported, mutate):
    result = deepcopy(exported["response"])
    mutate(result["recommendations"])
    with pytest.raises((NativeAnalysisError, ValueError)):
        validate(exported, result)


def test_unknown_candidate_remains_unknown_and_sorted_after_known_math(exported):
    result = deepcopy(exported["response"])
    items = result["recommendations"]["items"]
    item = items.pop(0)
    item.update(status="unquantified", impact=None)
    item["evaluation"]["axis_evaluation"].update(status="unavailable", damage=None, global_gap_codes=["model_missing"])
    items.append(item)
    assert validate(exported, result)["recommendations"]["items"][-1]["impact"] is None


def test_candidate_preparation_failure_does_not_pretend_no_improvement(exported):
    result = deepcopy(exported["response"])
    item = result["recommendations"]["items"].pop(0)
    item.update(status="preparation_unavailable", evaluation=None, impact=None, error_code="graduation_target_skill_level_invalid")
    result["recommendations"]["items"].append(item)
    result["recommendations"]["evaluated_candidates"] -= 1
    result["recommendations"]["preparation_unavailable_candidates"] += 1
    assert validate(exported, result)["recommendations"]["items"][-1]["status"] == "preparation_unavailable"


def test_advice_request_factory_freezes_actual_input_and_has_no_search_budget():
    frozen, args = prepared()
    old = response_fixture(wire_fixture(), "6"*64)
    values = {k: args[k] for k in ("target", "rules", "battle_selector", "source_input_digest", "request_id", "component_provenance")}
    request = build_advice_request(frozen=frozen, reference=freeze_benchmark_reference(old["benchmark_reference"]), axis_snapshot=freeze_axis_context_snapshot(old["axis_snapshot"]), **values)
    assert request["operation"] == "recommendation_evaluation" and "seeds" not in request and "budget" not in request
    assert request["current"]["source_sha256"] == frozen.sha256
    request["budget"] = {"max_evaluations": 1, "max_millis": 1}
    with pytest.raises(NativeAnalysisError):
        validate_request(request, args["rules"].dataset_id)


def test_transport_requires_advice_capability_before_dispatch_and_validates_exact_bytes(exported):
    envelope = json.loads(exported["request_bytes"])
    class AdviceClient(Client):
        dataset_version = envelope["dataset_version"]
        supports_team_axis_graduation_advice = True
        def _run(self, payload, *, checkpoint):
            self.calls += 1
            result = deepcopy(exported["response"])
            result["request_sha256"] = hashlib.sha256(payload).hexdigest()
            return json.dumps(result).encode("utf8")
    client = AdviceClient()
    result = client.evaluate_team_axis_graduation(envelope["request"])
    assert client.calls == 1 and result["recommendations"]["items"]
    client.supports_team_axis_graduation_advice = False
    with pytest.raises(NativeAnalysisError):
        client.evaluate_team_axis_graduation(envelope["request"])
    assert client.calls == 1


def test_real_client_advice_capability_is_separate_from_benchmark_and_requires_known_engine(tmp_path):
    exe = tmp_path / "nte-analysis-core.exe"
    exe.write_bytes(b"not executed")
    from src.integrations.analysis_core.nte_analysis_core import ENGINE_VERSION
    for version, capabilities, expected in [(ENGINE_VERSION, ["team_axis_graduation_cultivation_advice_v2"], True),
                                           (ENGINE_VERSION, ["team_axis_graduation_current_v2"], False),
                                           ("0.2.0", ["team_axis_graduation_cultivation_advice_v2"], False)]:
        client = NteAnalysisCoreClient(exe, dataset_version="fixture", engine_version=version, capabilities=capabilities)
        assert client.supports_team_axis_graduation_advice is expected

    with pytest.raises(ValueError):
        NteAnalysisCoreClient(exe, dataset_version="fixture", engine_version="unknown", capabilities=frozenset())
