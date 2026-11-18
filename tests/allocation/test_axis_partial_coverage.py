# 验证部分轴伤推荐的冻结计分域、伤害覆盖率守恒、保存元数据和玩家可见口径。
from copy import deepcopy

import pytest

from src.features.allocation.results.damage_result_summary import damage_optimization_presentation
from src.integrations.allocation.allocation_group_postprocess import postprocess_allocation_groups
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.services.allocation.direct.direct_postprocess import optimization_payload
from tests.allocation.test_group_postprocess_wire import client, group_request, group_response

NTE_TEST_TIER = "core"


def partial_response():
    response = group_response(axis=True)
    coverage = {"policy": "available_fixed_domain_v1", "domain_sha256": "a" * 64,
        "basis_damage": 100.0, "covered_damage": 60.0, "quantified_damage": 40.0,
        "estimated_damage": 20.0, "excluded_damage": 40.0, "proven_unchanged_damage": 10.0,
        "coverage_ratio": .6, "quantified_ratio": .4}
    for key, value in (("baseline", 70.0), ("selected_result", 80.0)):
        row = response[key]
        row.update(status="partial", candidate_value=value, delta_from_anchor=value - 60,
                   delta_from_baseline=value - 70, coverage=deepcopy(coverage))
        row["quantification"].update(status="partial", fully_quantified_damage=30.0, proven_unchanged_damage=10.0,
            partially_quantified_damage=20.0, unavailable_damage=40.0, quantified_increment=value - 60)
        row["gaps"] = [{"code": "fixture_missing", "dimension_id": "fixture", "dependency_scope": "mechanic_specific", "property_ids": [], "explanation": "未计入"}]
        row["quantification"]["gaps"] = deepcopy(row["gaps"])
    response["optimization"].update(baseline_value=70.0, candidate_value=80.0, delta=10.0)
    return response


def test_partial_axis_recommendation_preserves_coverage_and_distinct_gain():
    response = partial_response()
    result = postprocess_allocation_groups(client(response), group_request(axis=True), axis=True)
    meta = {**result["optimization"], "status": "completed", "coverage": result["selected_result"]["coverage"]}
    saved = optimization_payload({"damage_optimization": meta})["damage_optimization"]
    assert saved["coverage"] == meta["coverage"]
    view = damage_optimization_presentation(saved)
    assert "参与计分部分轴伤" in view.headline and "+14.29%" in view.headline
    assert "计分 60.0%" in view.headline and "量化 40.0%＋估算 20.0%" in view.headline
    assert "计分覆盖 60.0%" in view.note and "估算 20.0%" in view.note and "未计入 40.0%" in view.note
    assert "量化包括可用现有模型重算的伤害" in view.details and "估算指根据战报证据" in view.details
    assert "估算指根据战报证据" not in view.note
    assert "不是命中次数或预测准确率" in view.details


@pytest.mark.parametrize("change", ["ratio", "bucket", "domain", "full", "nonfinite"])
def test_partial_axis_rejects_forged_or_incomparable_coverage(change):
    response = partial_response()
    row = response["selected_result"]
    if change == "ratio":
        row["coverage"]["coverage_ratio"] = .9
    elif change == "bucket":
        row["coverage"]["estimated_damage"] = 30.0
    elif change == "domain":
        row["coverage"]["domain_sha256"] = "b" * 64
    elif change == "full":
        row["status"] = "complete"
    else:
        row["candidate_value"] = float("inf")
    with pytest.raises(NativeAnalysisError):
        postprocess_allocation_groups(client(response), group_request(axis=True), axis=True)


def test_estimated_only_objective_is_usable_without_forging_original_quantification():
    response = partial_response()
    for key in ("baseline", "selected_result"):
        row = response[key]
        row["coverage"].update(quantified_damage=0.0, estimated_damage=60.0,
                               proven_unchanged_damage=0.0, quantified_ratio=0.0)
        row["quantification"].update(status="unavailable", fully_quantified_damage=0.0,
            partially_quantified_damage=0.0, unavailable_damage=100.0, proven_unchanged_damage=0.0,
            quantified_increment=None)
    result = postprocess_allocation_groups(client(response), group_request(axis=True), axis=True)
    assert result["selected_result"]["candidate_value"] == 80.0
    assert result["selected_result"]["quantification"]["status"] == "unavailable"
    assert result["selected_result"]["coverage"]["quantified_ratio"] == 0.0


def test_malformed_saved_coverage_does_not_turn_into_full_team_gain():
    response = partial_response()
    meta = {**response["optimization"], "status": "completed", "coverage": response["selected_result"]["coverage"]}
    meta["coverage"]["estimated_damage"] = 200.0
    view = damage_optimization_presentation(meta)
    assert "覆盖率待核对" in view.headline
    assert "全队轴伤估算" not in view.headline
