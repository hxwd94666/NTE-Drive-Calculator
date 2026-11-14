# 验证内核固定轴比例的策略/能力、单位、零未知及保存展示闭环；旧历史不由Python补算比例。
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.features.teams.graduation.axis_graduation_presentation import axis_graduation_display
from src.features.teams.graduation.axis_graduation_controller import TeamAxisGraduationController
from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError, NteAnalysisCoreClient
from src.integrations.battle_analysis.graduation.team_axis_graduation import FIXED_AXIS_RATIO_POLICY, validate_request, validate_response
from src.integrations.battle_analysis.graduation.team_axis_graduation_archive import archive_axis_result, validate_saved_axis_result
from src.integrations.battle_analysis.graduation.team_axis_graduation_ratio import validate_fixed_axis_ratio
from tests.teams.graduation.test_team_axis_graduation_history_ui import case
from tests.teams.graduation.test_team_axis_graduation_history_ui import cleanup
from tests.teams.graduation.test_team_axis_graduation_history_ui import wait_for
from tests.teams.graduation.test_team_axis_graduation_service import solve
from tests.teams.graduation.test_team_axis_graduation_wire import Client
from tests.teams.graduation.test_team_axis_graduation_wire import response_fixture
from tests.teams.graduation.test_team_axis_graduation_wire import wire_fixture

NTE_TEST_TIER = "core"


@pytest.fixture(scope="module")
def exported():
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_FIXED_RATIO_RESULT")
    if not path:
        pytest.skip("explicit synthetic native fixed ratio fixture required")
    value = json.loads(Path(path).read_text("utf8"))
    assert value["fixture_kind"] == "synthetic_fixed_axis_ratio_v1"
    return value["cases"]


def validate(case, response=None):
    raw = case["request_bytes"].encode("utf8")
    root = json.loads(raw)
    return validate_response(response if response is not None else case["response"], root["request"],
        dataset_version=root["dataset_version"], engine_version="0.3.8", request_sha256=hashlib.sha256(raw).hexdigest())


def test_actual_native_benchmark_and_current_update_have_ratio_and_immutable_denominator(exported):
    first, second = (validate(c) for c in exported)
    assert first["fixed_axis_graduation"]["ratio_percent"] != second["fixed_axis_graduation"]["ratio_percent"]
    assert first["benchmark_reference"] == second["benchmark_reference"] and first["axis_snapshot"] == second["axis_snapshot"]
    assert second["reference_verification"]["search_evaluations"] == 0
    for case in exported:
        record = archive_axis_result(json.loads(case["request_bytes"])["request"], case["response"])
        assert validate_saved_axis_result(record) == record
        assert record.to_payload()["response"]["fixed_axis_graduation"]["axis_feasibility"] == "unverified"


@pytest.mark.parametrize("field,value", [
    ("policy_id", "other"), ("assumption", "actual_feasible"), ("axis_feasibility", "verified"),
    ("ratio_percent", True), ("ratio_percent", 999), ("ratio_percent", None), ("damage_gap", 123),
    ("relative_gain_to_reference", .01), ("above_reference", 0), ("benchmark_id", "other"),
    ("current_input_sha256", "0"*64), ("scoring_domain_sha256", "0"*64), ("status", "globally_optimal"),
])
def test_wrong_assumption_context_units_or_fake_executable_percentage_rejected(exported, field, value):
    response = deepcopy(exported[0]["response"])
    response["fixed_axis_graduation"][field] = value
    with pytest.raises(NativeAnalysisError):
        validate(exported[0], response)


@pytest.mark.parametrize("value", [None, True, "other"])
def test_ratio_policy_never_silently_downgrades_invalid_opt_in(value):
    request = wire_fixture()
    request["fixed_axis_ratio_policy"] = value
    with pytest.raises(NativeAnalysisError):
        validate_request(request, "fixture")


def test_percentage_zero_above_reference_and_unknown_are_different_without_clamping(exported):
    response = deepcopy(exported[0]["response"])
    ratio = response["fixed_axis_graduation"]
    reference = response["benchmark_reference"]["payload"]["reference_damage"]
    response["current"]["axis_evaluation"]["damage"] = 0
    ratio.update(ratio_percent=0, damage_gap=reference, relative_gain_to_reference=None, above_reference=False)
    assert validate_fixed_axis_ratio(response)["ratio_percent"] == 0
    response["current"]["axis_evaluation"]["damage"] = reference*2
    ratio.update(ratio_percent=200, damage_gap=-reference, relative_gain_to_reference=-.5, above_reference=True)
    assert validate_fixed_axis_ratio(response)["ratio_percent"] == 200
    response["current"]["axis_evaluation"]["damage"] = None
    ratio.update(status="current_unquantified", ratio_percent=None, damage_gap=None, relative_gain_to_reference=None, above_reference=None)
    assert validate_fixed_axis_ratio(response)["ratio_percent"] is None


def test_capability_is_opt_in_and_old_saved_result_stays_readable(exported, tmp_path):
    exe = tmp_path / "nte-analysis-core.exe"
    exe.write_bytes(b"not executed")
    for caps, enabled in [(None, False), (["team_axis_graduation_current_v2"], False),
                          (["team_axis_graduation_fixed_axis_ratio_v1"], True)]:
        assert NteAnalysisCoreClient(exe, "fixture", capabilities=caps).supports_team_axis_graduation_fixed_axis_ratio is enabled
    request = wire_fixture()
    old = response_fixture(request, "6"*64)
    display = axis_graduation_display(old, request["target"])
    assert "%" not in display.headline and "旧结果" in display.evidence
    root = json.loads(exported[0]["request_bytes"])
    class RatioClient(Client):
        dataset_version = root["dataset_version"]
        supports_team_axis_graduation_fixed_axis_ratio = True
        def _run(self, payload, *, checkpoint):
            self.calls += 1
            out = deepcopy(exported[0]["response"])
            out["request_sha256"] = hashlib.sha256(payload).hexdigest()
            return json.dumps(out).encode("utf8")
    client = RatioClient()
    assert client.evaluate_team_axis_graduation(root["request"])["fixed_axis_graduation"]["ratio_percent"] > 0
    client.supports_team_axis_graduation_fixed_axis_ratio = False
    with pytest.raises(NativeAnalysisError):
        client.evaluate_team_axis_graduation(root["request"])
    assert client.calls == 1


def test_new_ratio_saves_reopens_and_displays_without_schema_migration_or_reanalysis(tmp_path):
    service, inputs, client, values, team = case(tmp_path, stored=False)
    client.supports_team_axis_graduation_fixed_axis_ratio = True
    def attach(response):
        assert response["current"]["axis_evaluation"]["damage"] == 140
        assert response["benchmark_reference"]["payload"]["reference_damage"] == 200
        response["fixed_axis_graduation"] = {"policy_id": FIXED_AXIS_RATIO_POLICY,
            "assumption": "fixed_observed_actions_and_timing_v1", "axis_feasibility": "unverified", "status": "comparable",
            "ratio_percent": 70, "damage_gap": 60, "relative_gain_to_reference": 3/7, "above_reference": False,
            "benchmark_id": response["benchmark_reference"]["benchmark_id"],
            "current_input_sha256": response["current"]["input_sha256"],
            "scoring_domain_sha256": response["current"]["axis_evaluation"]["scoring_domain_sha256"]}
    client.on_evaluate = attach
    preview = solve(service, inputs, values)
    assert preview.request.payload["fixed_axis_ratio_policy"] == FIXED_AXIS_RATIO_POLICY
    service.save_completed_result(preview, expected_target_revision=0, expected_selection_revision=0)
    saved = service.selected_result(team)[1]
    assert saved.to_payload()["response"]["fixed_axis_graduation"]["ratio_percent"] == 70
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    dialog.open()
    try:
        wait_for(lambda: not owner.is_running())
        assert "固定观测轴毕业率：70.00%" in dialog.headline.text()
        assert "可执行性未证实" in dialog.evidence.toPlainText()
        assert len(client.operations) == 1
    finally:
        cleanup(owner, dialog)
