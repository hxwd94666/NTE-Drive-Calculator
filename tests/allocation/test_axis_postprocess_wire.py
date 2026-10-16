# 验证轴后处理沿用冻结批量事实门禁，并独立核对搜索收益、预算、覆盖与模型缺口。
from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from tests.battle_report.analysis.axis_evaluation_fixture import wire_request, wire_response
from src.integrations.axis_allocation_postprocess import postprocess_battle_axis
from src.integrations.nte_analysis_core import NativeAnalysisError


NTE_TEST_TIER = "core"


def request_fixture():
    request = wire_request()
    del request["candidates"]
    inventory = {f"nte-{row['kind']}-{row['uid_slot']}-{row['uid_serial']}": row
                 for row in request["baseline_equipment_by_character"][0]["equipment"]}
    request.update(objective="frozen_axis_expected_v1", selected_role="A", role_character_ids={"A": 1072},
        allocation_request={"version": 2, "roles": [{"name": "A"}], "inventory": [{"uid": uid} for uid in inventory]},
        baseline_plans={"A": {"valid": True, "score": 1.0}}, reserved_uids=[], inventory_equipment_by_uid=inventory,
        budget={"max_evaluations": 20, "max_rounds": 10})
    request["team_preflight"] = dict(source_input_digest="a" * 64, fixed_build_sha256="b" * 64,
        inventory_identities=[{key: row[key] for key in ("kind", "item_id", "uid_slot", "uid_serial")} for row in inventory.values()])
    return request


def response_fixture(request=None):
    request = request or request_fixture()
    response = wire_response(request)
    selected = response.pop("candidates")[0]
    selected["candidate_id"] = "selected"
    response.update(batch_kind="battle_axis_postprocess_v1", selected_result=selected,
        plans=deepcopy(request["baseline_plans"]), ordering_policy="previous_round_exact_delta_then_uid",
        neighborhood="single_item_fixed_signature", optimization={
            "objective": "frozen_axis_expected_v1", "model_id": "frozen_axis_expected_v1", "selected_role": "A",
            "baseline_value": 110.0, "candidate_value": 125.0, "delta": 15.0, "evaluations": 3, "rounds": 2,
            "improvements": 1, "termination": "local_optimum", "reason": "", "enumeration_complete": True,
            "quantification_complete": True, "neighborhood_complete": True, "unquantified_candidates": 0, "gaps": {}})
    return response


def transport(response):
    return Mock(dataset_version="fixture-static", engine_version="0.3.0", supports_battle_axis_postprocess=True,
                _run=Mock(return_value=json.dumps(response).encode("utf8")))


def test_axis_search_uses_one_child_and_reuses_common_anchor_acceptance():
    request, response = request_fixture(), response_fixture()
    client = transport(response)
    result = postprocess_battle_axis(client, request)
    assert result["selected_result"]["candidate_value"] == 125.0
    assert result["optimization"]["delta"] == 15.0
    assert client._run.call_count == 1
    assert json.loads(client._run.call_args.args[0])["batch_kind"] == "battle_axis_postprocess_v1"


@pytest.mark.parametrize("field,value", [("delta", 99.0), ("evaluations", True), ("evaluations", 21),
    ("candidate_value", 124.0), ("neighborhood_complete", False), ("quantification_complete", False),
    ("unquantified_candidates", 1), ("gaps", {"unknown": 1})])
def test_forged_search_claims_are_rejected(field, value):
    response = response_fixture()
    response["optimization"][field] = value
    with pytest.raises(NativeAnalysisError):
        postprocess_battle_axis(transport(response), request_fixture())


def test_model_gap_keeps_complete_best_value_but_never_claims_neighborhood_complete():
    response = response_fixture()
    response["optimization"].update(termination="model_gap", enumeration_complete=False, quantification_complete=False,
                                     neighborhood_complete=False, unquantified_candidates=1, gaps={"axis_gap": 1})
    result = postprocess_battle_axis(transport(response), request_fixture())
    assert result["selected_result"]["status"] == "complete" and not result["optimization"]["neighborhood_complete"]


def test_inventory_map_keys_bind_exact_frozen_physical_uid():
    request = request_fixture()
    request["inventory_equipment_by_uid"]["nte-core-4-100"]["uid_serial"] = 999
    client = transport(response_fixture())
    with pytest.raises(NativeAnalysisError):
        postprocess_battle_axis(client, request)
    client._run.assert_not_called()


@pytest.mark.parametrize("prepared", [True, False])
def test_not_ready_preserves_b0_without_inventing_selected_quantification(prepared):
    response = response_fixture()
    response["optimization"].update(termination="not_ready", reason="axis_crit_fixed_projection_mismatch",
        baseline_value=None, candidate_value=None, delta=None, evaluations=1 if prepared else 0, rounds=0,
        improvements=0, enumeration_complete=False, quantification_complete=False, neighborhood_complete=False)
    response["selected_result"] = None
    if not prepared:
        for key in ("source_input_digest", "frozen_identity", "scope", "baseline"):
            response[key] = None
    result = postprocess_battle_axis(transport(response), request_fixture())
    assert result["optimization"]["termination"] == "not_ready" and result["selected_result"] is None
    assert result["plans"] == request_fixture()["baseline_plans"]


def test_not_ready_cannot_hide_changed_plans_or_forged_source_identity():
    response = response_fixture()
    response["source_input_digest"] = "not-a-digest"
    with pytest.raises(NativeAnalysisError):
        postprocess_battle_axis(transport(response), request_fixture())
