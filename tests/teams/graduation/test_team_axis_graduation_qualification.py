# 比较条件证据只做跨语言契约校验；错误身份、伪兼容汇总和越界诊断不得被提升为正式百分比。
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.domain.teams.graduation.axis_graduation_target import decode_graduation_target
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.integrations.battle_analysis.graduation.team_axis_graduation import validate_response
from src.integrations.battle_analysis.graduation.team_axis_graduation_qualification import validate_target_qualification
from tests.teams.graduation.test_team_axis_graduation_wire import Client
from tests.teams.graduation.test_team_axis_graduation_wire import response_fixture
from tests.teams.graduation.test_team_axis_graduation_wire import wire_fixture


NTE_TEST_TIER = "core"


def fixture():
    request = wire_fixture()
    target = decode_graduation_target(json.dumps(request["target"]))
    evidence = deepcopy(response_fixture(request, "6"*64)["current"]["target_qualification"])
    return request, target, evidence


def outside(evidence):
    evidence.update(status="outside_target_conditions", compatible_preset_ids=[])
    for row in evidence["presets"]:
        row.update(compatible=False, issues=[{"character_id": 1, "field": "awakening_level", "entry_id": None, "code": "exceeds_target"}])


def test_comparison_conditions_are_bound_but_do_not_prove_exact_domain_or_axis_execution():
    request, target, evidence = fixture()
    assert validate_target_qualification(evidence, request, target) == "axis_feasibility_unverified"
    request["current"]["build"]["characters"][0]["profile"]["awakening_level"] = 6
    outside(evidence)
    assert validate_target_qualification(evidence, request, target) == "outside_target"
    assert evidence["exact_domain_membership"] == "not_evaluated"


@pytest.mark.parametrize("mutate", [
    lambda q: q.update(policy_id="other"),
    lambda q: q.update(scope="full_legal_domain"),
    lambda q: q.update(exact_domain_membership="proven"),
    lambda q: q.update(target_sha256="0"*64),
    lambda q: q.update(current_source_sha256="0"*64),
    lambda q: q.update(status="ready"),
    lambda q: q.update(status="outside_target_conditions"),
    lambda q: q.update(compatible_preset_ids=[]),
    lambda q: q["compatible_preset_ids"].reverse(),
    lambda q: q["presets"].pop(),
    lambda q: q["presets"].reverse(),
    lambda q: q["presets"][0].update(compatible=1),
    lambda q: q["presets"][0].update(compatible=False),
    lambda q: q["presets"][0].update(issues_omitted=True),
    lambda q: q["presets"][0].update(issues_omitted=1),
    lambda q: q.update(extra="ignored"),
])
def test_wrong_qualification_identity_shape_and_compatibility_are_rejected(mutate):
    request, target, evidence = fixture()
    mutate(evidence)
    with pytest.raises(NativeAnalysisError):
        validate_target_qualification(evidence, request, target)


@pytest.mark.parametrize("issue", [
    {"character_id": 1, "field": "unknown", "entry_id": None, "code": "exceeds_target"},
    {"character_id": True, "field": "awakening_level", "entry_id": None, "code": "exceeds_target"},
    {"character_id": 99, "field": "awakening_level", "entry_id": None, "code": "exceeds_target"},
    {"character_id": 1, "field": "yaodao_attack_add", "entry_id": None, "code": "exceeds_target"},
    {"character_id": None, "field": "quantao_crit_damage", "entry_id": None, "code": "not_in_target"},
    {"character_id": 1, "field": "skill_levels", "entry_id": "missing", "code": "not_in_target"},
    {"character_id": 1, "field": "selected_awaken_effect_ids", "entry_id": [], "code": "not_in_target"},
    {"character_id": 1, "field": "fork_id", "entry_id": None, "code": "exceeds_target"},
])
def test_issue_formal_identity_field_and_reason_are_rejected(issue):
    request, target, evidence = fixture()
    outside(evidence)
    evidence["presets"][0]["issues"] = [issue]
    with pytest.raises(NativeAnalysisError):
        validate_target_qualification(evidence, request, target)


def test_duplicate_issues_do_not_count_as_independent_evidence():
    request, target, evidence = fixture()
    outside(evidence)
    row = evidence["presets"][0]
    row["issues"].append(deepcopy(row["issues"][0]))
    with pytest.raises(NativeAnalysisError):
        validate_target_qualification(evidence, request, target)


def test_one_compatible_preset_does_not_require_the_best_reference_preset_to_match():
    request, target, evidence = fixture()
    evidence["presets"][0].update(compatible=False, issues=[{"character_id": None,
        "field": "yaodao_attack_add", "entry_id": None, "code": "exceeds_target"}])
    evidence["compatible_preset_ids"] = [evidence["presets"][1]["preset_id"]]
    assert validate_target_qualification(evidence, request, target) == "axis_feasibility_unverified"


def test_top_level_status_must_conserve_nested_qualification_and_never_publish_ratio():
    request = wire_fixture()
    def inconsistent(result):
        result["status"] = "outside_target"
    with pytest.raises(NativeAnalysisError):
        Client(inconsistent).evaluate_team_axis_graduation(request)
    def falsely_ready(result):
        result["current_ratio_percent"] = 70
    with pytest.raises(NativeAnalysisError):
        Client(falsely_ready).evaluate_team_axis_graduation(request)


def test_real_rust_outside_conditions_still_preserve_reference_and_empty_ratio():
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_CURRENT_WIRE_RESULT")
    if not path:
        pytest.skip("explicit synthetic Rust current-response fixture required")
    exported = json.loads(Path(path).read_text("utf8"))
    raw = exported["outside_request_bytes"].encode("utf8")
    envelope = json.loads(raw)
    result = validate_response(exported["outside_response"], envelope["request"],
        dataset_version=envelope["dataset_version"], engine_version="0.3.8", request_sha256=hashlib.sha256(raw).hexdigest())
    assert result["status"] == "outside_target" and result["current_ratio_percent"] is None
    assert result["current"]["target_qualification"]["compatible_preset_ids"] == []
    assert result["benchmark_reference"] == exported["response"]["benchmark_reference"]
    assert result["reference_verification"]["search_evaluations"] == 0
